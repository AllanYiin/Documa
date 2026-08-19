"""Stable data contracts for Documa's deterministic skill loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from documa.core.ir import TextContent


SKILL_IR_VERSION = "1.0"


class SkillBlockRole(str, Enum):
    IDENTITY = "identity"
    SCOPE = "scope"
    GUARDRAIL = "guardrail"
    WORKFLOW = "workflow"
    STEP = "step"
    EXAMPLE = "example"
    REFERENCE = "reference"
    TROUBLESHOOTING = "troubleshooting"
    TEST = "test"
    CONTENT = "content"


class SkillEdgeType(str, Enum):
    PARENT = "parent"
    NEXT = "next"
    REFERENCES_RESOURCE = "references_resource"
    REQUIRES_BLOCK = "requires_block"
    REQUIRES_SKILL = "requires_skill"


@dataclass(slots=True)
class SkillBlockIR:
    id: str
    resource_path: str
    kind: str
    role: SkillBlockRole
    text: TextContent
    title: str | None = None
    parent_id: str | None = None
    heading_path: list[str] = field(default_factory=list)
    depth: int = 0
    order_index: int = 0
    line_start: int = 0
    line_end: int = 0
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillResourceIR:
    path: str
    kind: str
    media_type: str
    sha256: str
    size: int
    text_indexed: bool = False
    block_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SkillEdgeIR:
    type: SkillEdgeType
    from_id: str
    to_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillIR:
    skill_id: str
    qualified_name: str
    name: str
    description: str
    generation: str
    source_digest: str
    source_root_id: str
    source_path: str
    ir_version: str = SKILL_IR_VERSION
    frontmatter: dict[str, Any] = field(default_factory=dict)
    blocks: list[SkillBlockIR] = field(default_factory=list)
    resources: list[SkillResourceIR] = field(default_factory=list)
    edges: list[SkillEdgeIR] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillRoot:
    id: str
    path: str
    priority: int = 0
    enabled: bool = True
    trusted: bool = True
    allow_native_scan_overlap: bool = False


@dataclass(slots=True)
class SkillSyncResult:
    status: str
    discovered: int = 0
    compiled: int = 0
    unchanged: int = 0
    missing: int = 0
    quarantined: int = 0
    index_rebuilt: bool = False
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class SkillBundle:
    status: str
    task: str
    code: str | None = None
    selected_skills: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    graph_trace: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    rendered_skill_md: str | None = None
