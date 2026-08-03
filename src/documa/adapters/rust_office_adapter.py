"""Rust Office event-stream adapter for Documa IR."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.adapters.native_binding import (
    NativeBindingSpec,
    load_native_binding,
    native_exception_to_documa,
)
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    ImageIR,
    PageIR,
    SpanIR,
    SpanStyle,
    TableIR,
    TextContent,
)
from documa.storage.assets import AssetStore, safe_asset_name


EXPECTED_PACKAGE_VERSION = "0.1.0"
EXPECTED_LAYOUT_CONTRACT = "office-layout-v1"
_RUST_OFFICE_BINDING = NativeBindingSpec(
    parser_id="rust_office",
    module_name="rust_office",
    identity_labels=("version", "contract"),
    expected_identity=(EXPECTED_PACKAGE_VERSION, EXPECTED_LAYOUT_CONTRACT),
    required_calls=("open", "capabilities"),
    not_installed_code="RUST_OFFICE_NOT_INSTALLED",
    incompatible_code="RUST_OFFICE_INCOMPATIBLE_VERSION",
    suggested_action=(
        "Reinstall Documa with its bundled Rust parser extensions, or select "
        "office_provider='python' for DOCX/PPTX."
    ),
)
_FALLBACK_CODES = {
    "RUST_OFFICE_NOT_INSTALLED",
    "RUST_OFFICE_INCOMPATIBLE_VERSION",
    "RUST_OFFICE_CAPABILITY_UNAVAILABLE",
}


def _load_rust_office() -> tuple[Any, str]:
    binding = load_native_binding(_RUST_OFFICE_BINDING)
    return binding.module, binding.identity["version"]


def _native_error(exc: Exception, source: Path) -> DocumaError:
    return native_exception_to_documa(
        exc,
        source=source,
        default_code="RUST_OFFICE_PARSE_FAILED",
        default_message=f"Unable to parse Office document: {source}",
        default_recoverable=False,
        suggested_action="Check that the file is valid, supported, and not encrypted.",
    )


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _layout_error(f"{field} must be an object.", field=field)
    return value


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise _layout_error(f"{field} must be an array.", field=field)
    return value


def _layout_error(message: str, **context: Any) -> DocumaError:
    return DocumaError(
        DocumaErrorDetail(
            code="RUST_OFFICE_LAYOUT_INVALID",
            message=message,
            recoverable=False,
            context=context or None,
        )
    )


def _confidence(value: Any) -> Confidence:
    return {
        "high": Confidence.HIGH,
        "medium": Confidence.MEDIUM,
        "low": Confidence.LOW,
    }.get(str(value).casefold(), Confidence.UNKNOWN)


def _block_type(value: Any) -> BlockType:
    return {
        "heading": BlockType.HEADING,
        "paragraph": BlockType.PARAGRAPH,
        "text": BlockType.TEXT,
        "table": BlockType.TABLE,
        "image": BlockType.IMAGE,
        "chart": BlockType.CHART,
        "footnote": BlockType.FOOTNOTE,
        "endnote": BlockType.FOOTNOTE,
        "header": BlockType.PAGE_HEADER,
        "footer": BlockType.PAGE_FOOTER,
        "page_header": BlockType.PAGE_HEADER,
        "page_footer": BlockType.PAGE_FOOTER,
    }.get(str(value).casefold(), BlockType.UNKNOWN)


def _span_styles(values: Any) -> list[SpanStyle]:
    aliases = {
        "b": SpanStyle.BOLD,
        "bold": SpanStyle.BOLD,
        "i": SpanStyle.ITALIC,
        "italic": SpanStyle.ITALIC,
        "u": SpanStyle.UNDERLINE,
        "underline": SpanStyle.UNDERLINE,
        "superscript": SpanStyle.SUPERSCRIPT,
        "subscript": SpanStyle.SUBSCRIPT,
        "emphasis": SpanStyle.EMPHASIS,
    }
    return [
        aliases[item]
        for item in (str(value).casefold() for value in values or [])
        if item in aliases
    ]


def _bbox(value: Any, *, visual: bool) -> tuple[float, float, float, float] | None:
    if not visual or value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise _layout_error("block.bbox must contain four coordinates.", value=value)
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise _layout_error(
            "block.bbox contains a non-numeric coordinate.", value=value
        ) from exc
    if x1 < x0 or y1 < y0:
        raise _layout_error("block.bbox has inverted coordinates.", value=value)
    return (x0, y0, x1, y1)


def _span(value: Any) -> SpanIR:
    span = _mapping(value, "span")
    text = str(span.get("text") or "")
    return SpanIR(
        id=str(span.get("id") or ""),
        text=TextContent(text),
        style=_span_styles(span.get("styles")),
        metadata=dict(span.get("metadata") or {}),
    )


def _block(value: Any, page_number: int, *, visual: bool) -> BlockIR:
    block = _mapping(value, "block")
    text = str(block.get("text") or "")
    metadata = dict(block.get("metadata") or {})
    metadata["citation_geometry"] = "visual" if visual else "structural"
    metadata["office_layout_kind"] = str(block.get("kind") or "unknown")
    source_refs = [
        str(item)
        for item in _sequence(block.get("source_refs", []), "block.source_refs")
    ]
    return BlockIR(
        id=str(block.get("id") or ""),
        type=_block_type(block.get("kind")),
        page_number=page_number,
        text=TextContent(text) if text else None,
        bbox=_bbox(block.get("bbox"), visual=visual),
        spans=[
            _span(item) for item in _sequence(block.get("spans", []), "block.spans")
        ],
        confidence=_confidence(block.get("confidence")),
        order_index=int(block.get("order_index") or 0),
        source_refs=source_refs,
        metadata=metadata,
    )


def _table(value: Any) -> TableIR:
    table = _mapping(value, "table")
    rows = []
    for row in _sequence(table.get("rows", []), "table.rows"):
        rows.append(
            [
                None if cell is None else str(cell)
                for cell in _sequence(row, "table.row")
            ]
        )
    metadata = dict(table.get("metadata") or {})
    metadata["source_refs"] = [
        str(item)
        for item in _sequence(table.get("source_refs", []), "table.source_refs")
    ]
    return TableIR(
        id=str(table.get("id") or ""),
        block_id=str(table.get("block_id") or ""),
        rows=rows,
        confidence=Confidence.HIGH,
        metadata=metadata,
    )


def _page(value: Any, coordinate_space: str) -> tuple[PageIR, list[TableIR]]:
    unit = _mapping(value, "unit")
    number = int(unit.get("number") or 1)
    visual = coordinate_space == "slide_points"
    metadata = dict(unit.get("metadata") or {})
    metadata.update(
        {
            "source": str(unit.get("kind") or "office_unit"),
            "label": str(unit.get("label") or ""),
            "coordinate_space": coordinate_space,
            "citation_geometry": "visual" if visual else "structural",
            "hidden": bool(unit.get("hidden", False)),
        }
    )
    page = PageIR(
        id=str(unit.get("id") or f"office_unit_{number}"),
        page_number=number,
        width=float(unit.get("width") or 0.0),
        height=float(unit.get("height") or 0.0),
        blocks=[
            _block(item, number, visual=visual)
            for item in _sequence(unit.get("blocks", []), "unit.blocks")
        ],
        metadata=metadata,
    )
    tables = [_table(item) for item in _sequence(unit.get("tables", []), "unit.tables")]
    return page, tables


def _asset(
    value: Any,
    document: DocumentIR,
    store: AssetStore | None,
) -> None:
    asset = _mapping(value, "asset")
    try:
        data = base64.b64decode(str(asset.get("data_base64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise _layout_error(
            "asset.data_base64 is invalid.", asset_id=asset.get("id")
        ) from exc
    sha256 = str(asset.get("sha256") or "")
    file_name = safe_asset_name(
        str(asset.get("file_name") or asset.get("id") or "asset")
    )
    asset_ref = None
    if store is not None:
        asset_ref = store.write_bytes(
            Path("office") / f"{sha256[:16]}_{file_name}", data
        )

    metadata = dict(asset.get("metadata") or {})
    metadata.update(
        {
            "id": str(asset.get("id") or ""),
            "mime_type": str(asset.get("mime_type") or "application/octet-stream"),
            "sha256": sha256,
            "source_ref": str(asset.get("source_ref") or ""),
            "alt_text": asset.get("alt_text"),
            "asset_ref": asset_ref,
        }
    )
    document.metadata.setdefault("office_assets", []).append(metadata)
    if asset_ref and document.pages:
        document.pages[0].images.append(
            ImageIR(
                id=str(
                    asset.get("id")
                    or f"office_asset_{len(document.pages[0].images) + 1}"
                ),
                page_number=document.pages[0].page_number,
                bbox=None,
                asset_ref=asset_ref,
                caption=str(asset.get("alt_text")) if asset.get("alt_text") else None,
                confidence=Confidence.HIGH,
                metadata={
                    "mime_type": metadata["mime_type"],
                    "sha256": sha256,
                    "source_ref": metadata["source_ref"],
                    "citation_geometry": "structural",
                },
            )
        )


class RustOfficeAdapter(ParserAdapter):
    """Map versioned rust_office events into Documa's existing IR."""

    name = "rust_office"

    def parse(
        self, source: str | Path, options: ParseOptions | None = None
    ) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        rust_office, package_version = _load_rust_office()
        rust_options = {
            "extract_images": options.extract_images,
            "include_hidden": bool(options.metadata.get("include_hidden", False)),
            "revision_mode": str(options.metadata.get("revision_mode", "final")),
            "formula_mode": str(
                options.metadata.get("formula_mode", "formula_and_cached_value")
            ),
            "external_links": "metadata_only",
        }
        try:
            stream = rust_office.open(source_path, rust_options)
        except Exception as exc:
            raise _native_error(exc, source_path) from exc

        document: DocumentIR | None = None
        coordinate_space = ""
        terminal_seen = False
        store = AssetStore(options.asset_dir) if options.asset_dir else None
        try:
            for raw_event in stream:
                event = _mapping(raw_event, "event")
                event_type = str(event.get("event") or "")
                if event_type == "document_start":
                    if document is not None:
                        raise _layout_error(
                            "document_start was emitted more than once."
                        )
                    if int(event.get("schema_version") or 0) != 1:
                        raise _layout_error(
                            "Unsupported Office Layout schema version.",
                            schema_version=event.get("schema_version"),
                        )
                    coordinate_space = str(event.get("coordinate_space") or "")
                    source_hash = str(event.get("source_hash") or "")
                    document = DocumentIR(
                        id=f"doc_office_{source_hash[:16]}",
                        source_name=str(source_path),
                        parser=self.name,
                        producer_version=package_version,
                        adapter_version=EXPECTED_LAYOUT_CONTRACT,
                        metadata={
                            "adapter": self.name,
                            "format": str(event.get("format") or ""),
                            "office_binding_version": package_version,
                            "coordinate_space": coordinate_space,
                            "languages": list(options.languages),
                            "source_hash": source_hash,
                            "warnings": list(event.get("warnings") or []),
                            "office_layout_metadata": dict(event.get("metadata") or {}),
                        },
                    )
                elif event_type == "unit":
                    if document is None:
                        raise _layout_error("unit was emitted before document_start.")
                    page, tables = _page(event.get("unit"), coordinate_space)
                    document.pages.append(page)
                    document.tables.extend(tables)
                elif event_type == "asset":
                    if document is None:
                        raise _layout_error("asset was emitted before document_start.")
                    _asset(event.get("asset"), document, store)
                elif event_type == "document_end":
                    terminal_seen = event.get("status") == "ok"
                else:
                    raise _layout_error(
                        "Unknown Office Layout event.", event_type=event_type
                    )
        except DocumaError:
            raise
        except Exception as exc:
            raise _layout_error(
                "Unable to map Office Layout events into Documa IR.",
                source=str(source_path),
                error=str(exc),
            ) from exc

        if document is None or not terminal_seen:
            raise _layout_error(
                "The Office Layout stream ended without a successful terminal event."
            )
        document.metadata.setdefault(
            "office_provider",
            {"requested": "rust", "actual": "rust", "fallback": False},
        )
        return document


__all__ = [
    "EXPECTED_LAYOUT_CONTRACT",
    "EXPECTED_PACKAGE_VERSION",
    "RustOfficeAdapter",
    "_FALLBACK_CODES",
]
