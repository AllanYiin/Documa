"""Documa core public API."""

from documa.core.ir import (
    BlockIR,
    BlockType,
    ChunkIR,
    Confidence,
    DocumentIR,
    FixtureIssueType,
    ImageIR,
    PageIR,
    RelationIR,
    RelationState,
    RelationType,
    SpanStyle,
    SpanIR,
    TableIR,
    TextContent,
)

__all__ = [
    "BlockIR",
    "BlockType",
    "ChunkIR",
    "Confidence",
    "DocumentIR",
    "FixtureIssueType",
    "ImageIR",
    "PageIR",
    "RelationIR",
    "RelationState",
    "RelationType",
    "SpanStyle",
    "SpanIR",
    "TableIR",
    "TextContent",
]

__version__ = "0.2.1"
