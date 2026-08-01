"""Map the optional shared Rust Layout IR into parser-neutral Documa IR.

The Rust binding is imported lazily. This module validates and converts the
versioned DTO only; it never interprets PDF syntax.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import uuid

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.image_filtering import decorative_image_reason
from documa.core.ir import (
    BlockIR, BlockType, Confidence, DocumentIR, ImageIR, PageIR,
    SpanIR, SpanStyle, TextContent,
)
from documa.core.language import LanguageHint, detect_text_script
from documa.storage.assets import AssetStore, safe_asset_name

_SCHEMA = 1
_SPACE = "layout_unrotated_top_left"
_REQUIRED_RUST_PDF_VERSION = "0.2.0"


def _load_rust_pdf() -> Any:
    try:
        import rust_pdf  # type: ignore
    except ImportError as exc:
        raise DocumaError(DocumaErrorDetail(
            code="RUST_PDF_NOT_INSTALLED",
            message="The optional rust_pdf binding is required for RustPdfAdapter.",
            recoverable=True,
            suggested_action="Install a verified rust-pdf-parser Python wheel.",
        )) from exc
    try:
        version, stage = rust_pdf.version_info()
    except (AttributeError, TypeError, ValueError) as exc:
        raise DocumaError(DocumaErrorDetail(
            code="RUST_PDF_INCOMPATIBLE_VERSION",
            message="The installed rust_pdf binding does not expose the 0.2.0 version contract.",
            recoverable=True,
            suggested_action="Install rust-pdf-parser 0.2.0 from the configured local project.",
        )) from exc
    if str(version) != _REQUIRED_RUST_PDF_VERSION:
        raise DocumaError(DocumaErrorDetail(
            code="RUST_PDF_INCOMPATIBLE_VERSION",
            message=(
                f"rust-pdf-parser {_REQUIRED_RUST_PDF_VERSION} is required; "
                f"found {version}."
            ),
            recoverable=True,
            suggested_action="Install rust-pdf-parser 0.2.0 from the configured local project.",
            context={"required": _REQUIRED_RUST_PDF_VERSION, "actual": str(version), "stage": str(stage)},
        ))
    return rust_pdf


def _layout_error(message: str, **context: Any) -> DocumaError:
    return DocumaError(DocumaErrorDetail(
        code="RUST_PDF_LAYOUT_INCOMPATIBLE",
        message=message,
        recoverable=True,
        suggested_action="Use the matching Rust wheel or select the pymupdf provider.",
        context=context,
    ))


def _dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _layout_error(f"Rust Layout IR field {field} must be an object.")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise _layout_error(f"Rust Layout IR field {field} must be an array.")
    return value


def _bbox(value: Any, field: str) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    item = _dict(value, field)
    try:
        box = tuple(float(item[key]) for key in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError) as exc:
        raise _layout_error(f"Rust Layout IR field {field} is not a valid BBox.") from exc
    if not all(math.isfinite(number) for number in box) or box[2] <= box[0] or box[3] <= box[1]:
        raise _layout_error(f"Rust Layout IR field {field} is not a finite positive BBox.")
    return box  # type: ignore[return-value]


def _confidence(value: Any) -> Confidence:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return Confidence.UNKNOWN
    if number >= 0.85:
        return Confidence.HIGH
    if number >= 0.60:
        return Confidence.MEDIUM
    return Confidence.LOW if number > 0 else Confidence.UNKNOWN


def _language(text: str, options: ParseOptions) -> LanguageHint:
    script = detect_text_script(text)
    if len(options.languages) == 1 and options.languages[0] != "auto":
        language = options.languages[0]
    else:
        language = {"Traditional": "zh-Hant", "Simplified": "zh-Hans", "Latin": "en"}.get(script, "auto")
    return LanguageHint(language=language, script=None if script == "Unknown" else script)


def _styles(font_name: str | None) -> list[SpanStyle]:
    name = (font_name or "").lower()
    result: list[SpanStyle] = []
    if "bold" in name:
        result.append(SpanStyle.BOLD)
    if "italic" in name or "oblique" in name:
        result.append(SpanStyle.ITALIC)
    return result


def _block_type(role: str) -> BlockType:
    return {
        "heading": BlockType.HEADING,
        "paragraph": BlockType.PARAGRAPH,
        "list": BlockType.PARAGRAPH,
        "list_item": BlockType.PARAGRAPH,
        "list_body": BlockType.PARAGRAPH,
        "header": BlockType.PAGE_HEADER,
        "footer": BlockType.PAGE_FOOTER,
        "page_number": BlockType.PAGE_FOOTER,
        "artifact": BlockType.UNKNOWN,
    }.get(role, BlockType.TEXT)


def _object_key(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, dict):
        return None
    number, generation = value.get("number"), value.get("generation")
    return (number, generation) if isinstance(number, int) and isinstance(generation, int) else None


def _trace(provenance: Any, rule_id: Any) -> list[Any] | None:
    if provenance is None:
        return None
    value = _dict(provenance, "provenance")
    return [
        value.get("source_ordinal_start"),
        value.get("source_ordinal_end"),
        value.get("mcids", []),
        value.get("text_origins", []),
        rule_id,
    ]


def _present(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value is not False and value != [] and value != {}:
        metadata[key] = value


def _span_metadata(span: dict[str, Any], verbose: bool) -> dict[str, Any]:
    if verbose:
        return {
            "source": "rust_pdf_layout_span", "rust_pdf_span_id": span.get("id"),
            "tag": span.get("tag"), "mcid": span.get("mcid"),
            "artifact": bool(span.get("artifact", False)),
            "actual_text": span.get("actual_text"), "rule_id": span.get("rule_id"),
            "provenance": span.get("provenance"), "coordinate_space": _SPACE,
        }
    metadata: dict[str, Any] = {}
    _present(metadata, "rust_pdf_trace", _trace(span.get("provenance"), span.get("rule_id")))
    _present(metadata, "tag", span.get("tag"))
    _present(metadata, "alt_text", span.get("alt_text"))
    _present(metadata, "actual_text", span.get("actual_text"))
    _present(metadata, "artifact", bool(span.get("artifact", False)))
    return metadata


def _node_metadata(
    node: dict[str, Any],
    node_id: str,
    role: str,
    verbose: bool,
) -> dict[str, Any]:
    if verbose:
        return {
            "source_type": "rust_pdf_layout_node", "rust_pdf_node_id": node_id,
            "rust_pdf_role": role, "tag": node.get("tag"), "alt_text": node.get("alt_text"),
            "actual_text": node.get("actual_text"), "artifact": bool(node.get("artifact", False)),
            "structure_object": node.get("structure_object"), "rule_id": node.get("rule_id"),
            "provenance": node.get("provenance"), "coordinate_space": _SPACE,
        }
    metadata: dict[str, Any] = {"rust_pdf_role": role}
    _present(metadata, "rust_pdf_trace", _trace(node.get("provenance"), node.get("rule_id")))
    for key in ("tag", "alt_text", "actual_text", "structure_object"):
        _present(metadata, key, node.get(key))
    _present(metadata, "artifact", bool(node.get("artifact", False)))
    return metadata


def _node_block(
    node: dict[str, Any],
    page_number: int,
    options: ParseOptions,
    verbose_metadata: bool,
) -> BlockIR:
    node_id = str(node.get("id") or "")
    if not node_id:
        raise _layout_error("Rust semantic node is missing id.")
    role, text = str(node.get("role") or "unclassified"), str(node.get("text") or "")
    spans: list[SpanIR] = []
    for index, raw in enumerate(_list(node.get("spans", []), f"node[{node_id}].spans"), 1):
        span = _dict(raw, f"node[{node_id}].spans[{index}]")
        span_text, font = str(span.get("text") or ""), span.get("font_resource")
        spans.append(SpanIR(
            id=f"rust_{span.get('id') or f'{node_id}_s{index}'}",
            text=TextContent(span_text),
            bbox=_bbox(span.get("bbox"), f"node[{node_id}].spans[{index}].bbox"),
            font_size=float(span["font_size"]) if span.get("font_size") is not None else None,
            font_name=str(font) if font is not None else None,
            style=_styles(str(font) if font is not None else None),
            language=_language(span_text, options),
            metadata=_span_metadata(span, verbose_metadata),

        ))
    metadata = _node_metadata(node, node_id, role, verbose_metadata)

    if role == "caption":
        metadata["caption_kind"] = "figure"
    return BlockIR(
        id=f"rust_{node_id}", type=_block_type(role), page_number=page_number,
        text=TextContent(text), bbox=_bbox(node.get("bbox"), f"node[{node_id}].bbox"),
        spans=spans, confidence=_confidence(node.get("confidence")),
        source_refs=[f"rust-pdf:node:{page_number}:{node_id}"], metadata=metadata,
    )


def _best_order(page: dict[str, Any], node_ids: list[str]) -> list[str]:
    orders = _dict(page.get("orders", {}), "page.orders")
    result: list[str] = []
    for field in ("inferred_order", "source_order"):
        for raw in _list(orders.get(field, []), f"page.orders.{field}"):
            item = str(raw)
            if item in node_ids and item not in result:
                result.append(item)
    result.extend(item for item in node_ids if item not in result)
    return result


def _apply_stream_finalization(document: DocumentIR, raw: Any) -> None:
    finalization = _dict(raw, "layout.page_finalization")
    page_index = int(finalization.get("page_index", -1))
    if not 0 <= page_index < len(document.pages):
        raise _layout_error("Rust page finalization references an unknown page.", page_index=page_index)
    page = document.pages[page_index]
    main_flow = [str(value) for value in _list(finalization.get("main_flow", []), "page_finalization.main_flow")]
    orders = _dict(page.metadata.get("rust_pdf_orders", {}), "page.metadata.rust_pdf_orders")
    orders["main_flow"] = main_flow
    blocks_by_node = {
        block.source_refs[0].rsplit(":", 1)[-1]: block
        for block in page.blocks
        if block.source_refs and block.source_refs[0].startswith("rust-pdf:node:")
    }
    for raw_update in _list(finalization.get("node_updates", []), "page_finalization.node_updates"):
        update = _dict(raw_update, "page_finalization.node_update")
        node_id = str(update.get("node_id") or "")
        block = blocks_by_node.get(node_id)
        if block is None:
            continue
        role = str(update.get("role") or "unclassified")
        block.type = _block_type(role)
        block.confidence = _confidence(update.get("confidence"))
        block.metadata["rust_pdf_role"] = role
        if "rule_id" in block.metadata:
            block.metadata["rule_id"] = update.get("rule_id")
        trace = block.metadata.get("rust_pdf_trace")
        if isinstance(trace, list) and len(trace) == 5:
            trace[4] = update.get("rule_id")

def _iter_rust_pages(pages: Any, source_path: Path):
    try:
        yield from pages
    except DocumaError:
        raise
    except Exception as exc:
        raise DocumaError(DocumaErrorDetail(
            code="RUST_PDF_PARSE_FAILED", message=f"Rust PDF parsing failed: {source_path}", recoverable=True,
            suggested_action="Inspect the Rust error or use the pymupdf provider.",
            context={"source": str(source_path), "error": str(exc)},
        )) from exc


def _table_block(
    table: dict[str, Any],
    page_number: int,
    nodes: dict[str, BlockIR],
    positions: dict[str, int],
    verbose_metadata: bool,
) -> tuple[int, BlockIR]:
    table_id = str(table.get("id") or "")
    try:
        height, width = int(table.get("rows", 0)), int(table.get("columns", 0))
    except (TypeError, ValueError) as exc:
        raise _layout_error("Rust table dimensions must be integers.") from exc
    if not table_id or height <= 0 or width <= 0:
        raise _layout_error("Rust table id and positive dimensions are required.")
    rows: list[list[str | None]] = [[None] * width for _ in range(height)]
    for raw in _list(table.get("cells", []), f"table[{table_id}].cells"):
        cell = _dict(raw, f"table[{table_id}].cell")
        try:
            row, column = int(cell.get("row")), int(cell.get("column"))
        except (TypeError, ValueError) as exc:
            raise _layout_error("Rust table cell coordinates must be integers.") from exc
        if not 0 <= row < height or not 0 <= column < width:
            raise _layout_error("Rust table cell is outside its table.")
        rows[row][column] = str(cell.get("text") or "")
    source_ids = [str(value) for value in _list(table.get("source_node_ids", []), f"table[{table_id}].source_node_ids")]
    source_blocks = [nodes[item] for item in source_ids if item in nodes]
    order = min((positions[item] for item in source_ids if item in positions), default=10**9)
    text = "\n".join("\t".join("" if cell is None else cell for cell in row) for row in rows)
    metadata: dict[str, Any] = {
        "table_rows": rows,
        "source_block_ids": [source.id for source in source_blocks],
        "extraction_strategy": table.get("evidence"),
        "table_cells": table.get("cells", []),
    }
    if verbose_metadata:
        metadata.update({
            "source_type": "rust_pdf_table",
            "source_blocks": [{"id": source.id, "bbox": source.bbox, "text": source.text.raw_text if source.text else "", "source_refs": source.source_refs} for source in source_blocks],
            "rust_pdf_table_id": table_id, "rust_pdf_source_node_ids": source_ids,
            "structure_object": table.get("structure_object"), "rule_id": table.get("rule_id"),
            "provenance": table.get("provenance"), "coordinate_space": _SPACE,
        })
    else:
        _present(metadata, "rust_pdf_trace", _trace(table.get("provenance"), table.get("rule_id")))
        _present(metadata, "structure_object", table.get("structure_object"))
    block = BlockIR(
        id=f"rust_{table_id}", type=BlockType.TABLE, page_number=page_number,
        text=TextContent(text), bbox=_bbox(table.get("bbox"), f"table[{table_id}].bbox"),
        confidence=_confidence(table.get("confidence")),
        source_refs=[ref for source in source_blocks for ref in source.source_refs] or [f"rust-pdf:table:{page_number}:{table_id}"],
        metadata=metadata,
    )
    return order, block


def _image_assets(rust_pdf: Any, data: bytes, store: AssetStore | None) -> tuple[dict[tuple[int, int, int], dict[str, Any]], dict[tuple[int, str], dict[str, Any]], str | None]:
    by_object: dict[tuple[int, int, int], dict[str, Any]] = {}
    by_resource: dict[tuple[int, str], dict[str, Any]] = {}
    if store is None:
        return by_object, by_resource, None
    try:
        for index, raw in enumerate(rust_pdf.extract_images(data), 1):
            image = _dict(raw, f"extracted_images[{index}]")
            page_index, resource = int(image.get("page_index", 0)), str(image.get("resource_name") or f"image_{index}")
            extension = "jpg" if image.get("format") == "jpeg" else "bin"
            asset_ref = store.write_bytes(f"images/page_{page_index + 1:04d}_{safe_asset_name(resource)}.{extension}", bytes(image.get("data") or []))
            item = {key: image.get(key) for key in ("width", "height", "bits_per_component", "color_space", "filter", "format", "warnings")}
            item["asset_ref"] = asset_ref
            by_resource[(page_index, resource)] = item
            key = _object_key(image.get("object_id"))
            if key is not None:
                by_object[(page_index, key[0], key[1])] = item
        return by_object, by_resource, None
    except Exception as exc:
        return {}, {}, str(exc)


class RustPdfAdapter(ParserAdapter):
    """Map the optional from-scratch Rust parser result into Documa IR."""
    name = "rust_pdf"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options, source_path = options or ParseOptions(), Path(source)
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            raise DocumaError(DocumaErrorDetail(
                code="PDF_OPEN_FAILED", message=f"Unable to read PDF: {source_path}", recoverable=True,
                suggested_action="Check whether the file exists and is readable.",
                context={"source": str(source_path), "error": str(exc)},
            )) from exc
        rust_pdf = _load_rust_pdf()
        stream = None
        try:
            if hasattr(rust_pdf, "extract_layout_stream"):
                stream = rust_pdf.extract_layout_stream(
                    data,
                    normalize_unicode=False,
                    quality=True,
                    debug_glyphs=False,
                    timings=False,
                )
                root = _dict(stream.metadata, "layout_stream.metadata")
                pages = stream
                page_count = int(root.get("page_count", -1))
                streaming = _dict(root.get("streaming", {}), "layout_stream.streaming")
                page_transfer = str(streaming.get("page_transfer") or "native_events_v2")
            else:
                layout = rust_pdf.extract_layout(
                    data,
                    normalize_unicode=False,
                    quality=True,
                    debug_glyphs=False,
                    timings=False,
                )
                root = _dict(layout, "layout")
                pages = _list(root.get("pages", []), "layout.pages")
                page_count = len(pages)
                page_transfer = "whole_document_json_v1"
        except DocumaError:
            raise
        except Exception as exc:
            raise DocumaError(DocumaErrorDetail(
                code="RUST_PDF_PARSE_FAILED", message=f"Rust PDF parsing failed: {source_path}", recoverable=True,
                suggested_action="Inspect the Rust error or use the pymupdf provider.",
                context={"source": str(source_path), "error": str(exc)},
            )) from exc
        if root.get("schema_version") != _SCHEMA:
            raise _layout_error("Unsupported Rust Layout IR schema version.", expected=_SCHEMA, actual=root.get("schema_version"))
        if root.get("coordinate_space") != _SPACE:
            raise _layout_error("Unsupported Rust Layout IR coordinate space.", expected=_SPACE, actual=root.get("coordinate_space"))
        if page_count < 0:
            raise _layout_error("Rust Layout IR page count is missing or invalid.")
        parser = _dict(root.get("parser", {}), "layout.parser")
        verbose_metadata = bool(options.metadata.get("rust_pdf_include_verbose_metadata", False))
        store = AssetStore(options.asset_dir) if options.asset_dir else None
        by_object, by_resource, asset_warning = _image_assets(rust_pdf, data, store if options.extract_images else None)
        document = DocumentIR(
            id=f"doc_{uuid.uuid4().hex}", source_name=str(source_path), parser=self.name,
            adapter_version=f"rust-pdf/{parser.get('version', 'unknown')}",
            metadata={
                "page_count": page_count, "adapter": self.name, "rust_pdf_schema_version": root.get("schema_version"),
                "rust_pdf_parser": parser, "rust_pdf_options_digest": root.get("options_digest"),
                "rust_pdf_capabilities": root.get("capabilities", {}), "rust_pdf_warnings": root.get("warnings", []),
                "named_destinations": root.get("named_destinations", []), "toc": root.get("outlines", []),
                "coordinate_space": _SPACE, "page_transfer": page_transfer,
                "preview_renderer": "unavailable_in_rust_pdf_adapter",
            },
        )
        document.metadata["rust_pdf_metadata_profile"] = "verbose_v1" if verbose_metadata else "compact_trace_v1"
        document.metadata["rust_pdf_include_verbose_metadata"] = verbose_metadata
        if not verbose_metadata:
            document.metadata["rust_pdf_trace_schema"] = {
                "version": 1,
                "fields": [
                    "source_ordinal_start", "source_ordinal_end", "mcids",
                    "text_origins", "rule_id",
                ],
                "page_object": "page.metadata.page_object",
                "coordinate_space": "document.metadata.coordinate_space",
            }
        if asset_warning:
            document.metadata["rust_pdf_image_asset_warning"] = asset_warning
        include_decorative_images = bool(options.metadata.get("rust_pdf_include_decorative_images", False))
        for page_index, raw_page in enumerate(_iter_rust_pages(pages, source_path)):
            page = _dict(raw_page, f"pages[{page_index}]")
            page_number = int(page.get("page_number", page_index + 1))
            geometry = _dict(page.get("geometry", {}), f"pages[{page_index}].geometry")
            bounds = _bbox(geometry.get("layout_bounds"), f"pages[{page_index}].geometry.layout_bounds")
            if bounds is None:
                raise _layout_error("Rust page layout bounds are missing.")
            orders = _dict(page.get("orders", {}), f"pages[{page_index}].orders")
            page_metadata = {
                "coordinate_space": _SPACE, "page_object": page.get("object"),
                "rust_pdf_orders": orders, "links": page.get("links", []),
                "reading_order_locked": True,
                "reading_order_provider": "rust_pdf_inferred_order_v1",
            }
            if verbose_metadata:
                page_metadata.update({
                    "rust_pdf_main_flow_ids": orders.get("main_flow", []),
                    "geometry": geometry,
                    "preview_unavailable": "rust_pdf_has_no_page_renderer",
                })
            page_ir = PageIR(
                id=f"page_{page_number}", page_number=page_number,
                width=bounds[2] - bounds[0], height=bounds[3] - bounds[1], rotation=int(geometry.get("rotation", 0)),
                metadata=page_metadata,
            )
            nodes = [_dict(value, f"pages[{page_index}].semantic_node") for value in _list(page.get("semantic_nodes", []), f"pages[{page_index}].semantic_nodes")]
            node_ids = [str(node.get("id") or "") for node in nodes]
            if any(not item for item in node_ids) or len(node_ids) != len(set(node_ids)):
                raise _layout_error("Rust page semantic node ids must be non-empty and unique.")
            node_values, node_blocks = dict(zip(node_ids, nodes)), {}
            for node_id, node in node_values.items():
                node_blocks[node_id] = _node_block(node, page_number, options, verbose_metadata)
            ordered_ids = _best_order(page, node_ids)
            positions = {node_id: index for index, node_id in enumerate(ordered_ids)}
            tables = [_dict(value, f"pages[{page_index}].table") for value in _list(page.get("tables", []), f"pages[{page_index}].tables")]
            table_sources = {str(source_id) for table in tables for source_id in _list(table.get("source_node_ids", []), "table.source_node_ids")}
            entries = [(positions[node_id], 1, node_blocks[node_id]) for node_id in ordered_ids if node_id not in table_sources]
            for table in tables:
                order, block = _table_block(table, page_number, node_blocks, positions, verbose_metadata)
                entries.append((order, 0, block))
            entries.sort(key=lambda item: (item[0], item[1], item[2].id))
            page_ir.blocks = [item[2] for item in entries]
            for order_index, block in enumerate(page_ir.blocks, 1):
                block.order_index = order_index
                if verbose_metadata:
                    block.metadata["reading_order"] = {
                        "strategy": "rust_pdf_inferred_order_v1",
                        "coordinate_space": _SPACE,
                    }
            suppressed_decorative_images = 0
            if options.extract_images:
                for raw in _list(page.get("image_placements", []), f"pages[{page_index}].image_placements"):
                    placement = _dict(raw, f"pages[{page_index}].image_placement")
                    placement_id = str(placement.get("id") or f"image_{len(page_ir.images) + 1}")
                    box = _bbox(placement.get("bbox"), f"image_placement[{placement_id}].bbox")
                    key = _object_key(placement.get("object"))
                    asset = by_object.get((page_index, key[0], key[1])) if key is not None else None
                    asset = asset or by_resource.get((page_index, str(placement.get("resource_name") or ""))) or {}
                    source_ids = [str(value) for value in _list(placement.get("source_node_ids", []), f"image_placement[{placement_id}].source_node_ids")]
                    caption = next((str(node_values[item].get("text") or "") for item in source_ids if item in node_values and node_values[item].get("role") == "caption"), None)
                    reason = decorative_image_reason(bbox=box, page_width=page_ir.width, page_height=page_ir.height, intrinsic_width=asset.get("width"), intrinsic_height=asset.get("height"))
                    author_figure = placement.get("tag") == "Figure" or bool(placement.get("alt_text") or source_ids)
                    decorative = bool(placement.get("artifact")) or (reason is not None and not author_figure)
                    if decorative and not include_decorative_images:
                        suppressed_decorative_images += 1
                        continue
                    page_ir.images.append(ImageIR(
                        id=f"rust_{placement_id}", page_number=page_number, bbox=box,
                        asset_ref=str(asset.get("asset_ref") or f"rust-pdf://page/{page_number}/placement/{placement.get('paint_ordinal', 0)}"),
                        image_type="decorative" if decorative else "image", caption=caption,
                        confidence=_confidence(placement.get("confidence")),
                        metadata={
                            "source": "rust_pdf_image_placement", "rust_pdf_placement_id": placement_id,
                            "paint_ordinal": placement.get("paint_ordinal"), "resource_name": placement.get("resource_name"),
                            "object": placement.get("object"), "quad": placement.get("quad"), "source_node_ids": source_ids,
                            "source_block_ids": [f"rust_{item}" for item in source_ids], "tag": placement.get("tag"),
                            "artifact": bool(placement.get("artifact")), "alt_text": placement.get("alt_text"),
                            "structure_object": placement.get("structure_object"), "caption_candidate": caption,
                            "decorative": decorative, "decorative_reason": reason, "rule_id": placement.get("rule_id"),
                            "provenance": placement.get("provenance"), "coordinate_space": _SPACE,
                            **{name: value for name, value in asset.items() if name != "asset_ref"},
                        },
                    ))
            page_ir.metadata["decorative_images"] = sum(image.image_type == "decorative" for image in page_ir.images)
            page_ir.metadata["decorative_image_placements_suppressed"] = suppressed_decorative_images
            document.pages.append(page_ir)
        if stream is not None:
            root = _dict(stream.metadata, "layout_stream.final_metadata")
            if hasattr(stream, "finalizations"):
                finalizations = stream.finalizations()
            else:
                finalizations = _list(root.get("page_finalizations", []), "layout.page_finalizations")
            for finalization in _iter_rust_pages(finalizations, source_path):
                _apply_stream_finalization(document, finalization)
            document.metadata.update({
                "rust_pdf_capabilities": root.get("capabilities", {}),
                "rust_pdf_warnings": root.get("warnings", []),
                "named_destinations": root.get("named_destinations", []),
                "toc": root.get("outlines", []),
                "page_transfer": _dict(root.get("streaming", {}), "layout_stream.streaming").get("page_transfer", page_transfer),
            })
        document.metadata["decorative_image_placements_suppressed"] = sum(
            int(page.metadata.get("decorative_image_placements_suppressed", 0)) for page in document.pages
        )
        document.metadata["rust_pdf_include_decorative_images"] = include_decorative_images
        if len(document.pages) != page_count:
            raise _layout_error("Rust Layout IR page stream ended before the declared page count.", expected=page_count, actual=len(document.pages))
        return document