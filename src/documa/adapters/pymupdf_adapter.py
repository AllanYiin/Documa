"""PyMuPDF parser adapter.

This module keeps PyMuPDF objects behind the adapter boundary. Downstream code
receives only Documa IR dataclasses and asset references.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
import uuid

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    ImageIR,
    PageIR,
    RelationIR,
    RelationType,
    SpanIR,
    SpanStyle,
    TextContent,
)
from documa.core.language import LanguageHint, detect_text_script
from documa.storage.assets import AssetStore, safe_asset_name


def _load_pymupdf():
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        try:
            import fitz  # type: ignore

            return fitz
        except ImportError as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="PYMUPDF_NOT_INSTALLED",
                    message="PyMuPDF is required for PyMuPDFAdapter.",
                    recoverable=True,
                    suggested_action="Install the optional pdf dependency: pip install documa[pdf]",
                )
            ) from exc


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if hasattr(value, "__iter__"):
        items = list(value)
        if len(items) >= 4:
            return (float(items[0]), float(items[1]), float(items[2]), float(items[3]))
    return None


def _span_style(span: dict[str, Any]) -> list[SpanStyle]:
    font = str(span.get("font") or "").lower()
    flags = int(span.get("flags") or 0)
    styles: list[SpanStyle] = []
    if "bold" in font:
        styles.append(SpanStyle.BOLD)
    if "italic" in font or "oblique" in font:
        styles.append(SpanStyle.ITALIC)
    if flags & 2:
        styles.append(SpanStyle.ITALIC)
    return styles


def _language_for_text(text: str, options: ParseOptions) -> LanguageHint:
    if len(options.languages) == 1 and options.languages[0] != "auto":
        language = options.languages[0]
    else:
        script = detect_text_script(text)
        if script == "Traditional":
            language = "zh-Hant"
        elif script == "Simplified":
            language = "zh-Hans"
        elif script == "Latin":
            language = "en"
        else:
            language = "auto"
    script = detect_text_script(text)
    return LanguageHint(language=language, script=None if script == "Unknown" else script)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__iter__"):
        try:
            return [float(item) for item in value]
        except Exception:
            pass
    return str(value)


def _rect_area(rect: tuple[float, float, float, float] | None) -> float:
    if rect is None:
        return 0.0
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection_area(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
) -> float:
    if left is None or right is None:
        return 0.0
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _overlap_ratio(
    block_bbox: tuple[float, float, float, float] | None,
    table_bbox: tuple[float, float, float, float] | None,
) -> float:
    area = _rect_area(block_bbox)
    if area <= 0:
        return 0.0
    return _intersection_area(block_bbox, table_bbox) / area


def _cell_text(cell: str | None) -> str:
    return "" if cell is None else str(cell).strip()


def _clean_table_rows(rows: Any) -> list[list[str | None]]:
    cleaned: list[list[str | None]] = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)):
            continue
        cells = [None if cell is None else str(cell).strip() for cell in row]
        if any(_cell_text(cell) for cell in cells):
            cleaned.append(cells)
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    if width < 2 or len(cleaned) < 2:
        return []
    return [row + [None] * (width - len(row)) for row in cleaned]


def _table_text(rows: list[list[str | None]]) -> str:
    return "\n".join(" | ".join(_cell_text(cell) for cell in row) for row in rows)


def _find_page_tables(page: Any) -> list[Any]:
    if not hasattr(page, "find_tables"):
        return []
    try:
        stdout = StringIO()
        with redirect_stdout(stdout):
            finder = page.find_tables()
    except Exception:
        return []
    return list(getattr(finder, "tables", []) or [])


class PyMuPDFAdapter(ParserAdapter):
    """Parse PDFs into Documa IR primitives using PyMuPDF."""

    name = "pymupdf"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        pymupdf = _load_pymupdf()
        options = options or ParseOptions()
        source_path = Path(source)
        asset_store = AssetStore(options.asset_dir) if options.asset_dir else None

        try:
            pdf = pymupdf.open(source_path)
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="PDF_OPEN_FAILED",
                    message=f"Unable to open PDF: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists, is readable, and is not encrypted.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc

        document = DocumentIR(
            id=f"doc_{uuid.uuid4().hex}",
            source_name=str(source_path),
            parser=self.name,
            metadata={
                "page_count": getattr(pdf, "page_count", len(pdf)),
                "toc": _json_safe(pdf.get_toc(simple=False)) if hasattr(pdf, "get_toc") else [],
                "adapter": self.name,
            },
        )

        for page_index, page in enumerate(pdf, start=1):
            page_ir = self._parse_page(pymupdf, page, page_index, options, asset_store)
            document.pages.append(page_ir)

        pdf.close()
        return document

    def _parse_page(
        self,
        pymupdf: Any,
        page: Any,
        page_number: int,
        options: ParseOptions,
        asset_store: AssetStore | None,
    ) -> PageIR:
        rect = page.rect
        page_ir = PageIR(
            id=f"page_{page_number}",
            page_number=page_number,
            width=float(rect.width),
            height=float(rect.height),
            rotation=int(getattr(page, "rotation", 0) or 0),
            metadata={
                "label": page.get_label() if hasattr(page, "get_label") else "",
                "links": _json_safe(page.get_links() if hasattr(page, "get_links") else []),
            },
        )

        if asset_store:
            page_ir.metadata["preview_asset_ref"] = self._write_page_preview(
                pymupdf,
                page,
                page_number,
                options,
                asset_store,
            )

        self._parse_text_blocks(page, page_ir, options)
        self._parse_tables(page, page_ir)
        if options.extract_images:
            self._parse_images(page, page_ir, asset_store)
        self._parse_annotations(page, page_ir)
        return page_ir

    def _write_page_preview(
        self,
        pymupdf: Any,
        page: Any,
        page_number: int,
        options: ParseOptions,
        asset_store: AssetStore,
    ) -> str:
        scale = max(float(options.preview_scale), 0.1)
        matrix = pymupdf.Matrix(scale, scale)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        return asset_store.write_bytes(
            f"previews/page_{page_number:04d}.png",
            pixmap.tobytes("png"),
        )

    def _parse_text_blocks(self, page: Any, page_ir: PageIR, options: ParseOptions) -> None:
        text_dict = page.get_text("dict")
        block_order = 0
        for block in text_dict.get("blocks", []):
            if int(block.get("type", 0)) != 0:
                continue
            spans: list[SpanIR] = []
            text_parts: list[str] = []
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = str(span.get("text") or "")
                    if span_text:
                        text_parts.append(span_text)
                    span_id = f"p{page_ir.page_number}_s{len(spans) + 1}_{block_order}"
                    spans.append(
                        SpanIR(
                            id=span_id,
                            text=TextContent(span_text),
                            bbox=_bbox(span.get("bbox")),
                            font_size=float(span["size"]) if span.get("size") is not None else None,
                            font_name=span.get("font"),
                            style=_span_style(span),
                            language=_language_for_text(span_text, options),
                            metadata={"origin": _json_safe(span.get("origin"))},
                        )
                    )
            raw_text = "".join(text_parts)
            block_order += 1
            page_ir.blocks.append(
                BlockIR(
                    id=f"p{page_ir.page_number}_b{block_order}",
                    type=BlockType.TEXT,
                    page_number=page_ir.page_number,
                    text=TextContent(raw_text),
                    bbox=_bbox(block.get("bbox")),
                    spans=spans,
                    confidence=Confidence.HIGH if raw_text else Confidence.UNKNOWN,
                    order_index=block_order,
                    source_refs=[f"pymupdf:block:{page_ir.page_number}:{block_order}"],
                    metadata={"source_type": "pymupdf_text_block"},
                )
            )

    def _parse_tables(self, page: Any, page_ir: PageIR) -> None:
        table_blocks: list[BlockIR] = []
        remaining_blocks = list(page_ir.blocks)
        for table_index, table in enumerate(_find_page_tables(page), start=1):
            rows = _clean_table_rows(table.extract() if hasattr(table, "extract") else [])
            bbox = _bbox(getattr(table, "bbox", None))
            if not rows or bbox is None:
                continue

            overlapping = [
                block
                for block in remaining_blocks
                if block.type == BlockType.TEXT and _overlap_ratio(block.bbox, bbox) >= 0.75
            ]
            overlapping_ids = {block.id for block in overlapping}
            remaining_blocks = [block for block in remaining_blocks if block.id not in overlapping_ids]
            source_order = min(
                (block.order_index for block in overlapping if block.order_index is not None),
                default=len(remaining_blocks) + len(table_blocks) + 1,
            )
            source_refs = [ref for block in overlapping for ref in block.source_refs]
            source_blocks = [
                {
                    "id": block.id,
                    "bbox": block.bbox,
                    "text": block.text.raw_text if block.text else "",
                    "source_refs": block.source_refs,
                }
                for block in overlapping
            ]
            table_blocks.append(
                BlockIR(
                    id=f"p{page_ir.page_number}_table{table_index}",
                    type=BlockType.TABLE,
                    page_number=page_ir.page_number,
                    text=TextContent(_table_text(rows)),
                    bbox=bbox,
                    confidence=Confidence.MEDIUM,
                    order_index=source_order,
                    source_refs=source_refs or [f"pymupdf:table:{page_ir.page_number}:{table_index}"],
                    metadata={
                        "source_type": "pymupdf_table",
                        "table_rows": rows,
                        "source_block_ids": [block.id for block in overlapping],
                        "source_blocks": source_blocks,
                        "extraction_strategy": "pymupdf_find_tables",
                    },
                )
            )

        if table_blocks:
            page_ir.blocks = sorted(
                [*remaining_blocks, *table_blocks],
                key=lambda block: (
                    block.order_index is None,
                    block.order_index or 0,
                    block.bbox[1] if block.bbox else 0.0,
                    block.bbox[0] if block.bbox else 0.0,
                ),
            )

    def _parse_images(self, page: Any, page_ir: PageIR, asset_store: AssetStore | None) -> None:
        if not hasattr(page, "get_images"):
            return
        doc = page.parent
        seen: set[tuple[int, int]] = set()
        for image_index, image_info in enumerate(page.get_images(full=True), start=1):
            xref = int(image_info[0])
            rects = page.get_image_rects(xref) if hasattr(page, "get_image_rects") else []
            if not rects:
                rects = [None]
            asset_ref = f"images/page_{page_ir.page_number:04d}_image_{image_index:03d}.bin"
            if asset_store:
                extracted = doc.extract_image(xref)
                ext = safe_asset_name(str(extracted.get("ext") or "bin"))
                asset_ref = asset_store.write_bytes(
                    f"images/page_{page_ir.page_number:04d}_image_{image_index:03d}.{ext}",
                    extracted["image"],
                )
            for rect_index, rect in enumerate(rects, start=1):
                key = (xref, rect_index)
                if key in seen:
                    continue
                seen.add(key)
                page_ir.images.append(
                    ImageIR(
                        id=f"p{page_ir.page_number}_img{image_index}_{rect_index}",
                        page_number=page_ir.page_number,
                        bbox=_bbox(rect),
                        asset_ref=asset_ref,
                        image_type="image",
                        confidence=Confidence.MEDIUM,
                        metadata={
                            "xref": xref,
                            "source": "pymupdf_image",
                            "width": image_info[2] if len(image_info) > 2 else None,
                            "height": image_info[3] if len(image_info) > 3 else None,
                        },
                    )
                )

    def _parse_annotations(self, page: Any, page_ir: PageIR) -> None:
        if not hasattr(page, "annots"):
            return
        annotations: list[dict[str, Any]] = []
        annot_iter = page.annots()
        if annot_iter is None:
            page_ir.metadata["annotations"] = annotations
            return
        for annot in annot_iter:
            annotations.append(
                {
                    "type": str(getattr(annot, "type", "")),
                    "rect": _bbox(getattr(annot, "rect", None)),
                    "content": getattr(annot, "info", {}).get("content") if hasattr(annot, "info") else None,
                }
            )
        page_ir.metadata["annotations"] = annotations


def build_page_link_relations(document: DocumentIR) -> list[RelationIR]:
    """Build lightweight unresolved relations for page links.

    Full TOC/link resolution belongs to Stage 4. This helper preserves link
    evidence early without pretending the target block is known.
    """

    relations: list[RelationIR] = []
    for page in document.pages:
        for index, link in enumerate(page.metadata.get("links", []), start=1):
            relations.append(
                RelationIR(
                    id=f"rel_link_p{page.page_number}_{index}",
                    type=RelationType.UNRESOLVED,
                    from_id=page.id,
                    confidence=Confidence.LOW,
                    evidence=[f"pymupdf link on page {page.page_number}: {link}"],
                    metadata={"source": "pymupdf_link", "link": link},
                )
            )
    return relations
