"""RAG JSON exporter for LLM ingestion frameworks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documa.core.ir import DocumentIR
from documa.exporters.base import ExportOptions, Exporter


@dataclass(slots=True)
class RagJsonExporter(Exporter):
    """Export chunks as stable records with traceable metadata."""

    name: str = "rag-json"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> dict[str, Any]:
        records = []
        for chunk in document.chunks:
            records.append(
                {
                    "id": chunk.id,
                    "page_content": chunk.text.normalized_text or chunk.text.raw_text,
                    "metadata": {
                        "document_id": document.id,
                        "source_name": document.source_name,
                        "source_block_ids": chunk.source_block_ids,
                        "heading_path": chunk.heading_path,
                        "page_refs": chunk.page_refs,
                        "bbox_refs": chunk.bbox_refs,
                        "asset_refs": chunk.asset_refs,
                        "relation_ids": chunk.relation_ids,
                        **chunk.metadata,
                    },
                }
            )

        return {
            "document_id": document.id,
            "source_name": document.source_name,
            "chunk_count": len(records),
            "chunks": records,
        }

