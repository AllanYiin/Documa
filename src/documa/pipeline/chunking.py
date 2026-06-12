"""Optional intra-block retrieval views."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.text_normalization import clean_retrieval_text
from documa.core.image_filtering import is_decorative_image
from documa.core.ir import BlockIR, BlockType, ChunkIR, DocumentBlockIR, DocumentBlockType, DocumentIR, TextContent
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.block_tree import document_block_text
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata
from documa.pipeline.relations import block_text


_TEXT_BLOCKS = {BlockType.TEXT, BlockType.PARAGRAPH, BlockType.FOOTNOTE}


def _unique(values: list) -> list:
    seen = set()
    output = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(value)
    return output


def _table_markdown(document: DocumentIR, block: BlockIR) -> str | None:
    table_id = block.metadata.get("table_id")
    for table in document.tables:
        if table.block_id == block.id or table.id == table_id:
            return table.markdown
    return None


def _block_chunk_text(document: DocumentIR, block: BlockIR) -> str:
    if block.type == BlockType.TABLE:
        return clean_retrieval_text(_table_markdown(document, block) or block_text(block))
    return clean_retrieval_text(block_text(block))


def _block_by_id(document: DocumentIR) -> dict[str, BlockIR]:
    return {block.id: block for page in document.pages for block in page.blocks}


def _document_block_by_id(document: DocumentIR) -> dict[str, DocumentBlockIR]:
    return {block.id: block for block in document.document_blocks}


def _block_path(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> list[str]:
    path: list[str] = []
    current: DocumentBlockIR | None = block
    while current is not None:
        if current.type == DocumentBlockType.SECTION and current.title:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _source_table(document: DocumentIR, source_block_ids: list[str]):
    for table in document.tables:
        if table.block_id in source_block_ids:
            return table
    return None


def _table_context(document: DocumentIR, block: DocumentBlockIR, heading_path: list[str]) -> tuple[str, str]:
    table = _source_table(document, block.source_block_ids)
    source_blocks = _block_by_id(document)
    source = source_blocks.get(block.source_block_ids[0]) if block.source_block_ids else None
    title = block.title or (source.metadata.get("table_title") if source else None)
    caption = source.metadata.get("caption") if source else None
    notes = (source.metadata.get("table_notes") or source.metadata.get("unit")) if source else None
    table_text = clean_retrieval_text(table.markdown if table and table.markdown else document_block_text(document, block))
    context_lines = []
    if heading_path:
        context_lines.append("Section: " + " > ".join(heading_path))
    if title:
        context_lines.append("Table: " + str(title))
    if caption:
        context_lines.append("Caption: " + str(caption))
    if notes:
        context_lines.append("Notes: " + str(notes))
    return "\n".join(context_lines).strip(), table_text


def _split_text_within_block(text: str, max_chars: int) -> list[str]:
    text = clean_retrieval_text(text)
    if not text or len(text) <= max_chars:
        return [text] if text else []
    pieces = []
    active = ""
    for part in text.replace("\r\n", "\n").split("\n"):
        part = part.strip()
        if not part:
            continue
        candidate = f"{active}\n{part}".strip() if active else part
        if len(candidate) <= max_chars:
            active = candidate
            continue
        if active:
            pieces.append(active)
        while len(part) > max_chars:
            pieces.append(part[:max_chars])
            part = part[max_chars:]
        active = part
    if active:
        pieces.append(active)
    return pieces


@dataclass(slots=True)
class ChunkingStage(PipelineStage):
    """Create optional retrieval chunks that never cross document block boundaries."""

    name: str = "rag_chunking"
    default_max_chars: int = 1200

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        settings = context.settings if context else {}
        force = bool(settings.get("force_rechunk", False))
        max_chars = int(settings.get("max_chars", self.default_max_chars))
        if document.chunks and not force:
            return StageResult(
                document=document,
                stage_name=self.name,
                changed=False,
                report={"chunks_created": 0, "skipped": "existing_chunks"},
            )
        if force:
            document.chunks.clear()

        if document.document_blocks:
            return self._run_document_block_chunks(document, max_chars)

        page_citations = ensure_page_citation_map(document)
        chunks_created = 0
        heading_path: list[str] = []
        buffer_texts: list[str] = []
        buffer_blocks: list[BlockIR] = []

        def flush(kind: str = "text") -> None:
            nonlocal chunks_created
            text = "\n\n".join(item for item in buffer_texts if item.strip()).strip()
            if not text:
                buffer_texts.clear()
                buffer_blocks.clear()
                return
            chunks_created += 1
            page_refs = _unique([block.page_number for block in buffer_blocks])
            chunk = ChunkIR(
                id=f"chunk_{document.id}_{chunks_created:04d}",
                text=TextContent(text),
                source_block_ids=[block.id for block in buffer_blocks],
                parent_block_id=None,
                heading_path=list(heading_path),
                page_refs=page_refs,
                bbox_refs=_unique([block.bbox for block in buffer_blocks if block.bbox]),
                metadata={
                    "stage": self.name,
                    "chunk_kind": kind,
                    "block_types": _unique([block.type.value for block in buffer_blocks]),
                    **page_citation_metadata(page_refs, page_citations),
                },
            )
            document.chunks.append(chunk)
            buffer_texts.clear()
            buffer_blocks.clear()

        for page in sorted(document.pages, key=lambda item: item.page_number):
            blocks = sorted(page.blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
            for block in blocks:
                if block.type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER, BlockType.TOC, BlockType.UNKNOWN}:
                    continue

                text = _block_chunk_text(document, block).strip()
                if not text:
                    continue

                if block.type == BlockType.HEADING:
                    flush()
                    level = int(block.metadata.get("heading_level", 1) or 1)
                    level = max(1, min(level, 6))
                    heading_path = heading_path[: level - 1] + [text]
                    buffer_texts.append(text)
                    buffer_blocks.append(block)
                    flush(kind="heading")
                    continue

                if block.type == BlockType.TABLE:
                    flush()
                    buffer_texts.append(text)
                    buffer_blocks.append(block)
                    flush(kind="table")
                    continue

                if block.type in _TEXT_BLOCKS:
                    projected_length = len("\n\n".join([*buffer_texts, text]))
                    if buffer_texts and projected_length > max_chars:
                        flush()
                    buffer_texts.append(text)
                    buffer_blocks.append(block)

        flush()

        image_chunks = 0
        for page in document.pages:
            for image in page.images:
                if is_decorative_image(image):
                    continue
                image_text = clean_retrieval_text(str(image.caption or image.metadata.get("alt_text") or image.metadata.get("ocr_text") or ""))
                if not image_text:
                    continue
                chunks_created += 1
                image_chunks += 1
                page_refs = [page.page_number]
                document.chunks.append(
                    ChunkIR(
                        id=f"chunk_{document.id}_{chunks_created:04d}",
                        text=TextContent(image_text),
                        source_block_ids=[],
                        parent_block_id=None,
                        heading_path=[],
                        page_refs=page_refs,
                        bbox_refs=[image.bbox] if image.bbox else [],
                        asset_refs=[image.asset_ref],
                        metadata={
                            "stage": self.name,
                            "chunk_kind": "image",
                            "image_id": image.id,
                            **page_citation_metadata(page_refs, page_citations),
                        },
                    )
                )

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=chunks_created > 0,
            report={"chunks_created": chunks_created, "image_chunks": image_chunks},
        )

    def _run_document_block_chunks(self, document: DocumentIR, max_chars: int) -> StageResult:
        page_citations = ensure_page_citation_map(document)
        chunks_created = 0
        by_id = _document_block_by_id(document)
        source_blocks = _block_by_id(document)
        leaves = [
            block
            for block in sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
            if not block.child_ids
            and block.type
            in {
                DocumentBlockType.PARAGRAPH,
                DocumentBlockType.TABLE,
                DocumentBlockType.IMAGE,
                DocumentBlockType.FOOTNOTE,
            }
        ]

        def append_chunk(block: DocumentBlockIR, text: str, kind: str, extra_metadata: dict | None = None) -> None:
            nonlocal chunks_created
            text = clean_retrieval_text(text)
            if not text:
                return
            chunks_created += 1
            source_ids = block.source_block_ids
            source_types = [
                source_blocks[source_id].type.value
                for source_id in source_ids
                if source_id in source_blocks
            ]
            page_refs = list(block.page_refs)
            metadata = {
                "stage": self.name,
                "chunk_kind": kind,
                "parent_block_id": block.id,
                "block_path": _block_path(block, by_id),
                "block_types": _unique(source_types),
                "intra_block_view": True,
                **page_citation_metadata(page_refs, page_citations),
            }
            metadata.update(extra_metadata or {})
            document.chunks.append(
                ChunkIR(
                    id=f"chunk_{document.id}_{chunks_created:04d}",
                    text=TextContent(text),
                    source_block_ids=source_ids,
                    parent_block_id=block.id,
                    heading_path=metadata["block_path"],
                    page_refs=page_refs,
                    bbox_refs=block.bbox_refs,
                    metadata=metadata,
                )
            )

        for block in leaves:
            heading_path = _block_path(block, by_id)
            if block.type == DocumentBlockType.TABLE:
                context, table_text = _table_context(document, block, heading_path)
                lines = [line for line in table_text.splitlines() if line.strip()]
                if context and table_text.startswith(context):
                    table_body = table_text
                elif context:
                    table_body = f"{context}\n{table_text}".strip()
                else:
                    table_body = table_text
                if len(table_body) <= max_chars:
                    append_chunk(
                        block,
                        table_body,
                        "table",
                        {
                            "table_context_included": bool(context),
                            "repeated_fields": ["section_path", "table_title", "table_caption", "column_headers"],
                        },
                    )
                    continue

                header_lines = []
                body_lines = lines
                if len(lines) >= 2 and lines[0].lstrip().startswith("|") and set(lines[1].replace("|", "").strip()) <= {
                    "-",
                    ":",
                    " ",
                }:
                    header_lines = lines[:2]
                    body_lines = lines[2:]
                active_rows: list[str] = []
                row_start = 1
                for row_index, row in enumerate(body_lines, start=1):
                    prefix = "\n".join([item for item in [context, *header_lines] if item])
                    candidate = "\n".join([item for item in [prefix, *active_rows, row] if item]).strip()
                    if active_rows and len(candidate) > max_chars:
                        text = "\n".join([item for item in [prefix, *active_rows] if item]).strip()
                        append_chunk(
                            block,
                            text,
                            "table_row_group",
                            {
                                "table_context_included": bool(prefix),
                                "row_range": [row_start, row_index - 1],
                                "repeated_fields": ["section_path", "table_title", "table_caption", "column_headers"],
                            },
                        )
                        active_rows = [row]
                        row_start = row_index
                    else:
                        active_rows.append(row)
                if active_rows:
                    prefix = "\n".join([item for item in [context, *header_lines] if item])
                    text = "\n".join([item for item in [prefix, *active_rows] if item]).strip()
                    append_chunk(
                        block,
                        text,
                        "table_row_group",
                        {
                            "table_context_included": bool(prefix),
                            "row_range": [row_start, row_start + len(active_rows) - 1],
                            "repeated_fields": ["section_path", "table_title", "table_caption", "column_headers"],
                        },
                    )
                continue

            text = clean_retrieval_text(document_block_text(document, block))
            for index, piece in enumerate(_split_text_within_block(text, max_chars), start=1):
                append_chunk(
                    block,
                    piece,
                    block.type.value,
                    {"split_index": index} if len(text) > max_chars else None,
                )

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=chunks_created > 0,
            report={"chunks_created": chunks_created, "strategy": "intra_block"},
        )
