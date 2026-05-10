"""Layout classification stage for Documa blocks."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


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


@dataclass(slots=True)
class LayoutClassificationStage(PipelineStage):
    """Classify high-level layout roles without discarding source evidence."""

    name: str = "layout_classification"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        changed_count = 0
        report_pages: list[dict[str, int]] = []

        for page in document.pages:
            font_sizes = [
                size
                for block in page.blocks
                for size in [_block_font_size(block)]
                if size is not None and block.type in {BlockType.TEXT, BlockType.PARAGRAPH}
            ]
            median_font = float(median(font_sizes)) if font_sizes else 0.0
            page_counts = {"heading": 0, "page_header": 0, "page_footer": 0}

            for block in page.blocks:
                if block.bbox is None or block.type not in {BlockType.TEXT, BlockType.PARAGRAPH}:
                    continue
                original_type = block.type
                block_font = _block_font_size(block) or median_font
                y0, y1 = block.bbox[1], block.bbox[3]

                if y0 <= page.height * 0.06:
                    block.type = BlockType.PAGE_HEADER
                    page_counts["page_header"] += 1
                elif y1 >= page.height * 0.94:
                    block.type = BlockType.PAGE_FOOTER
                    page_counts["page_footer"] += 1
                elif median_font and block_font >= median_font * 1.25 and _is_short_heading_candidate(block):
                    block.type = BlockType.HEADING
                    page_counts["heading"] += 1

                if block.type != original_type:
                    block.metadata.setdefault("layout_classification", {})
                    block.metadata["layout_classification"].update(
                        {
                            "previous_type": original_type.value,
                            "strategy": "font_size_and_page_position",
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

