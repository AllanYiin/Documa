"""Base exporter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from documa.core.ir import DocumentIR


@dataclass(slots=True)
class ExportOptions:
    output_dir: Path | None = None
    include_bbox: bool = True
    include_images: bool = True
    include_relations: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class Exporter(ABC):
    """Exporter interface for JSON, Markdown, chunks, and assets."""

    name: str

    @abstractmethod
    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> Any:
        """Export a document into a target representation."""

