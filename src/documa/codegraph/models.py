"""Stable contracts for Documa's repository intelligence graph."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


CODE_GRAPH_SCHEMA_VERSION = 1
CODE_GRAPH_ANALYZER_VERSION = "python-ast-v1"


class CodeNodeKind(str, Enum):
    WORKSPACE = "workspace"
    PACKAGE = "package"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    EXTERNAL_MODULE = "external_module"
    EXTERNAL_SYMBOL = "external_symbol"


class CodeEdgeType(str, Enum):
    CONTAINS = "contains"
    DEFINES = "defines"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    DECORATES = "decorates"
    EXPORTS = "exports"
    IMPORTS_MODULE = "imports_module"
    IMPORTS_SYMBOL = "imports_symbol"
    CALLS = "calls"
    CONSTRUCTS = "constructs"
    REGISTERS_CALLBACK = "registers_callback"


class EdgeResolution(str, Enum):
    EXACT = "EXACT"
    RESOLVED = "RESOLVED"
    POSSIBLE = "POSSIBLE"
    UNRESOLVED = "UNRESOLVED"


class ParseStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True, frozen=True)
class CodeSpan:
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None


@dataclass(slots=True)
class CodeNode:
    node_id: str
    kind: CodeNodeKind
    qualified_name: str
    display_name: str
    source_locator: str | None
    content_hash: str | None
    span: CodeSpan | None = None
    file_id: str | None = None
    parent_id: str | None = None
    signature: str | None = None
    docstring: str | None = None
    summary: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodeOccurrence:
    occurrence_id: str
    file_id: str
    source_node_id: str
    role: str
    span: CodeSpan
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodeEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    type: CodeEdgeType
    resolution: EdgeResolution
    resolver: str
    evidence_occurrence_id: str | None = None
    evidence_file_id: str | None = None
    evidence_span: CodeSpan | None = None
    evidence_hash: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedCodeFile:
    file_id: str
    relative_path: str
    language: str
    digest: str
    parse_status: ParseStatus
    nodes: list[CodeNode] = field(default_factory=list)
    occurrences: list[CodeOccurrence] = field(default_factory=list)
    structural_edges: list[CodeEdge] = field(default_factory=list)
    blindspots: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class CodeLanguageAdapter(Protocol):
    """Language adapter boundary; adapters parse but never mutate the store."""

    name: str
    version: str

    def supports(self, path: str) -> bool: ...

    def parse(self, root: str, source_root: str, path: str, workspace_id: str) -> ParsedCodeFile: ...


class CodeSummaryEnricher(Protocol):
    """Optional derived-summary provider; it cannot emit nodes or edges."""

    name: str
    version: str

    def summarize(self, node: dict[str, Any]) -> str | None: ...


def sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def edge_id(
    source_node_id: str,
    target_node_id: str,
    edge_type: CodeEdgeType,
    occurrence_id: str | None = None,
) -> str:
    return stable_id("ce", source_node_id, target_node_id, edge_type.value, occurrence_id or "")
