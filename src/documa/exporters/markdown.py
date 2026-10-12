"""Markdown exporter for Documa IR."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote
import re

from documa.core.image_filtering import is_decorative_image
from documa.core.ir import BlockIR, BlockType, DocumentIR
from documa.exporters.base import ExportOptions, Exporter
from documa.exporters.pdf_reflow import reflow_pdf_text
from documa.pipeline.relations import block_text
from documa.pipeline.continuations import join_fragments


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


def _comment_text(text: str) -> str:
    return " ".join(text.replace("-->", "-- >").split())


def _trim_blank_lines(text: str) -> str:
    rows = text.split("\n")
    start, end = 0, len(rows)
    while start < end and not rows[start].strip():
        start += 1
    while end > start and not rows[end - 1].strip():
        end -= 1
    return "\n".join(rows[start:end])


@dataclass(slots=True)
class MarkdownExporter(Exporter):
    name: str = "markdown"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> str:
        options = options or ExportOptions()
        title = document.metadata.get("document_title") or document.source_name or document.id
        lines = [f"# {title}", ""]
        code_fence: str | None = None
        source_index = {b.id: b for p in document.pages for b in p.blocks}
        chains = {g['source_block_ids'][0]: g['source_block_ids'] for g in document.metadata.get('paragraph_continuations', [])}
        tails = {identity for ids in chains.values() for identity in ids[1:]}

        for page in sorted(document.pages, key=lambda item: item.page_number):
            lines.extend([f"<!-- page: {page.page_number} -->", ""])
            blocks = sorted(page.blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
            groups: dict[str, list[BlockIR]] = {}
            for block in blocks:
                if group_id := block.metadata.get("figure_group_id"):
                    groups.setdefault(group_id, []).append(block)
            emitted_groups: set[str] = set()
            emitted_images: set[str] = set()
            for block in blocks:
                if block.id in tails:
                    continue
                if block.id in chains:
                    parts = [reflow_pdf_text(source_index[identity], _trim_blank_lines(block_text(source_index[identity]))) for identity in chains[block.id]]
                    lines.extend([join_fragments(parts), ""])
                    continue
                if group_id := block.metadata.get("figure_group_id"):
                    if group_id in emitted_groups:
                        continue
                    emitted_groups.add(group_id)
                    members = groups[group_id]
                    caption = next(b for b in members if b.id == group_id)
                    label = re.match(r"\s*((?:Figure|Fig\.?|圖)\s*[\dA-Za-z]+)", block_text(caption), re.I)
                    label = label.group(1) if label else "Figure"
                    lines.extend([f"<details><summary>{escape(label)}：圖內文字與素材</summary>", ""])
                    remote_source = document.metadata.get("source_access", {}).get("source_url")
                    if remote_source:
                        source_url = quote(remote_source, safe='/:?=&%')
                        lines.extend([f"[查看原始圖頁（PDF p.{page.page_number}）]({source_url}#page={page.page_number})", ""])
                    elif document.source_name.lower().endswith('.pdf'):
                        source_url = quote(Path(document.source_name).as_posix(), safe='/:')
                        lines.extend([f"[查看原始圖頁（PDF p.{page.page_number}）]({source_url}#page={page.page_number})", ""])
                    for member in members:
                        if member is not caption and (value := block_text(member).strip()):
                            value = re.sub(r"(?m)^(#{1,6})(?=\s)", r"\\\1", value)
                            lines.extend([value, ""])
                    if options.include_images:
                        for picture in page.images:
                            if picture.metadata.get("figure_group_id") == group_id and not is_decorative_image(picture):
                                lines.extend([f"![{picture.image_type}]({picture.asset_ref})", ""])
                                emitted_images.add(picture.id)
                    lines.extend(["</details>", "", reflow_pdf_text(caption, block_text(caption)).strip(), ""])
                    continue
                text = block_text(block).strip()
                fenced = code_fence is not None
                for source_line in text.splitlines():
                    marker = re.match(r"^\s*(`{3,}|~{3,})", source_line)
                    if marker:
                        fenced = True
                        value = marker.group(1)
                        if code_fence is None:
                            code_fence = value
                        elif value[0] == code_fence[0] and len(value) >= len(code_fence):
                            code_fence = None
                if block.type == BlockType.PAGE_HEADER:
                    if text:
                        lines.extend([f"<!-- page-header: {_comment_text(text)} -->", ""])
                    continue
                if block.type == BlockType.PAGE_FOOTER:
                    if text:
                        lines.extend([f"<!-- page-footer: {_comment_text(text)} -->", ""])
                    continue
                if block.type == BlockType.UNKNOWN:
                    continue
                if block.type == BlockType.HEADING and text:
                    if block.metadata.get("heading_role") == "document_title":
                        continue
                    lines.extend([f"{_heading_prefix(block)} {text}", ""])
                    continue
                if block.type == BlockType.TABLE:
                    table_text = _table_markdown(document, block) or text
                    if table_text:
                        lines.extend([table_text, ""])
                    continue
                if text:
                    if document.parser in {"rust_pdf", "pymupdf"}:
                        # Keep source indentation for protected code/list rows.
                        text = _trim_blank_lines(block_text(block))
                        if not fenced:
                            text = reflow_pdf_text(block, text)
                            # Literal Markdown in PDF code/prompt examples is
                            # content, not an additional document heading.
                            text = re.sub(r"(?m)^(#{1,6})(?=\s)", r"\\\1", text)
                    lines.extend([text, ""])

            if options.include_images:
                for image in page.images:
                    if image.id in emitted_images or is_decorative_image(image):
                        continue
                    lines.append(f"![{image.image_type}]({image.asset_ref})")
                    if image.caption:
                        lines.append("")
                        lines.append(image.caption)
                    lines.append("")

        return "\n".join(lines).strip() + "\n"
