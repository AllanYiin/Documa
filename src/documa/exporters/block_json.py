"""Block tree JSON exporter for progressive disclosure workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documa.core.ir import DocumentIR, to_plain_data
from documa.exporters.base import ExportOptions, Exporter


@dataclass(slots=True)
class BlockJsonExporter(Exporter):
    name: str = "block-json"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> dict[str, Any]:
        return {
            "document_id": document.id,
            "source_name": document.source_name,
            "block_count": len(document.document_blocks),
            "blocks": [to_plain_data(block) for block in document.document_blocks],
        }
