"""Reading order resolver for Documa IR blocks."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from documa.core.ir import BlockIR, Confidence, DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


def _block_center_x(block: BlockIR) -> float:
    assert block.bbox is not None
    return (block.bbox[0] + block.bbox[2]) / 2


def _column_intervals(blocks: list[BlockIR], page_width: float) -> list[tuple[float, float]]:
    """Detect simple visual columns by x interval gaps.

    This is a conservative fallback, not a universal layout model. PyMuPDF docs
    explicitly note that natural reading order may require custom column logic.
    """

    intervals = sorted((block.bbox[0], block.bbox[2]) for block in blocks if block.bbox)
    if not intervals:
        return []

    gap_threshold = max(page_width * 0.08, 24.0)
    merged: list[tuple[float, float]] = []
    start, end = intervals[0]
    for x0, x1 in intervals[1:]:
        if x0 - end > gap_threshold:
            merged.append((start, end))
            start, end = x0, x1
        else:
            end = max(end, x1)
    merged.append((start, end))
    return merged


def _column_index(block: BlockIR, columns: list[tuple[float, float]], page_width: float) -> int:
    if not block.bbox or not columns:
        return len(columns)
    width = block.bbox[2] - block.bbox[0]
    if len(columns) > 1 and width > page_width * 0.65:
        return -1
    center = _block_center_x(block)
    distances = [abs(center - ((x0 + x1) / 2)) for x0, x1 in columns]
    return min(range(len(distances)), key=distances.__getitem__)


@dataclass(slots=True)
class ReadingOrderStage(PipelineStage):
    """Assign human-oriented reading order indexes to page blocks."""

    name: str = "reading_order"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        changed = False
        report_pages: list[dict[str, int]] = []

        for page in document.pages:
            sortable = [block for block in page.blocks if block.bbox is not None]
            if not sortable:
                continue

            columns = _column_intervals(sortable, page.width)
            column_count = max(len(columns), 1)
            original_ids = [block.id for block in page.blocks]

            def sort_key(block: BlockIR) -> tuple[int, float, float, int]:
                if block.bbox is None:
                    return (column_count + 1, float("inf"), float("inf"), block.order_index or 0)
                column = _column_index(block, columns, page.width)
                return (column, block.bbox[1], block.bbox[0], block.order_index or 0)

            page.blocks = sorted(page.blocks, key=sort_key)
            for index, block in enumerate(page.blocks, start=1):
                if block.metadata.get("original_order_index") is None:
                    block.metadata["original_order_index"] = block.order_index
                block.order_index = index
                if block.bbox is not None:
                    block.metadata["reading_order"] = {
                        "column_index": _column_index(block, columns, page.width),
                        "strategy": "simple_column_gap",
                    }
                if block.confidence == Confidence.UNKNOWN:
                    block.confidence = Confidence.MEDIUM

            changed = changed or original_ids != [block.id for block in page.blocks]
            report_pages.append(
                {
                    "page_number": page.page_number,
                    "block_count": len(page.blocks),
                    "column_count": column_count,
                }
            )

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed,
            report={"pages": report_pages},
        )


def median_text_block_height(blocks: list[BlockIR]) -> float:
    heights = [block.bbox[3] - block.bbox[1] for block in blocks if block.bbox]
    return float(median(heights)) if heights else 0.0

