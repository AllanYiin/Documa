"""Paragraph grouping for ordered text blocks."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, TextContent
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.reading_order import median_text_block_height


CJK_TERMINAL_PUNCTUATION = "。！？：；"
LATIN_TERMINAL_PUNCTUATION = ".!?:;"


def _x_overlap_ratio(left: BlockIR, right: BlockIR) -> float:
    if not left.bbox or not right.bbox:
        return 0.0
    overlap = max(0.0, min(left.bbox[2], right.bbox[2]) - max(left.bbox[0], right.bbox[0]))
    base = max(1.0, min(left.bbox[2] - left.bbox[0], right.bbox[2] - right.bbox[0]))
    return overlap / base


def _vertical_gap(left: BlockIR, right: BlockIR) -> float:
    if not left.bbox or not right.bbox:
        return float("inf")
    return right.bbox[1] - left.bbox[3]


def _bbox_union(blocks: list[BlockIR]) -> tuple[float, float, float, float] | None:
    boxes = [block.bbox for block in blocks if block.bbox]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _join_text(previous: str, current: str) -> str:
    previous = previous.rstrip()
    current = current.lstrip()
    if not previous:
        return current
    if previous.endswith("-") and current[:1].isascii():
        return previous[:-1] + current
    if previous[-1:] in CJK_TERMINAL_PUNCTUATION:
        return previous + current
    if current[:1] and ("\u3400" <= current[0] <= "\u9fff"):
        return previous + current
    if previous[-1:] and ("\u3400" <= previous[-1] <= "\u9fff"):
        return previous + current
    return previous + " " + current


def _can_merge(previous: BlockIR, current: BlockIR, gap_threshold: float) -> bool:
    if previous.type != BlockType.TEXT or current.type != BlockType.TEXT:
        return False
    if not previous.text or not current.text:
        return False
    if previous.bbox is None or current.bbox is None:
        return False
    if _vertical_gap(previous, current) < -2:
        return False
    if _vertical_gap(previous, current) > gap_threshold:
        return False
    return _x_overlap_ratio(previous, current) >= 0.45


def _make_paragraph(page_number: int, index: int, blocks: list[BlockIR]) -> BlockIR:
    text = ""
    spans = []
    source_refs: list[str] = []
    for block in blocks:
        if block.text:
            text = _join_text(text, block.text.raw_text)
        spans.extend(block.spans)
        source_refs.extend(block.source_refs or [block.id])

    return BlockIR(
        id=f"p{page_number}_para{index}",
        type=BlockType.PARAGRAPH,
        page_number=page_number,
        text=TextContent(text),
        bbox=_bbox_union(blocks),
        spans=spans,
        confidence=Confidence.MEDIUM,
        order_index=index,
        source_refs=source_refs,
        metadata={
            "source_block_ids": [block.id for block in blocks],
            "strategy": "vertical_gap_x_overlap",
        },
    )


@dataclass(slots=True)
class ParagraphGroupingStage(PipelineStage):
    """Group adjacent text blocks into paragraph blocks."""

    name: str = "paragraph_grouping"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        changed = False
        report_pages: list[dict[str, int]] = []

        for page in document.pages:
            text_blocks = [block for block in page.blocks if block.type == BlockType.TEXT and block.bbox]
            if not text_blocks:
                continue
            gap_threshold = max(median_text_block_height(text_blocks) * 1.8, 18.0)
            groups: list[list[BlockIR]] = []
            active: list[BlockIR] = []

            for block in sorted(page.blocks, key=lambda item: item.order_index or 0):
                if block.type != BlockType.TEXT:
                    if active:
                        groups.append(active)
                        active = []
                    groups.append([block])
                    continue
                if active and _can_merge(active[-1], block, gap_threshold):
                    active.append(block)
                else:
                    if active:
                        groups.append(active)
                    active = [block]
            if active:
                groups.append(active)

            new_blocks: list[BlockIR] = []
            paragraph_count = 0
            for group in groups:
                if len(group) > 1 and all(block.type == BlockType.TEXT for block in group):
                    paragraph_count += 1
                    new_blocks.append(_make_paragraph(page.page_number, paragraph_count, group))
                else:
                    new_blocks.extend(group)

            changed = changed or len(new_blocks) != len(page.blocks)
            page.blocks = new_blocks
            for index, block in enumerate(page.blocks, start=1):
                block.order_index = index

            report_pages.append(
                {
                    "page_number": page.page_number,
                    "paragraphs_created": paragraph_count,
                    "block_count": len(page.blocks),
                }
            )

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed,
            report={"pages": report_pages},
        )

