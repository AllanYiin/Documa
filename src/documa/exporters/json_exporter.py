"""JSON exporter for Documa IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documa.core.ir import DocumentIR, to_plain_data
from documa.exporters.base import ExportOptions, Exporter


@dataclass(slots=True)
class JsonExporter(Exporter):
    name: str = "json"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> dict[str, Any]:
        return to_plain_data(document)

