"""Footnote marker to body relation stage."""

from __future__ import annotations

import re
from dataclasses import dataclass

from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    RelationIR,
    RelationState,
    RelationType,
    SpanStyle,
)
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.relations import append_relation_once, block_text, iter_blocks


_FOOTNOTE_PREFIX = re.compile(r"^\s*(?P<marker>[\d０-９A-Za-zivxlcdmIVXLCDM]{1,6})\s*[\.\)\]）:：、]?\s+")
_MARKER_TEXT = re.compile(r"^[\d０-９A-Za-zivxlcdmIVXLCDM]{1,6}$")


def _span_marker(block: BlockIR) -> str | None:
    explicit = block.metadata.get("footnote_marker")
    if explicit:
        return str(explicit).strip()
    for span in block.spans:
        if SpanStyle.SUPERSCRIPT in span.style:
            marker = (span.text.normalized_text or span.text.raw_text).strip()
            if _MARKER_TEXT.match(marker):
                return marker
    return None


def _body_marker(block: BlockIR) -> str | None:
    explicit = block.metadata.get("footnote_body_marker")
    if explicit:
        return str(explicit).strip()
    text = block_text(block)
    match = _FOOTNOTE_PREFIX.match(text)
    if match:
        return match.group("marker")
    return None


@dataclass(slots=True)
class FootnoteLinkingStage(PipelineStage):
    """Link superscript or explicit footnote markers to footnote bodies."""

    name: str = "footnote_linking"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        bodies_by_marker: dict[str, BlockIR] = {}
        markers: list[tuple[BlockIR, str]] = []

        for block in iter_blocks(document):
            marker = _span_marker(block)
            if marker:
                markers.append((block, marker))
            if block.type == BlockType.FOOTNOTE or block.metadata.get("footnote_body_marker"):
                body_marker = _body_marker(block)
                if body_marker and body_marker not in bodies_by_marker:
                    bodies_by_marker[body_marker] = block

        created = 0
        unresolved = 0
        for index, (marker_block, marker) in enumerate(markers, start=1):
            target = bodies_by_marker.get(marker)
            relation_type = RelationType.FOOTNOTE_MARKER_TO_BODY if target else RelationType.UNRESOLVED
            relation = RelationIR(
                id=f"rel_footnote_{marker_block.id}_{index}",
                type=relation_type,
                from_id=marker_block.id,
                to_id=target.id if target else None,
                confidence=Confidence.MEDIUM if target else Confidence.LOW,
                state=RelationState.INFERRED if target else RelationState.UNRESOLVED,
                evidence=[
                    f"marker={marker}",
                    "source=span_superscript_or_metadata",
                    "target=footnote_block_prefix" if target else "target_missing",
                ],
                metadata={"marker": marker, "stage": self.name},
            )
            if append_relation_once(document, relation):
                created += 1
                if target is None:
                    unresolved += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=created > 0,
            report={"relations_created": created, "unresolved": unresolved},
        )
