"""RAG JSON exporter for LLM ingestion frameworks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from documa.core.ir import DocumentIR
from documa.core.text_normalization import clean_retrieval_text
from documa.exporters.base import ExportOptions, Exporter
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata


@dataclass(slots=True)
class RagJsonExporter(Exporter):
    """Export chunks as stable records with traceable metadata."""

    name: str = "rag-json"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> dict[str, Any]:
        page_citations = ensure_page_citation_map(document)
        records = []
        for chunk in document.chunks:
            page_content = clean_retrieval_text(chunk.text.normalized_text or chunk.text.raw_text)
            records.append(
                {
                    "id": chunk.id,
                    "page_content": page_content,
                    "metadata": {
                        "document_id": document.id,
                        "source_name": document.source_name,
                        "source_block_ids": chunk.source_block_ids,
                        "parent_block_id": chunk.parent_block_id,
                        "heading_path": chunk.heading_path,
                        "page_refs": chunk.page_refs,
                        "bbox_refs": chunk.bbox_refs,
                        "asset_refs": chunk.asset_refs,
                        "relation_ids": chunk.relation_ids,
                        **chunk.metadata,
                        **page_citation_metadata(chunk.page_refs, page_citations),
                    },
                }
            )

        return {
            "document_id": document.id,
            "source_name": document.source_name,
            "chunk_count": len(records),
            "chunks": records,
        }
