"""Caption relation stage for figures, images, charts, and tables."""

from __future__ import annotations

import re
from dataclasses import dataclass

from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    ImageIR,
    RelationIR,
    RelationState,
    RelationType,
)
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.relations import append_relation_once, block_text


_FIGURE_PREFIX = re.compile(r"^\s*(figure|fig\.?|圖|圖表)\s*[\d０-９A-Za-z一二三四五六七八九十]*\s*[:：.\-]?", re.I)
_TABLE_PREFIX = re.compile(r"^\s*(table|表)\s*[\d０-９A-Za-z一二三四五六七八九十]*\s*[:：.\-]?", re.I)


def _caption_kind(block: BlockIR) -> str | None:
    explicit = block.metadata.get("caption_kind")
    if explicit in {"image", "table"}:
        return str(explicit)
    text = block_text(block)
    if _TABLE_PREFIX.match(text):
        return "table"
    if _FIGURE_PREFIX.match(text):
        return "image"
    return None


def _bbox_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax = (a[0] + a[2]) / 2
    ay = (a[1] + a[3]) / 2
    bx = (b[0] + b[2]) / 2
    by = (b[1] + b[3]) / 2
    return abs(ax - bx) + abs(ay - by)


def _nearest_image(caption: BlockIR, images: list[ImageIR]) -> ImageIR | None:
    if caption.bbox is None:
        return images[0] if len(images) == 1 else None
    candidates = [image for image in images if image.bbox is not None]
    if not candidates:
        return images[0] if len(images) == 1 else None
    return min(candidates, key=lambda image: _bbox_distance(caption.bbox, image.bbox))  # type: ignore[arg-type]


def _nearest_table_block(caption: BlockIR, table_blocks: list[BlockIR]) -> BlockIR | None:
    if caption.bbox is None:
        return table_blocks[0] if len(table_blocks) == 1 else None
    candidates = [block for block in table_blocks if block.bbox is not None]
    if not candidates:
        return table_blocks[0] if len(table_blocks) == 1 else None
    return min(candidates, key=lambda block: _bbox_distance(caption.bbox, block.bbox))  # type: ignore[arg-type]


@dataclass(slots=True)
class CaptionLinkingStage(PipelineStage):
    """Link caption-like blocks to nearby image or table candidates."""

    name: str = "caption_linking"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        table_block_ids = {table.block_id for table in document.tables}
        created = 0
        unresolved = 0

        for page in document.pages:
            table_blocks = [
                block
                for block in page.blocks
                if block.type == BlockType.TABLE or block.id in table_block_ids or block.metadata.get("table_id")
            ]
            for block in page.blocks:
                kind = _caption_kind(block)
                if kind is None:
                    continue

                if kind == "table":
                    target = _nearest_table_block(block, table_blocks)
                    relation_type = RelationType.CAPTION_TO_TABLE if target else RelationType.UNRESOLVED
                    to_id = target.id if target else None
                else:
                    target_image = _nearest_image(block, page.images)
                    relation_type = RelationType.CAPTION_TO_IMAGE if target_image else RelationType.UNRESOLVED
                    to_id = target_image.id if target_image else None

                relation = RelationIR(
                    id=f"rel_caption_{block.id}",
                    type=relation_type,
                    from_id=block.id,
                    to_id=to_id,
                    confidence=Confidence.MEDIUM if to_id else Confidence.LOW,
                    state=RelationState.INFERRED if to_id else RelationState.UNRESOLVED,
                    evidence=[
                        f"caption_prefix={kind}",
                        "nearest_same_page_candidate" if to_id else "target_missing",
                    ],
                    metadata={
                        "caption_kind": kind,
                        "stage": self.name,
                        "page_number": page.page_number,
                    },
                )
                if append_relation_once(document, relation):
                    created += 1
                    if to_id is None:
                        unresolved += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=created > 0,
            report={"relations_created": created, "unresolved": unresolved},
        )

