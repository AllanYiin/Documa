"""Chunk provenance relation stage."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.ir import Confidence, DocumentIR, RelationIR, RelationType
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.relations import append_relation_once


@dataclass(slots=True)
class ProvenanceLinkingStage(PipelineStage):
    """Materialize chunk source block references as relations."""

    name: str = "provenance_linking"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        created = 0

        for chunk in document.chunks:
            for index, source_block_id in enumerate(chunk.source_block_ids, start=1):
                relation = RelationIR(
                    id=f"rel_chunk_{chunk.id}_{index}",
                    type=RelationType.CHUNK_TO_SOURCE,
                    from_id=chunk.id,
                    to_id=source_block_id,
                    confidence=Confidence.HIGH,
                    evidence=["chunk_source_block_ids", f"source_block_id={source_block_id}"],
                    metadata={"source_block_id": source_block_id, "stage": self.name},
                )
                if append_relation_once(document, relation):
                    chunk.relation_ids.append(relation.id)
                    created += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=created > 0,
            report={"relations_created": created},
        )
