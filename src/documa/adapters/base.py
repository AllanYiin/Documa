"""Base interface for parser adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from documa.core.ir import DocumentIR


@dataclass(slots=True)
class ParseOptions:
    languages: list[str] = field(default_factory=lambda: ["auto"])
    normalize_unicode: bool = True
    extract_images: bool = True
    resolve_relations: bool = True
    asset_dir: Path | None = None
    preview_scale: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


class ParserAdapter(ABC):
    """Parser-neutral adapter contract."""

    name: str

    @abstractmethod
    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        """Parse a source document into Documa IR primitives."""
