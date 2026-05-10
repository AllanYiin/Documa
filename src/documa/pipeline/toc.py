"""Table-of-contents linking stage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    RelationIR,
    RelationState,
    RelationType,
)
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.relations import append_relation_once, block_text, iter_blocks, normalized_key


def _toc_items_from_block(block: BlockIR) -> list[dict[str, Any]]:
    items = block.metadata.get("toc_items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    title = block.metadata.get("toc_title")
    if title:
        return [{"title": str(title), "page_number": block.metadata.get("target_page")}]
    return []


def _toc_items_from_document(document: DocumentIR) -> list[dict[str, Any]]:
    raw_items = document.metadata.get("toc_items") or document.metadata.get("toc")
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            title = raw.get("title")
            if title:
                items.append(
                    {
                        "title": str(title),
                        "page_number": raw.get("page_number") or raw.get("page"),
                        "source_id": raw.get("source_id"),
                    }
                )
        elif isinstance(raw, (list, tuple)) and len(raw) >= 3:
            items.append({"title": str(raw[1]), "page_number": raw[2]})
    return items


def _find_heading(headings: list[BlockIR], title: str, page_number: Any) -> tuple[BlockIR | None, Confidence, str]:
    title_key = normalized_key(title)
    if title_key:
        for heading in headings:
            if normalized_key(block_text(heading)) == title_key:
                return heading, Confidence.HIGH, "heading_title_exact_match"

    try:
        target_page = int(page_number)
    except (TypeError, ValueError):
        target_page = None
    if target_page is not None:
        for heading in headings:
            if heading.page_number == target_page:
                return heading, Confidence.LOW, "heading_page_match"

    return None, Confidence.LOW, "target_missing"


@dataclass(slots=True)
class TocLinkingStage(PipelineStage):
    """Link TOC items to heading blocks when enough evidence exists."""

    name: str = "toc_linking"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        headings = [block for block in iter_blocks(document) if block.type == BlockType.HEADING]
        entries: list[tuple[str, dict[str, Any]]] = []

        for block in iter_blocks(document):
            if block.type != BlockType.TOC and "toc_items" not in block.metadata and "toc_title" not in block.metadata:
                continue
            for item in _toc_items_from_block(block):
                entries.append((block.id, item))

        for index, item in enumerate(_toc_items_from_document(document), start=1):
            source_id = str(item.get("source_id") or f"{document.id}#toc:{index}")
            entries.append((source_id, item))

        created = 0
        unresolved = 0
        for index, (source_id, item) in enumerate(entries, start=1):
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            page_number = item.get("page_number")
            target, confidence, evidence = _find_heading(headings, title, page_number)
            relation_type = RelationType.TOC_ITEM_TO_HEADING if target else RelationType.UNRESOLVED
            relation = RelationIR(
                id=f"rel_toc_{index}",
                type=relation_type,
                from_id=source_id,
                to_id=target.id if target else None,
                confidence=confidence,
                state=RelationState.INFERRED if target else RelationState.UNRESOLVED,
                evidence=[evidence, f"title={title}"],
                metadata={
                    "toc_title": title,
                    "target_page": page_number,
                    "stage": self.name,
                },
            )
            if append_relation_once(document, relation):
                created += 1
                if target is None:
                    unresolved += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=created > 0,
            report={"relations_created": created, "unresolved": unresolved},
        )

