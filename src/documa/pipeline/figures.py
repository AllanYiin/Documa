"""Keep embedded figure text, image material and captions out of body flow."""

from statistics import median
import re

from documa.core.ir import BlockType, DocumentIR
from documa.core.text_joining import is_caption_block

_STRATEGY = "caption_image_region_v1"
_KEYS = (
    "figure_group_id",
    "figure_role",
    "figure_bbox",
    "figure_image_ids",
    "reading_region",
    "figure_region_strategy",
)


def classify_figure_regions(document: DocumentIR) -> int:
    if document.parser not in {"rust_pdf", "pymupdf"}:
        return 0
    count = 0
    for page in document.pages:
        for item in [*page.blocks, *page.images]:
            if item.metadata.get("figure_region_strategy") == _STRATEGY:
                for key in _KEYS:
                    item.metadata.pop(key, None)
        for caption in sorted(
            page.blocks,
            key=lambda b: (b.bbox[1], b.bbox[0]) if b.bbox else (float("inf"), 0),
        ):
            text = caption.text.raw_text.strip() if caption.text else ""
            if (
                not caption.bbox
                or not is_caption_block(caption, text)
                or not text.lower().startswith(("fig", "圖"))
            ):
                continue
            if not caption.metadata.get("caption_kind") and not re.match(
                r"^\s*(?:Figure|Fig\.?|圖表?)\s*[\dA-Z一二三四五六七八九十]+\s*[:：.\-]",
                text,
                re.I,
            ):
                continue
            x0, y0, x1, y1 = caption.bbox

            def in_column(box):
                overlap = max(0.0, min(x1 + 2, box[2]) - max(x0 - 2, box[0]))
                return overlap >= (box[2] - box[0]) * 0.9

            images = [
                image
                for image in page.images
                if image.bbox
                and not image.metadata.get("figure_group_id")
                and image.bbox[3] <= y0 + 2
                and in_column(image.bbox)
                and (
                    image.image_type != "decorative"
                    or image.metadata.get("decorative_reason") == "small_area"
                )
            ]
            caption_ratios = [
                (s.bbox[3] - s.bbox[1]) / s.font_size
                for s in caption.spans
                if s.bbox and s.font_size
            ]
            caption_ratio = median(caption_ratios) if caption_ratios else 1.0
            seeds = []
            for block in page.blocks:
                if block.metadata.get("figure_group_id"):
                    continue
                if not block.bbox or block.type in {
                    BlockType.PAGE_HEADER,
                    BlockType.PAGE_FOOTER,
                    BlockType.FOOTNOTE,
                }:
                    continue
                if block.bbox[3] > y0 + 2 or not in_column(block.bbox):
                    continue
                ratios = [
                    (s.bbox[3] - s.bbox[1]) / s.font_size
                    for s in block.spans
                    if s.bbox and s.font_size
                ]
                if ratios and median(ratios) < min(0.55, caption_ratio * 0.6):
                    seeds.append(block)
            # Require image and transformed-text evidence, not proximity alone.
            if len(seeds) < (2 if images else 3):
                continue
            top = min([image.bbox[1] for image in images] + [b.bbox[1] for b in seeds])
            bounds = (x0, top, x1, y1)
            members = [
                b
                for b in page.blocks
                if b.bbox
                and not b.metadata.get("figure_group_id")
                and b.type
                not in {
                    BlockType.PAGE_HEADER,
                    BlockType.PAGE_FOOTER,
                    BlockType.FOOTNOTE,
                    BlockType.TABLE,
                }
                and b.bbox[1] >= top - 1
                and b.bbox[3] <= y1 + 1
                and in_column(b.bbox)
            ]
            if caption not in members:
                continue
            for block in members:
                block.metadata.update(
                    figure_group_id=caption.id,
                    figure_role="caption" if block is caption else "text",
                    reading_region="figure",
                    figure_bbox=bounds,
                    figure_region_strategy=_STRATEGY,
                )
            caption.metadata["figure_image_ids"] = [image.id for image in images]
            for image in images:
                image.metadata.update(
                    figure_group_id=caption.id, figure_region_strategy=_STRATEGY
                )
            count += 1
    return count
