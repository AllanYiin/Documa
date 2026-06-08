"""Layout classification stage for Documa blocks."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from collections import Counter
from statistics import median

from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR
from documa.core.text_normalization import normalize_for_search
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult

_SPACES = re.compile(r"\s+")
_REPEATED_REGION_RATIO = 0.12
_MIN_REPEATED_PAGES = 3


def _block_font_size(block: BlockIR) -> float | None:
    sizes = [span.font_size for span in block.spans if span.font_size is not None]
    if not sizes:
        return None
    return float(median(sizes))


def _is_short_heading_candidate(block: BlockIR) -> bool:
    if not block.text:
        return False
    text = block.text.raw_text.strip()
    return 0 < len(text) <= 120 and "\n" not in text


def _repeat_key(block: BlockIR) -> str:
    if not block.text:
        return ""
    text = normalize_for_search(block.text.raw_text)
    return _SPACES.sub(" ", text).strip()


def _repeated_region_keys(document: DocumentIR, *, top: bool) -> set[str]:
    """Find repeated top/bottom furniture text across pages.

    Positional rules catch very high headers. This catches the common second
    header line that is slightly lower but repeated on many pages.
    """

    page_count = len(document.pages)
    if page_count < _MIN_REPEATED_PAGES:
        return set()

    min_pages = max(_MIN_REPEATED_PAGES, math.ceil(page_count * 0.2))
    counts: Counter[str] = Counter()
    for page in document.pages:
        page_keys: set[str] = set()
        threshold = page.height * _REPEATED_REGION_RATIO
        for block in page.blocks:
            if block.bbox is None or block.type not in {BlockType.TEXT, BlockType.PARAGRAPH}:
                continue
            y0, y1 = block.bbox[1], block.bbox[3]
            in_region = y0 <= threshold if top else y1 >= page.height - threshold
            if not in_region:
                continue
            key = _repeat_key(block)
            if 0 < len(key) <= 220:
                page_keys.add(key)
        counts.update(page_keys)

    return {key for key, count in counts.items() if count >= min_pages}


@dataclass(slots=True)
class LayoutClassificationStage(PipelineStage):
    """Classify high-level layout roles without discarding source evidence."""

    name: str = "layout_classification"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        changed_count = 0
        report_pages: list[dict[str, int]] = []
        repeated_headers = _repeated_region_keys(document, top=True)
        repeated_footers = _repeated_region_keys(document, top=False)

        for page in document.pages:
            body_font_sizes = [
                size
                for block in page.blocks
                for size in [_block_font_size(block)]
                if size is not None
                and block.type in {BlockType.TEXT, BlockType.PARAGRAPH}
                and block.bbox is not None
                and block.bbox[1] > page.height * _REPEATED_REGION_RATIO
                and block.bbox[3] < page.height * 0.82
            ]
            all_font_sizes = [
                size
                for block in page.blocks
                for size in [_block_font_size(block)]
                if size is not None and block.type in {BlockType.TEXT, BlockType.PARAGRAPH}
            ]
            font_sizes = body_font_sizes or all_font_sizes
            median_font = float(median(font_sizes)) if font_sizes else 0.0
            page_counts = {"heading": 0, "page_header": 0, "page_footer": 0}

            for block in page.blocks:
                if block.bbox is None or block.type not in {BlockType.TEXT, BlockType.PARAGRAPH}:
                    continue
                original_type = block.type
                block_font = _block_font_size(block) or median_font
                y0, y1 = block.bbox[1], block.bbox[3]
                repeat_key = _repeat_key(block)
                strategy = None

                if y0 <= page.height * 0.06:
                    block.type = BlockType.PAGE_HEADER
                    page_counts["page_header"] += 1
                    strategy = "font_size_and_page_position"
                elif repeat_key in repeated_headers:
                    block.type = BlockType.PAGE_HEADER
                    page_counts["page_header"] += 1
                    strategy = "repeated_top_text"
                elif y1 >= page.height * 0.94:
                    block.type = BlockType.PAGE_FOOTER
                    page_counts["page_footer"] += 1
                    strategy = "font_size_and_page_position"
                elif repeat_key in repeated_footers:
                    block.type = BlockType.PAGE_FOOTER
                    page_counts["page_footer"] += 1
                    strategy = "repeated_bottom_text"
                elif median_font and block_font >= median_font * 1.25 and _is_short_heading_candidate(block):
                    block.type = BlockType.HEADING
                    page_counts["heading"] += 1
                    strategy = "font_size_and_page_position"

                if block.type != original_type:
                    block.metadata.setdefault("layout_classification", {})
                    block.metadata["layout_classification"].update(
                        {
                            "previous_type": original_type.value,
                            "strategy": strategy or "font_size_and_page_position",
                            "median_font_size": median_font,
                            "block_font_size": block_font,
                        }
                    )
                    if block.confidence == Confidence.UNKNOWN:
                        block.confidence = Confidence.MEDIUM
                    changed_count += 1

            report_pages.append({"page_number": page.page_number, **page_counts})

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed_count > 0,
            report={"blocks_classified": changed_count, "pages": report_pages},
        )
