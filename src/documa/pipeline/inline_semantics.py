"""Inline semantic enrichment for spans."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from documa.core.ir import DocumentIR, SpanIR, SpanStyle
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


def _span_height(span: SpanIR) -> float | None:
    if not span.bbox:
        return None
    return span.bbox[3] - span.bbox[1]


def _mark_superscripts(spans: list[SpanIR]) -> int:
    sized = [span for span in spans if span.font_size and span.bbox]
    if len(sized) < 2:
        return 0

    median_font = median(span.font_size for span in sized if span.font_size)
    median_top = median(span.bbox[1] for span in sized if span.bbox)
    changes = 0
    for span in sized:
        if span.font_size is None or span.bbox is None:
            continue
        span_height = _span_height(span) or 0.0
        is_smaller = span.font_size <= median_font * 0.78 or span_height <= median_font * 0.8
        is_raised = span.bbox[1] < median_top
        if is_smaller and is_raised and SpanStyle.SUPERSCRIPT not in span.style:
            span.style.append(SpanStyle.SUPERSCRIPT)
            span.metadata["inline_semantics"] = "superscript"
            changes += 1
    return changes


@dataclass(slots=True)
class InlineSemanticsStage(PipelineStage):
    """Add inline semantic hints such as superscript spans."""

    name: str = "inline_semantics"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        changed_count = 0
        for page in document.pages:
            for block in page.blocks:
                changed_count += _mark_superscripts(block.spans)
        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed_count > 0,
            report={"superscript_spans_marked": changed_count},
        )

