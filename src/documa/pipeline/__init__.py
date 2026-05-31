"""Documa pipeline interfaces."""

from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.block_keywords import BlockKeywordExtractionStage
from documa.pipeline.block_tree import BlockTreeBuildingStage
from documa.pipeline.captions import CaptionLinkingStage
from documa.pipeline.chunking import ChunkingStage
from documa.pipeline.footnotes import FootnoteLinkingStage
from documa.pipeline.images import ImageNormalizationStage
from documa.pipeline.inline_semantics import InlineSemanticsStage
from documa.pipeline.layout import LayoutClassificationStage
from documa.pipeline.paragraphs import ParagraphGroupingStage
from documa.pipeline.provenance import ProvenanceLinkingStage
from documa.pipeline.reading_order import ReadingOrderStage
from documa.pipeline.runner import PipelineRun, default_pipeline_stages, run_default_pipeline, run_pipeline
from documa.pipeline.tables import TableNormalizationStage
from documa.pipeline.toc import TocLinkingStage

__all__ = [
    "BlockKeywordExtractionStage",
    "BlockTreeBuildingStage",
    "CaptionLinkingStage",
    "ChunkingStage",
    "FootnoteLinkingStage",
    "ImageNormalizationStage",
    "InlineSemanticsStage",
    "LayoutClassificationStage",
    "ParagraphGroupingStage",
    "PipelineContext",
    "PipelineRun",
    "PipelineStage",
    "ProvenanceLinkingStage",
    "ReadingOrderStage",
    "StageResult",
    "TableNormalizationStage",
    "TocLinkingStage",
    "default_pipeline_stages",
    "run_default_pipeline",
    "run_pipeline",
]
