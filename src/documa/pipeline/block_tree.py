"""Build a progressive-disclosure document block tree."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from documa.core.text_normalization import clean_retrieval_text
from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    RelationIR,
    RelationType,
    repair_surrogate_text,
)
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata, printed_page_label_from_footer
from documa.pipeline.relations import append_relation_once, block_text


_FURNITURE_TYPES = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}
_SKIP_LEAF_TYPES = {BlockType.UNKNOWN, BlockType.TOC, *_FURNITURE_TYPES}


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    output = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(value)
    return output


def _preview(text: str, max_chars: int = 160) -> str | None:
    clean = " ".join(clean_retrieval_text(text).split())
    if not clean:
        return None
    return clean[:max_chars]


def _hash(text: str) -> str | None:
    if not text:
        return None
    return hashlib.sha256(repair_surrogate_text(text).encode("utf-8")).hexdigest()


def _block_type(block: BlockIR) -> DocumentBlockType:
    if block.type == BlockType.HEADING:
        return DocumentBlockType.SECTION
    if block.type == BlockType.PARAGRAPH or block.type == BlockType.TEXT:
        return DocumentBlockType.PARAGRAPH
    if block.type == BlockType.TABLE:
        return DocumentBlockType.TABLE
    if block.type == BlockType.IMAGE or block.type == BlockType.CHART:
        return DocumentBlockType.IMAGE
    if block.type == BlockType.FOOTNOTE:
        return DocumentBlockType.FOOTNOTE
    if block.type == BlockType.TOC:
        return DocumentBlockType.TOC
    if block.type in _FURNITURE_TYPES:
        return DocumentBlockType.METADATA
    return DocumentBlockType.UNKNOWN


def block_text_by_id(document: DocumentIR) -> dict[str, str]:
    return {block.id: block_text(block) for page in document.pages for block in page.blocks}


def document_block_text(document: DocumentIR, document_block: DocumentBlockIR) -> str:
    text_by_id = block_text_by_id(document)
    return "\n\n".join(
        text_by_id[source_id].strip()
        for source_id in document_block.source_block_ids
        if text_by_id.get(source_id, "").strip()
    )


@dataclass(slots=True)
class BlockTreeBuildingStage(PipelineStage):
    """Create document-level blocks without discarding page-local evidence."""

    name: str = "block_tree"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        settings = context.settings if context else {}
        force = bool(settings.get("force_rebuild_blocks", False))
        if document.document_blocks and not force:
            return StageResult(
                document=document,
                stage_name=self.name,
                changed=False,
                report={"blocks_created": 0, "skipped": "existing_document_blocks"},
            )
        if force:
            document.document_blocks.clear()

        blocks: list[DocumentBlockIR] = []
        root_id = f"db_{document.id}_root"
        root = DocumentBlockIR(
            id=root_id,
            type=DocumentBlockType.DOCUMENT,
            title=document.source_name,
            depth=0,
            order_index=1,
            confidence=Confidence.HIGH,
            metadata={"stage": self.name, "role": "body", "build_strategy": "document_root"},
        )
        blocks.append(root)

        section_stack: list[DocumentBlockIR] = [root]
        page_nodes: dict[tuple[str, int], DocumentBlockIR] = {}
        order = 1
        furniture: list[dict[str, Any]] = []
        page_citations = ensure_page_citation_map(document)

        def append_child(parent: DocumentBlockIR, child: DocumentBlockIR) -> None:
            child.parent_id = parent.id
            child.depth = parent.depth + 1
            parent.child_ids.append(child.id)
            blocks.append(child)

        def current_section() -> DocumentBlockIR:
            return section_stack[-1]

        def page_node(parent: DocumentBlockIR, page_number: int) -> DocumentBlockIR:
            key = (parent.id, page_number)
            if key in page_nodes:
                return page_nodes[key]
            nonlocal order
            order += 1
            node = DocumentBlockIR(
                id=f"db_{document.id}_p{page_number}_{len(page_nodes) + 1}",
                type=DocumentBlockType.PAGE,
                title=f"Page {page_number}",
                page_refs=[page_number],
                order_index=order,
                confidence=Confidence.HIGH,
                metadata={
                    "stage": self.name,
                    "role": "body",
                    "build_strategy": "page_boundary",
                    **page_citation_metadata([page_number], page_citations),
                },
            )
            append_child(parent, node)
            page_nodes[key] = node
            return node

        for page in sorted(document.pages, key=lambda item: item.page_number):
            page_blocks = sorted(page.blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
            for source in page_blocks:
                text = block_text(source).strip()
                if source.type in _FURNITURE_TYPES:
                    item = {
                        "source_block_id": source.id,
                        "type": source.type.value,
                        "page_number": source.page_number,
                        "text_preview": _preview(text),
                    }
                    if source.type == BlockType.PAGE_FOOTER:
                        item["printed_page_label"] = printed_page_label_from_footer(text)
                    furniture.append(item)
                    continue
                if source.type == BlockType.HEADING:
                    level = int(source.metadata.get("heading_level", 1) or 1)
                    level = max(1, min(level, 6))
                    while len(section_stack) > level:
                        section_stack.pop()
                    parent = section_stack[-1]
                    order += 1
                    section = DocumentBlockIR(
                        id=f"db_{document.id}_sec_{order:04d}",
                        type=DocumentBlockType.SECTION,
                        title=text or source.id,
                        source_block_ids=[source.id],
                        page_refs=[source.page_number],
                        bbox_refs=[source.bbox] if source.bbox else [],
                        text_preview=_preview(text),
                        content_hash=_hash(text),
                        order_index=order,
                        confidence=source.confidence if source.confidence != Confidence.UNKNOWN else Confidence.MEDIUM,
                        metadata={
                            "stage": self.name,
                            "role": "body",
                            "heading_level": level,
                            "build_strategy": "heading_block",
                            **page_citation_metadata([source.page_number], page_citations),
                            "source_range": {
                                "line_start": source.metadata.get("line_start"),
                                "line_end": source.metadata.get("line_end"),
                            }
                            if source.metadata.get("line_start") is not None
                            else None,
                        },
                    )
                    append_child(parent, section)
                    section_stack.append(section)
                    continue
                if source.type in _SKIP_LEAF_TYPES:
                    continue

                parent_page = page_node(current_section(), page.page_number)
                order += 1
                text = text if source.type != BlockType.TABLE else text
                title = (
                    source.metadata.get("paragraph_title")
                    or source.metadata.get("table_title")
                    or source.metadata.get("caption")
                )
                leaf = DocumentBlockIR(
                    id=f"db_{document.id}_{source.id}",
                    type=_block_type(source),
                    title=title,
                    source_block_ids=[source.id],
                    page_refs=[source.page_number],
                    bbox_refs=[source.bbox] if source.bbox else [],
                    text_preview=_preview(text),
                    content_hash=_hash(text),
                    order_index=order,
                    confidence=source.confidence if source.confidence != Confidence.UNKNOWN else Confidence.MEDIUM,
                    metadata={
                        "stage": self.name,
                        "role": "body",
                        "source_block_type": source.type.value,
                        "build_strategy": "page_local_block",
                        **page_citation_metadata([source.page_number], page_citations),
                        "source_range": {
                            "line_start": source.metadata.get("line_start"),
                            "line_end": source.metadata.get("line_end"),
                        }
                        if source.metadata.get("line_start") is not None
                        else None,
                    },
                )
                append_child(parent_page, leaf)

        root.page_refs = _unique([page.page_number for page in document.pages])
        root.metadata["furniture"] = furniture
        for node in blocks:
            if node.child_ids:
                children = [candidate for candidate in blocks if candidate.id in set(node.child_ids)]
                node.page_refs = _unique([page for child in children for page in child.page_refs] + node.page_refs)
                node.bbox_refs = _unique([bbox for child in children for bbox in child.bbox_refs] + node.bbox_refs)
                node.metadata.update(page_citation_metadata(node.page_refs, page_citations))

        document.document_blocks = blocks
        created_relations = 0
        for node in blocks:
            if node.parent_id:
                relation = RelationIR(
                    id=f"rel_block_parent_{node.id}",
                    type=RelationType.BLOCK_PARENT_CHILD,
                    from_id=node.parent_id,
                    to_id=node.id,
                    confidence=Confidence.HIGH,
                    evidence=["document_block_parent_id"],
                    metadata={"stage": self.name, "child_id": node.id},
                )
                if append_relation_once(document, relation):
                    created_relations += 1
            for source_id in node.source_block_ids:
                relation = RelationIR(
                    id=f"rel_block_source_{node.id}_{source_id}",
                    type=RelationType.BLOCK_TO_SOURCE,
                    from_id=node.id,
                    to_id=source_id,
                    confidence=Confidence.HIGH,
                    evidence=["document_block_source_block_ids"],
                    metadata={"stage": self.name, "source_block_id": source_id},
                )
                if append_relation_once(document, relation):
                    created_relations += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=bool(blocks),
            report={
                "blocks_created": len(blocks),
                "relations_created": created_relations,
                "furniture_blocks": len(furniture),
            },
        )
