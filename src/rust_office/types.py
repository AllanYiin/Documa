"""Public typing helpers; unrecognized options are rejected at runtime."""
from typing import Any, Literal, TypedDict, Union

class ParseLimits(TypedDict, total=False):
    max_input_bytes: int
    max_parts: int
    max_part_bytes: int
    max_uncompressed_bytes: int
    max_compression_ratio: int
    max_cells: int
    max_shapes: int
    max_text_chars: int
    max_xml_depth: int

class ParseOptions(TypedDict, total=False):
    extract_images: bool
    include_hidden: bool
    revision_mode: Literal["final", "original", "all"]
    formula_mode: Literal["formula_and_cached_value", "formula", "cached_value"]
    external_links: Literal["metadata_only", "ignore"]
    asset_mode: Literal["inline", "metadata"]
    sheets: list[str]
    cell_range: str
    batch_rows: int
    sparse: bool
    limits: ParseLimits

class DocumentStart(TypedDict):
    event: Literal["document_start"]
    schema_version: int
    format: str
    coordinate_space: str
    source_name: str
    source_hash: str
    metadata: dict[str, Any]
    warnings: list[dict[str, Any]]
    unit_count: int

class UnitEvent(TypedDict):
    event: Literal["unit"]
    unit: dict[str, Any]

class AssetEvent(TypedDict):
    event: Literal["asset"]
    asset: dict[str, Any]

class DocumentEnd(TypedDict):
    event: Literal["document_end"]
    status: Literal["ok"]

OfficeEvent = Union[DocumentStart, UnitEvent, AssetEvent, DocumentEnd]
