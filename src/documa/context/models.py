"""Stable cross-source context contracts for graph-guided evidence retrieval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


CONTEXT_IR_VERSION = "1.0"


class ContextSourceKind(str, Enum):
    DOCUMENT = "document"
    CODE = "code"
    SKILL = "skill"


class ContextAuthority(str, Enum):
    HOST = "host"
    DEVELOPER = "developer"
    USER = "user"
    SKILL = "skill"
    TOOL = "tool"
    DERIVED = "derived"


class RelationOrigin(str, Enum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(slots=True)
class SourceSpan:
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None


@dataclass(slots=True)
class ContextBlock:
    block_id: str
    source_id: str
    source_kind: ContextSourceKind
    title: str
    body: str
    source_locator: str
    content_hash: str
    authority: ContextAuthority = ContextAuthority.DERIVED
    parent_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    route_tags: list[str] = field(default_factory=list)
    source_span: SourceSpan | None = None
    pinned: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContextRelation:
    source_block_id: str
    target_block_id: str
    type: str
    origin: RelationOrigin = RelationOrigin.EXTRACTED
    confidence: float | None = None
    evidence_block_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContextIR:
    context_id: str
    source_kind: ContextSourceKind
    source_digest: str
    blocks: list[ContextBlock]
    relations: list[ContextRelation] = field(default_factory=list)
    schema_version: str = CONTEXT_IR_VERSION
    source_authority: str = "filesystem-fallback"
    metadata: dict[str, Any] = field(default_factory=dict)


def sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _span_payload(span: SourceSpan | None) -> dict[str, int] | None:
    if span is None:
        return None
    return {
        "startLine": span.start_line,
        "endLine": span.end_line,
        **({"startColumn": span.start_column} if span.start_column is not None else {}),
        **({"endColumn": span.end_column} if span.end_column is not None else {}),
    }


def context_ir_to_plain_data(context: ContextIR) -> dict[str, Any]:
    """Emit the shared camelCase wire contract consumed by HarnessFold."""

    return {
        "schemaVersion": context.schema_version,
        "contextId": context.context_id,
        "sourceKind": context.source_kind.value,
        "sourceAuthority": context.source_authority,
        "sourceTreeHash": context.source_digest,
        "blocks": [
            {
                "blockId": block.block_id,
                "sourceId": block.source_id,
                "sourceKind": block.source_kind.value,
                "title": block.title,
                "body": block.body,
                "sourceLocator": block.source_locator,
                "contentHash": block.content_hash,
                "authority": block.authority.value,
                **({"parentId": block.parent_id} if block.parent_id else {}),
                "dependsOn": list(block.depends_on),
                "routeTags": list(block.route_tags),
                **({"sourceSpan": _span_payload(block.source_span)} if block.source_span else {}),
                "pinned": block.pinned,
                "metadata": block.metadata,
            }
            for block in context.blocks
        ],
        "relations": [
            {
                "sourceBlockId": relation.source_block_id,
                "targetBlockId": relation.target_block_id,
                "type": relation.type,
                "origin": relation.origin.value,
                **({"confidence": relation.confidence} if relation.confidence is not None else {}),
                "evidenceBlockIds": list(relation.evidence_block_ids),
                "metadata": relation.metadata,
            }
            for relation in context.relations
        ],
        "metadata": context.metadata,
    }


def _value(data: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    return data[camel] if camel in data else data.get(snake, default)


def context_ir_from_plain_data(data: dict[str, Any]) -> ContextIR:
    blocks: list[ContextBlock] = []
    for item in data.get("blocks", []):
        span_data = _value(item, "sourceSpan", "source_span")
        span = None
        if span_data:
            span = SourceSpan(
                start_line=int(_value(span_data, "startLine", "start_line")),
                end_line=int(_value(span_data, "endLine", "end_line")),
                start_column=_value(span_data, "startColumn", "start_column"),
                end_column=_value(span_data, "endColumn", "end_column"),
            )
        blocks.append(
            ContextBlock(
                block_id=str(_value(item, "blockId", "block_id")),
                source_id=str(_value(item, "sourceId", "source_id", _value(data, "contextId", "context_id", ""))),
                source_kind=ContextSourceKind(_value(item, "sourceKind", "source_kind", _value(data, "sourceKind", "source_kind"))),
                title=str(item.get("title") or ""),
                body=str(item.get("body") or ""),
                source_locator=str(_value(item, "sourceLocator", "source_locator", "")),
                content_hash=str(_value(item, "contentHash", "content_hash")),
                authority=ContextAuthority(item.get("authority", "derived")),
                parent_id=_value(item, "parentId", "parent_id"),
                depends_on=[str(value) for value in _value(item, "dependsOn", "depends_on", [])],
                route_tags=[str(value) for value in _value(item, "routeTags", "route_tags", [])],
                source_span=span,
                pinned=bool(item.get("pinned", False)),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    relations = [
        ContextRelation(
            source_block_id=str(_value(item, "sourceBlockId", "source_block_id")),
            target_block_id=str(_value(item, "targetBlockId", "target_block_id")),
            type=str(item.get("type") or "related_to"),
            origin=RelationOrigin(item.get("origin", "EXTRACTED")),
            confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            evidence_block_ids=[str(value) for value in _value(item, "evidenceBlockIds", "evidence_block_ids", [])],
            metadata=dict(item.get("metadata") or {}),
        )
        for item in data.get("relations", [])
    ]
    return ContextIR(
        context_id=str(_value(data, "contextId", "context_id")),
        source_kind=ContextSourceKind(_value(data, "sourceKind", "source_kind")),
        source_digest=str(_value(data, "sourceTreeHash", "source_digest")),
        blocks=blocks,
        relations=relations,
        schema_version=str(_value(data, "schemaVersion", "schema_version", CONTEXT_IR_VERSION)),
        source_authority=str(_value(data, "sourceAuthority", "source_authority", "filesystem-fallback")),
        metadata=dict(data.get("metadata") or {}),
    )
