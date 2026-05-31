"""Serialization helpers for Documa IR."""

from __future__ import annotations

from typing import Any, TypeVar

from documa.core.ir import (
    BlockIR,
    BlockType,
    ChunkIR,
    Confidence,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    ImageIR,
    PageIR,
    RelationIR,
    RelationState,
    RelationType,
    SpanIR,
    SpanStyle,
    TableIR,
    TextContent,
)
from documa.core.language import LanguageHint


EnumT = TypeVar("EnumT")


def _coerce_enum(enum_cls: type[EnumT], value: Any, default: EnumT) -> EnumT:
    if value is None:
        return default
    if isinstance(value, enum_cls):
        return value
    return enum_cls(value)


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _text(value: Any) -> TextContent | None:
    if value is None:
        return None
    if isinstance(value, TextContent):
        return value
    if isinstance(value, str):
        return TextContent(value)
    return TextContent(
        raw_text=str(value.get("raw_text", "")),
        normalized_text=value.get("normalized_text"),
        unicode_form=value.get("unicode_form", "NFC"),
        width_normalized=bool(value.get("width_normalized", False)),
        simplified_traditional_converted=bool(value.get("simplified_traditional_converted", False)),
    )


def _language(value: Any) -> LanguageHint:
    if isinstance(value, LanguageHint):
        return value
    if isinstance(value, dict):
        return LanguageHint(
            language=value.get("language", "auto"),
            script=value.get("script"),
            text_direction=value.get("text_direction", "ltr"),
        )
    return LanguageHint()


def span_from_plain_data(data: dict[str, Any]) -> SpanIR:
    return SpanIR(
        id=str(data["id"]),
        text=_text(data.get("text")) or TextContent(""),
        bbox=_bbox(data.get("bbox")),
        font_size=None if data.get("font_size") is None else float(data["font_size"]),
        font_name=data.get("font_name"),
        style=[_coerce_enum(SpanStyle, item, SpanStyle.EMPHASIS) for item in data.get("style", [])],
        language=_language(data.get("language")),
        metadata=dict(data.get("metadata", {})),
    )


def block_from_plain_data(data: dict[str, Any]) -> BlockIR:
    return BlockIR(
        id=str(data["id"]),
        type=_coerce_enum(BlockType, data.get("type"), BlockType.UNKNOWN),
        page_number=int(data.get("page_number", 0)),
        text=_text(data.get("text")),
        bbox=_bbox(data.get("bbox")),
        spans=[span_from_plain_data(item) for item in data.get("spans", [])],
        confidence=_coerce_enum(Confidence, data.get("confidence"), Confidence.UNKNOWN),
        order_index=data.get("order_index"),
        source_refs=[str(item) for item in data.get("source_refs", [])],
        metadata=dict(data.get("metadata", {})),
    )


def table_from_plain_data(data: dict[str, Any]) -> TableIR:
    return TableIR(
        id=str(data["id"]),
        block_id=str(data["block_id"]),
        rows=[[None if cell is None else str(cell) for cell in row] for row in data.get("rows", [])],
        html=data.get("html"),
        markdown=data.get("markdown"),
        confidence=_coerce_enum(Confidence, data.get("confidence"), Confidence.UNKNOWN),
        metadata=dict(data.get("metadata", {})),
    )


def image_from_plain_data(data: dict[str, Any]) -> ImageIR:
    return ImageIR(
        id=str(data["id"]),
        page_number=int(data.get("page_number", 0)),
        bbox=_bbox(data.get("bbox")),
        asset_ref=str(data.get("asset_ref", "")),
        image_type=str(data.get("image_type", "image")),
        caption=data.get("caption"),
        confidence=_coerce_enum(Confidence, data.get("confidence"), Confidence.UNKNOWN),
        metadata=dict(data.get("metadata", {})),
    )


def relation_from_plain_data(data: dict[str, Any]) -> RelationIR:
    return RelationIR(
        id=str(data["id"]),
        type=_coerce_enum(RelationType, data.get("type"), RelationType.UNRESOLVED),
        from_id=str(data["from_id"]),
        to_id=None if data.get("to_id") is None else str(data["to_id"]),
        confidence=_coerce_enum(Confidence, data.get("confidence"), Confidence.UNKNOWN),
        state=_coerce_enum(RelationState, data.get("state"), RelationState.INFERRED),
        evidence=[str(item) for item in data.get("evidence", [])],
        metadata=dict(data.get("metadata", {})),
    )


def chunk_from_plain_data(data: dict[str, Any]) -> ChunkIR:
    return ChunkIR(
        id=str(data["id"]),
        text=_text(data.get("text")) or TextContent(""),
        source_block_ids=[str(item) for item in data.get("source_block_ids", [])],
        parent_block_id=None if data.get("parent_block_id") is None else str(data.get("parent_block_id")),
        heading_path=[str(item) for item in data.get("heading_path", [])],
        page_refs=[int(item) for item in data.get("page_refs", [])],
        bbox_refs=[bbox for item in data.get("bbox_refs", []) if (bbox := _bbox(item)) is not None],
        asset_refs=[str(item) for item in data.get("asset_refs", [])],
        relation_ids=[str(item) for item in data.get("relation_ids", [])],
        metadata=dict(data.get("metadata", {})),
    )


def document_block_from_plain_data(data: dict[str, Any]) -> DocumentBlockIR:
    return DocumentBlockIR(
        id=str(data["id"]),
        type=_coerce_enum(DocumentBlockType, data.get("type"), DocumentBlockType.UNKNOWN),
        title=None if data.get("title") is None else str(data.get("title")),
        parent_id=None if data.get("parent_id") is None else str(data.get("parent_id")),
        child_ids=[str(item) for item in data.get("child_ids", [])],
        source_block_ids=[str(item) for item in data.get("source_block_ids", [])],
        source_chunk_ids=[str(item) for item in data.get("source_chunk_ids", [])],
        page_refs=[int(item) for item in data.get("page_refs", [])],
        bbox_refs=[bbox for item in data.get("bbox_refs", []) if (bbox := _bbox(item)) is not None],
        text_preview=data.get("text_preview"),
        content_hash=data.get("content_hash"),
        depth=int(data.get("depth", 0)),
        order_index=data.get("order_index"),
        confidence=_coerce_enum(Confidence, data.get("confidence"), Confidence.UNKNOWN),
        metadata=dict(data.get("metadata", {})),
    )


def page_from_plain_data(data: dict[str, Any]) -> PageIR:
    return PageIR(
        id=str(data["id"]),
        page_number=int(data.get("page_number", 0)),
        width=float(data.get("width", 0)),
        height=float(data.get("height", 0)),
        rotation=int(data.get("rotation", 0)),
        blocks=[block_from_plain_data(item) for item in data.get("blocks", [])],
        images=[image_from_plain_data(item) for item in data.get("images", [])],
        metadata=dict(data.get("metadata", {})),
    )


def document_from_plain_data(data: dict[str, Any]) -> DocumentIR:
    return DocumentIR(
        id=str(data["id"]),
        source_name=str(data.get("source_name", "")),
        ir_version=str(data.get("ir_version", "0.1")),
        parser=data.get("parser"),
        pages=[page_from_plain_data(item) for item in data.get("pages", [])],
        tables=[table_from_plain_data(item) for item in data.get("tables", [])],
        relations=[relation_from_plain_data(item) for item in data.get("relations", [])],
        document_blocks=[document_block_from_plain_data(item) for item in data.get("document_blocks", [])],
        chunks=[chunk_from_plain_data(item) for item in data.get("chunks", [])],
        metadata=dict(data.get("metadata", {})),
    )
