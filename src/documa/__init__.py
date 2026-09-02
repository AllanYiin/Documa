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
from documa.summarization import (
    SummaryError,
    SummaryOptions,
    SummaryResult,
    SummarySentence,
    summarize_document,
    summarize_text,
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
    "SummaryError",
    "SummaryOptions",
    "SummaryResult",
    "SummarySentence",
    "summarize_document",
    "summarize_text",
]

__version__ = "0.8.0"
