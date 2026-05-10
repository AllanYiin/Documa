"""Pipeline stage contracts for document understanding."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from documa.core.ir import DocumentIR


@dataclass(slots=True)
class PipelineContext:
    project_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class StageResult:
    document: DocumentIR
    stage_name: str
    changed: bool = False
    report: dict[str, Any] = field(default_factory=dict)


class PipelineStage(ABC):
    """A single, testable document understanding stage."""

    name: str

    @abstractmethod
    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        """Run a stage and return the updated document plus a report."""

