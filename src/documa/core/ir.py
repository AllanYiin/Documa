"""Documa intermediate representation.

The IR is parser-neutral: adapter-specific objects must be converted into these
dataclasses before entering the understanding pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

from documa.core.language import LanguageHint
from documa.core.text_normalization import DEFAULT_UNICODE_FORM, normalize_unicode

BBox = tuple[float, float, float, float]


class SerializableEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BlockType(SerializableEnum):
    TEXT = "text"
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE = "table"
    IMAGE = "image"
    CHART = "chart"
    FOOTNOTE = "footnote"
    TOC = "table_of_content"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    UNKNOWN = "unknown"


class DocumentBlockType(SerializableEnum):
    DOCUMENT = "document"
    SECTION = "section"
    PAGE = "page"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    FOOTNOTE = "footnote"
    TOC = "table_of_content"
    METADATA = "metadata"
    UNKNOWN = "unknown"


class SpanStyle(SerializableEnum):
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    SUPERSCRIPT = "superscript"
    SUBSCRIPT = "subscript"
    EMPHASIS = "emphasis"


class RelationType(SerializableEnum):
    FOOTNOTE_MARKER_TO_BODY = "footnote_marker_to_body"
    TOC_ITEM_TO_HEADING = "toc_item_to_heading"
    CAPTION_TO_IMAGE = "caption_to_image"
    CAPTION_TO_TABLE = "caption_to_table"
    CHUNK_TO_SOURCE = "chunk_to_source"
    BLOCK_PARENT_CHILD = "block_parent_child"
    BLOCK_TO_SOURCE = "block_to_source"
    NEXT_IN_READING_ORDER = "next_in_reading_order"
    UNRESOLVED = "unresolved"


class RelationState(SerializableEnum):
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class Confidence(SerializableEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class FixtureIssueType(SerializableEnum):
    INLINE_SUPERSCRIPT_AND_STYLE = "inline_superscript_and_style"
    HUMAN_READING_ORDER = "human_reading_order"
    NON_TEXT_OBJECT_UNDERSTANDING = "non_text_object_understanding"
    PARAGRAPH_GROUPING = "paragraph_grouping"
    TABLE_STRUCTURE = "table_structure"
    RAG_RLM_CHUNKING = "rag_rlm_chunking"
    FOOTNOTE_LINKING = "footnote_linking"
    TOC_LINKING = "toc_linking"
    IMAGE_CHART_ASSET_EXTRACTION = "image_chart_asset_extraction"


@dataclass(slots=True)
class TextContent:
    raw_text: str
    normalized_text: str | None = None
    unicode_form: str = DEFAULT_UNICODE_FORM
    width_normalized: bool = False
    simplified_traditional_converted: bool = False

    def __post_init__(self) -> None:
        if self.normalized_text is None:
            self.normalized_text = normalize_unicode(self.raw_text, self.unicode_form)


@dataclass(slots=True)
class SpanIR:
    id: str
    text: TextContent
    bbox: BBox | None = None
    font_size: float | None = None
    font_name: str | None = None
    style: list[SpanStyle] = field(default_factory=list)
    language: LanguageHint = field(default_factory=LanguageHint)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BlockIR:
    id: str
    type: BlockType
    page_number: int
    text: TextContent | None = None
    bbox: BBox | None = None
    spans: list[SpanIR] = field(default_factory=list)
    confidence: Confidence = Confidence.UNKNOWN
    order_index: int | None = None
    source_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TableIR:
    id: str
    block_id: str
    rows: list[list[str | None]] = field(default_factory=list)
    html: str | None = None
    markdown: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageIR:
    id: str
    page_number: int
    bbox: BBox | None
    asset_ref: str
    image_type: str = "image"
    caption: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RelationIR:
    id: str
    type: RelationType
    from_id: str
    to_id: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    state: RelationState = RelationState.INFERRED
    evidence: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChunkIR:
    id: str
    text: TextContent
    source_block_ids: list[str]
    parent_block_id: str | None = None
    heading_path: list[str] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    bbox_refs: list[BBox] = field(default_factory=list)
    asset_refs: list[str] = field(default_factory=list)
    relation_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PageIR:
    id: str
    page_number: int
    width: float
    height: float
    rotation: int = 0
    blocks: list[BlockIR] = field(default_factory=list)
    images: list[ImageIR] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentBlockIR:
    id: str
    type: DocumentBlockType
    title: str | None = None
    parent_id: str | None = None
    child_ids: list[str] = field(default_factory=list)
    source_block_ids: list[str] = field(default_factory=list)
    source_chunk_ids: list[str] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    bbox_refs: list[BBox] = field(default_factory=list)
    text_preview: str | None = None
    content_hash: str | None = None
    depth: int = 0
    order_index: int | None = None
    confidence: Confidence = Confidence.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentIR:
    id: str
    source_name: str
    # ir_version follows semver semantics: minor bumps are strictly additive.
    ir_version: str = "0.2"
    parser: str | None = None
    producer_version: str | None = None
    adapter_version: str | None = None
    pipeline_profile: str | None = None
    pages: list[PageIR] = field(default_factory=list)
    tables: list[TableIR] = field(default_factory=list)
    relations: list[RelationIR] = field(default_factory=list)
    document_blocks: list[DocumentBlockIR] = field(default_factory=list)
    chunks: list[ChunkIR] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def to_plain_data(value: Any) -> Any:
    """Convert IR dataclasses and enums into JSON-serializable data."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: to_plain_data(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain_data(item) for item in value]
    return value
