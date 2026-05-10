"""Markdown exporter for Documa IR."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.ir import BlockIR, BlockType, DocumentIR
from documa.exporters.base import ExportOptions, Exporter
from documa.pipeline.relations import block_text


def _heading_prefix(block: BlockIR) -> str:
    level = int(block.metadata.get("heading_level", 1) or 1)
    level = max(1, min(level, 6))
    return "#" * level


def _table_markdown(document: DocumentIR, block: BlockIR) -> str | None:
    table_id = block.metadata.get("table_id")
    for table in document.tables:
        if table.block_id == block.id or table.id == table_id:
            return table.markdown
    return None


@dataclass(slots=True)
class MarkdownExporter(Exporter):
    name: str = "markdown"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> str:
        options = options or ExportOptions()
        lines = [f"# {document.source_name or document.id}", ""]

        for page in sorted(document.pages, key=lambda item: item.page_number):
            lines.extend([f"<!-- page: {page.page_number} -->", ""])
            blocks = sorted(page.blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
            for block in blocks:
                text = block_text(block).strip()
                if block.type in {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER, BlockType.UNKNOWN}:
                    continue
                if block.type == BlockType.HEADING and text:
                    lines.extend([f"{_heading_prefix(block)} {text}", ""])
                    continue
                if block.type == BlockType.TABLE:
                    table_text = _table_markdown(document, block) or text
                    if table_text:
                        lines.extend([table_text, ""])
                    continue
                if text:
                    lines.extend([text, ""])

            if options.include_images:
                for image in page.images:
                    lines.append(f"![{image.image_type}]({image.asset_ref})")
                    if image.caption:
                        lines.append("")
                        lines.append(image.caption)
                    lines.append("")

        return "\n".join(lines).strip() + "\n"

