"""Pipeline orchestration for end-to-end Documa processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from documa.core.ir import DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.captions import CaptionLinkingStage
from documa.pipeline.chunking import ChunkingStage
from documa.pipeline.footnotes import FootnoteLinkingStage
from documa.pipeline.images import ImageNormalizationStage
from documa.pipeline.inline_semantics import InlineSemanticsStage
from documa.pipeline.layout import LayoutClassificationStage
from documa.pipeline.paragraphs import ParagraphGroupingStage
from documa.pipeline.provenance import ProvenanceLinkingStage
from documa.pipeline.reading_order import ReadingOrderStage
from documa.pipeline.tables import TableNormalizationStage
from documa.pipeline.toc import TocLinkingStage


@dataclass(slots=True)
class PipelineRun:
    document: DocumentIR
    stage_results: list[StageResult] = field(default_factory=list)

    def report(self) -> dict:
        return {
            "stage_count": len(self.stage_results),
            "stages": [
                {
                    "stage_name": result.stage_name,
                    "changed": result.changed,
                    "report": result.report,
                }
                for result in self.stage_results
            ],
        }


def default_pipeline_stages(*, include_chunking: bool = True) -> list[PipelineStage]:
    stages: list[PipelineStage] = [
        ReadingOrderStage(),
        InlineSemanticsStage(),
        LayoutClassificationStage(),
        ParagraphGroupingStage(),
        TableNormalizationStage(),
        ImageNormalizationStage(),
        FootnoteLinkingStage(),
        TocLinkingStage(),
        CaptionLinkingStage(),
    ]
    if include_chunking:
        stages.extend([ChunkingStage(), ProvenanceLinkingStage()])
    return stages


def run_pipeline(
    document: DocumentIR,
    stages: Iterable[PipelineStage],
    context: PipelineContext | None = None,
) -> PipelineRun:
    context = context or PipelineContext()
    results: list[StageResult] = []
    current = document
    for stage in stages:
        result = stage.run(current, context)
        current = result.document
        results.append(result)
    return PipelineRun(document=current, stage_results=results)


def run_default_pipeline(
    document: DocumentIR,
    context: PipelineContext | None = None,
    *,
    include_chunking: bool = True,
) -> PipelineRun:
    return run_pipeline(document, default_pipeline_stages(include_chunking=include_chunking), context)

