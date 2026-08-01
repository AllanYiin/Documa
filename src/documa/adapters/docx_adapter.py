"""DOCX parser adapter."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, PageIR, SpanIR, SpanStyle, TableIR, TextContent


_HEADING_STYLE_RE = re.compile(r"^heading\s+(?P<level>[1-9])$", re.IGNORECASE)
_EMU_PAGE_WIDTH = 12240


def _load_docx():
    try:
        from docx import Document  # type: ignore
        from docx.oxml.ns import qn  # type: ignore
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore

        return Document, Paragraph, Table, qn
    except ImportError as exc:
        raise DocumaError(
            DocumaErrorDetail(
                code="DOCX_DEPENDENCY_NOT_INSTALLED",
                message="python-docx is required for DocxAdapter.",
                recoverable=True,
                suggested_action="Install or repair the standard runtime: pip install --upgrade documa",
            )
        ) from exc


def _document_id(source_path: Path, size: int) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}\n{size}".encode("utf-8")).hexdigest()[:16]
    return f"doc_docx_{digest}"


def _style_name(item: Any) -> str | None:
    style = getattr(item, "style", None)
    name = getattr(style, "name", None)
    return str(name) if name else None


def _heading_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    if style_name.casefold() == "title":
        return 1
    match = _HEADING_STYLE_RE.match(style_name.strip())
    if match:
        return int(match.group("level"))
    return None


def _run_styles(run: Any) -> list[SpanStyle]:
    styles: list[SpanStyle] = []
    if getattr(run, "bold", False):
        styles.append(SpanStyle.BOLD)
    if getattr(run, "italic", False):
        styles.append(SpanStyle.ITALIC)
    if getattr(run, "underline", False):
        styles.append(SpanStyle.UNDERLINE)
    return styles


def _table_rows(table: Any) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    for row in table.rows:
        cells = [cell.text.strip() or None for cell in row.cells]
        if any(cell for cell in cells):
            rows.append(cells)
    return rows


def _table_text(rows: list[list[str | None]]) -> str:
    return "\n".join(" | ".join("" if cell is None else cell for cell in row) for row in rows)


class DocxAdapter(ParserAdapter):
    """Parse DOCX files into flow-oriented Documa IR."""

    name = "docx"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        Document, Paragraph, Table, qn = _load_docx()

        try:
            docx = Document(str(source_path))
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="DOCX_OPEN_FAILED",
                    message=f"Unable to open DOCX: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists and is a valid .docx file.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc

        document = DocumentIR(
            id=_document_id(source_path, source_path.stat().st_size if source_path.exists() else 0),
            source_name=str(source_path),
            parser=self.name,
            metadata={
                "adapter": self.name,
                "format": "docx",
                "languages": list(options.languages),
                "page_model": "logical_flow",
            },
        )
        page = PageIR(
            id="page_1",
            page_number=1,
            width=float(_EMU_PAGE_WIDTH),
            height=0.0,
            metadata={"source": "docx_body", "page_model": "logical_flow"},
        )
        document.pages.append(page)

        order = 0
        table_index = 0
        body = docx.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, docx)
                text = paragraph.text.strip()
                if not text:
                    continue
                order += 1
                page.blocks.append(self._paragraph_block(paragraph, order))
            elif child.tag == qn("w:tbl"):
                table_index += 1
                table = Table(child, docx)
                rows = _table_rows(table)
                if not rows:
                    continue
                order += 1
                block = self._table_block(table, rows, order, table_index)
                page.blocks.append(block)
                document.tables.append(
                    TableIR(
                        id=f"table_{block.id}",
                        block_id=block.id,
                        rows=rows,
                        markdown=None,
                        confidence=Confidence.MEDIUM,
                        metadata={"source": "docx_table"},
                    )
                )

        page.height = float(max(1, order))
        return document

    def _paragraph_block(self, paragraph: Any, order: int) -> BlockIR:
        style_name = _style_name(paragraph)
        heading_level = _heading_level(style_name)
        text = paragraph.text.strip()
        spans: list[SpanIR] = []
        for run_index, run in enumerate(paragraph.runs, start=1):
            run_text = str(run.text or "")
            if not run_text:
                continue
            spans.append(
                SpanIR(
                    id=f"docx_s{order:04d}_{run_index:02d}",
                    text=TextContent(run_text),
                    style=_run_styles(run),
                    metadata={"source": "docx_run"},
                )
            )
        metadata: dict[str, Any] = {
            "source_type": "docx_paragraph",
            "style_name": style_name,
            "paragraph_title": text if heading_level is None else None,
        }
        if heading_level is not None:
            metadata["heading_level"] = heading_level
        return BlockIR(
            id=f"docx_b{order:04d}",
            type=BlockType.HEADING if heading_level is not None else BlockType.PARAGRAPH,
            page_number=1,
            text=TextContent(text),
            spans=spans,
            confidence=Confidence.HIGH,
            order_index=order,
            source_refs=[f"docx:block:{order}"],
            metadata=metadata,
        )
    def _table_block(self, table: Any, rows: list[list[str | None]], order: int, table_index: int) -> BlockIR:
        return BlockIR(
            id=f"docx_table{table_index:04d}",
            type=BlockType.TABLE,
            page_number=1,
            text=TextContent(_table_text(rows)),
            confidence=Confidence.MEDIUM,
            order_index=order,
            source_refs=[f"docx:table:{table_index}"],
            metadata={
                "source_type": "docx_table",
                "style_name": _style_name(table),
                "table_rows": rows,
                "table_title": rows[0][0] if rows and rows[0] else None,
            },
        )
