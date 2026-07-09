"""Reading order resolver: zone/column ordering with a per-block trace (v2).

Gestalt-informed, fully deterministic, zero-ML. The algorithm follows the
XY-Cut++ family (mask cross-layout elements, then cut):

1. Spanner detection (figure/ground): blocks whose width covers most of the
   local content width (default ratio 0.65), plus page headers/footers.
2. Vertical banding (common region): spanners split the unit into bands.
   Banding recurses (depth-limited) so a band-local wide block cannot mask
   the gutters of the columns around it.
3. Gutter/column split (proximity): gaps >= min_gutter in the union of block
   x-intervals become column separators; by construction no block crosses one.
4. Column-first ordering (continuity): bands top-down, columns left-to-right,
   blocks by (y0, x0) within a column.

Every block records why it landed where it did
(``metadata["reading_order"]``: zone_id / column_index / rule / gestalt) and
each page records the zone/gutter map (``metadata["reading_order_trace"]``)
so the quality benchmark can localize ordering mistakes. Blocks without a
bbox cannot be placed visually and are appended last with rule
``fallback_row_major``.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult

STRATEGY = "zone_column_v2"
DEFAULT_SPANNER_RATIO = 0.65
DEFAULT_MIN_GUTTER = 12.0
_MAX_BAND_DEPTH = 2

_SPANNER_TYPES = {BlockType.PAGE_HEADER, BlockType.PAGE_FOOTER}


@dataclass(slots=True)
class _Placement:
    block: BlockIR
    zone_id: str
    column_index: int | None
    rule: str
    gestalt: list[str] = field(default_factory=list)


def _content_bounds(blocks: list[BlockIR]) -> tuple[float, float]:
    x0 = min(block.bbox[0] for block in blocks)
    x1 = max(block.bbox[2] for block in blocks)
    return x0, x1


def _y_center(block: BlockIR) -> float:
    return (block.bbox[1] + block.bbox[3]) / 2


def _detect_gutters(blocks: list[BlockIR], min_gutter: float) -> list[tuple[float, float]]:
    """Gaps >= min_gutter in the union of x-intervals; nothing crosses them."""
    intervals = sorted((block.bbox[0], block.bbox[2]) for block in blocks)
    merged: list[tuple[float, float]] = []
    start, end = intervals[0]
    for x0, x1 in intervals[1:]:
        if x0 - end >= min_gutter:
            merged.append((start, end))
            start, end = x0, x1
        else:
            end = max(end, x1)
    merged.append((start, end))
    return [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]


def _column_index_for(block: BlockIR, gutters: list[tuple[float, float]]) -> int:
    center = (block.bbox[0] + block.bbox[2]) / 2
    return bisect_right([g[0] for g in gutters], center)


def _sides_coexist_vertically(blocks: list[BlockIR], gutter: tuple[float, float]) -> bool:
    """A gutter only separates columns if content on both sides overlaps in y.

    Two x-separated blocks at different heights (e.g. a footnote continuation
    above a section heading) are sequential text, not columns.
    """
    g0, g1 = gutter
    left = [b for b in blocks if (b.bbox[0] + b.bbox[2]) / 2 < g0]
    right = [b for b in blocks if (b.bbox[0] + b.bbox[2]) / 2 > g1]
    if not left or not right:
        return False
    left_y0, left_y1 = min(b.bbox[1] for b in left), max(b.bbox[3] for b in left)
    right_y0, right_y1 = min(b.bbox[1] for b in right), max(b.bbox[3] for b in right)
    return min(left_y1, right_y1) - max(left_y0, right_y0) > 0


def _columns_span_band_height(
    rest: list[BlockIR], gutters: list[tuple[float, float]], min_coverage: float = 0.5
) -> bool:
    """True when every revealed gutter separates real text columns.

    Real columns extend over most of the band height on BOTH sides of the
    gutter; table cell grids also produce x-gaps, but their cells only cover
    the table's own small y-range, so they fail this check.
    """
    band_y0 = min(b.bbox[1] for b in rest)
    band_y1 = max(b.bbox[3] for b in rest)
    band_height = max(band_y1 - band_y0, 1.0)
    for g0, g1 in gutters:
        for side in (
            [b for b in rest if (b.bbox[0] + b.bbox[2]) / 2 < g0],
            [b for b in rest if (b.bbox[0] + b.bbox[2]) / 2 > g1],
        ):
            if not side:
                return False
            side_extent = max(b.bbox[3] for b in side) - min(b.bbox[1] for b in side)
            if side_extent / band_height < min_coverage:
                return False
    return True


def _looks_like_grid(blocks: list[BlockIR], gutters: list[tuple[float, float]]) -> bool:
    """Distinguish a table cell grid from side-by-side text columns.

    Table cells align in rows across columns (matching y-centers); paragraph
    boundaries in real text columns are independent. Grids are read row-major
    (Gestalt: similarity), text columns column-first (continuity).
    """
    if len(blocks) < 6 or not gutters:
        return False
    columns: dict[int, list[BlockIR]] = {}
    for block in blocks:
        columns.setdefault(_column_index_for(block, gutters), []).append(block)
    if len(columns) < 2:
        return False
    aligned = 0
    for index, members in columns.items():
        others = [b for i, ms in columns.items() if i != index for b in ms]
        for block in members:
            center = _y_center(block)
            tolerance = max((block.bbox[3] - block.bbox[1]) * 0.5, 2.0)
            if any(abs(center - _y_center(other)) <= tolerance for other in others):
                aligned += 1
    return aligned / len(blocks) >= 0.5


def _gutter_crossing_spanners(
    blocks: list[BlockIR], min_gutter: float, max_mask: int = 3
) -> list[BlockIR]:
    """XY-Cut++-style masking: widest blocks that hide a gutter they cross.

    A near-full-width title sits below the spanner ratio yet still bridges the
    column gap, gluing both columns into one x-interval. Masking the widest
    block(s) reveals the gutter; if every masked block crosses it AND the
    revealed columns span most of the band height (not just a table's cell
    grid), the masked blocks are spanners in disguise.
    """
    if len(blocks) < 4:
        return []
    by_width = sorted(blocks, key=lambda b: (b.bbox[2] - b.bbox[0], b.id), reverse=True)
    for k in range(1, min(max_mask, len(blocks) - 3) + 1):
        masked = by_width[:k]
        rest = by_width[k:]
        gutters = _detect_gutters(rest, min_gutter)
        if (
            gutters
            and all(
                any(b.bbox[0] < g0 and b.bbox[2] > g1 for g0, g1 in gutters) for b in masked
            )
            and _columns_span_band_height(rest, gutters)
        ):
            return masked
    return []


def _order_unit(
    blocks: list[BlockIR],
    zone_id: str,
    depth: int,
    spanner_ratio: float,
    min_gutter: float,
    zones: list[dict[str, Any]],
    gutters_out: list[dict[str, Any]],
) -> list[_Placement]:
    """Recursively order one vertical unit (a page or a band)."""
    if not blocks:
        return []

    content_x0, content_x1 = _content_bounds(blocks)
    content_width = max(content_x1 - content_x0, 1.0)
    direct_gutters = [
        g for g in _detect_gutters(blocks, min_gutter) if _sides_coexist_vertically(blocks, g)
    ]

    def crosses_a_gutter(block: BlockIR) -> bool:
        return any(block.bbox[0] < g0 and block.bbox[2] > g1 for g0, g1 in direct_gutters)

    type_spanners = [block for block in blocks if block.type in _SPANNER_TYPES]
    ratio_spanners = [
        block
        for block in blocks
        if block.type not in _SPANNER_TYPES
        and (block.bbox[2] - block.bbox[0]) >= spanner_ratio * content_width
    ]
    if direct_gutters:
        # Columns are already visible: a wide block only outranks them when it
        # actually bridges a gutter (true cross-layout element). A wide main
        # column next to a full-height sidebar must NOT band the sidebar apart.
        ratio_spanners = [block for block in ratio_spanners if crosses_a_gutter(block)]
    spanners = sorted(
        type_spanners + ratio_spanners, key=lambda block: (block.bbox[1], block.bbox[0])
    )
    if not spanners and not direct_gutters:
        spanners = sorted(
            _gutter_crossing_spanners(blocks, min_gutter),
            key=lambda block: (block.bbox[1], block.bbox[0]),
        )

    # Recurse on bands only when spanners genuinely split remaining content.
    if spanners and depth < _MAX_BAND_DEPTH and len(spanners) < len(blocks):
        spanner_ids = {block.id for block in spanners}
        content = [block for block in blocks if block.id not in spanner_ids]
        boundaries = [_y_center(block) for block in spanners]
        bands: list[list[BlockIR]] = [[] for _ in range(len(spanners) + 1)]
        for block in content:
            bands[bisect_right(boundaries, _y_center(block))].append(block)

        placements: list[_Placement] = []
        for index in range(len(spanners) + 1):
            placements.extend(
                _order_unit(
                    bands[index], f"{zone_id}b{index + 1}", depth + 1,
                    spanner_ratio, min_gutter, zones, gutters_out,
                )
            )
            if index < len(spanners):
                spanner = spanners[index]
                placements.append(
                    _Placement(spanner, zone_id, None, "spanner", ["figure/ground", "common region"])
                )
        # Placements must interleave by vertical position: a spanner sits after
        # the band above it, so re-emit in band order (already correct) — but
        # the band above a top spanner may be empty; ordering is positional.
        zones.append(
            {
                "zone_id": zone_id,
                "kind": "banded",
                "y0": round(min(block.bbox[1] for block in blocks), 2),
                "y1": round(max(block.bbox[3] for block in blocks), 2),
                "spanner_count": len(spanners),
                "band_count": len(bands),
                "block_count": len(blocks),
            }
        )
        return placements

    gutters = direct_gutters
    column_count = len(gutters) + 1
    is_grid = _looks_like_grid(blocks, gutters)
    if is_grid:
        rule = "grid_row_major"
        ordered = sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
    else:
        rule = "column_flow" if column_count > 1 else "single_column"
        ordered = sorted(
            blocks,
            key=lambda block: (_column_index_for(block, gutters), block.bbox[1], block.bbox[0]),
        )
    spanner_ids = {block.id for block in spanners}
    zones.append(
        {
            "zone_id": zone_id,
            "kind": "grid" if is_grid else "content",
            "y0": round(min(block.bbox[1] for block in blocks), 2),
            "y1": round(max(block.bbox[3] for block in blocks), 2),
            "column_count": column_count,
            "block_count": len(blocks),
        }
    )
    for gutter_x0, gutter_x1 in gutters:
        gutters_out.append({"zone_id": zone_id, "x0": round(gutter_x0, 2), "x1": round(gutter_x1, 2)})
    if is_grid:
        gestalt = ["similarity", "proximity"]
    elif column_count > 1:
        gestalt = ["proximity", "continuity"]
    else:
        gestalt = ["proximity"]
    return [
        _Placement(
            block,
            zone_id,
            None if block.id in spanner_ids else _column_index_for(block, gutters),
            "spanner" if block.id in spanner_ids else rule,
            ["figure/ground"] if block.id in spanner_ids else gestalt,
        )
        for block in ordered
    ]


@dataclass(slots=True)
class ReadingOrderStage(PipelineStage):
    """Assign human-oriented reading order indexes to page blocks."""

    name: str = "reading_order"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        settings = context.settings if context else {}
        spanner_ratio = float(settings.get("reading_order_spanner_ratio", DEFAULT_SPANNER_RATIO))
        min_gutter = float(settings.get("reading_order_min_gutter", DEFAULT_MIN_GUTTER))

        changed = False
        report_pages: list[dict[str, Any]] = []

        for page in document.pages:
            if not page.blocks:
                continue
            sortable = [block for block in page.blocks if block.bbox is not None]
            unsortable = [block for block in page.blocks if block.bbox is None]
            original_ids = [block.id for block in page.blocks]

            zones: list[dict[str, Any]] = []
            gutters: list[dict[str, Any]] = []
            placements = _order_unit(
                sortable, "z1", 0, spanner_ratio, min_gutter, zones, gutters
            ) if sortable else []
            placements.extend(
                _Placement(block, "z1", None, "fallback_row_major", []) for block in unsortable
            )

            page.blocks = [placement.block for placement in placements]
            for index, placement in enumerate(placements, start=1):
                block = placement.block
                if block.metadata.get("original_order_index") is None:
                    block.metadata["original_order_index"] = block.order_index
                block.order_index = index
                block.metadata["reading_order"] = {
                    "strategy": STRATEGY,
                    "zone_id": placement.zone_id,
                    "column_index": placement.column_index,
                    "rule": placement.rule,
                    "gestalt": placement.gestalt,
                }
                if block.confidence == Confidence.UNKNOWN:
                    block.confidence = Confidence.MEDIUM

            page.metadata["reading_order_trace"] = {"zones": zones, "gutters": gutters}
            changed = changed or original_ids != [block.id for block in page.blocks]
            report_pages.append(
                {
                    "page_number": page.page_number,
                    "block_count": len(page.blocks),
                    "zone_count": len(zones),
                    "column_count_max": max(
                        (zone.get("column_count", 1) for zone in zones), default=1
                    ),
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
