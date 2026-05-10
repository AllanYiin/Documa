"""RAG/RLM-ready chunking stage."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.ir import BlockIR, BlockType, ChunkIR, DocumentIR, TextContent
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
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
        return _table_markdown(document, block) or block_text(block)
    return block_text(block)


@dataclass(slots=True)
class ChunkingStage(PipelineStage):
    """Create layout-aware chunks with source ids, page refs, and headings."""

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
            chunk = ChunkIR(
                id=f"chunk_{document.id}_{chunks_created:04d}",
                text=TextContent(text),
                source_block_ids=[block.id for block in buffer_blocks],
                heading_path=list(heading_path),
                page_refs=_unique([block.page_number for block in buffer_blocks]),
                bbox_refs=_unique([block.bbox for block in buffer_blocks if block.bbox]),
                metadata={
                    "stage": self.name,
                    "chunk_kind": kind,
                    "block_types": _unique([block.type.value for block in buffer_blocks]),
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
                image_text = image.caption or image.metadata.get("alt_text") or image.metadata.get("ocr_text")
                if not image_text:
                    continue
                chunks_created += 1
                image_chunks += 1
                document.chunks.append(
                    ChunkIR(
                        id=f"chunk_{document.id}_{chunks_created:04d}",
                        text=TextContent(str(image_text)),
                        source_block_ids=[],
                        heading_path=[],
                        page_refs=[page.page_number],
                        bbox_refs=[image.bbox] if image.bbox else [],
                        asset_refs=[image.asset_ref],
                        metadata={"stage": self.name, "chunk_kind": "image", "image_id": image.id},
                    )
                )

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=chunks_created > 0,
            report={"chunks_created": chunks_created, "image_chunks": image_chunks},
        )

