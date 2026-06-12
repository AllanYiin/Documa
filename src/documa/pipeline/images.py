"""Image and chart candidate normalization stage."""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.image_filtering import decorative_image_reason
from documa.core.ir import DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


CHART_HINTS = ("chart", "graph", "plot", "圖表", "圖 ", "圖：", "圖:", "Figure")


@dataclass(slots=True)
class ImageNormalizationStage(PipelineStage):
    """Normalize image metadata and mark chart candidates conservatively."""

    name: str = "image_normalization"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        normalized = 0
        chart_candidates = 0
        decorative_images = 0

        for page in document.pages:
            for image in page.images:
                image.metadata.setdefault("normalized", True)
                image.metadata.setdefault("source_page", page.page_number)
                normalized += 1
                reason = decorative_image_reason(
                    bbox=image.bbox,
                    page_width=page.width,
                    page_height=page.height,
                    intrinsic_width=image.metadata.get("width"),
                    intrinsic_height=image.metadata.get("height"),
                )
                if reason:
                    image.image_type = "decorative"
                    image.metadata["decorative"] = True
                    image.metadata["decorative_reason"] = reason
                    decorative_images += 1
                    continue
                haystack = " ".join(
                    str(part or "")
                    for part in (
                        image.caption,
                        image.metadata.get("caption_candidate"),
                        image.metadata.get("alt_text"),
                    )
                )
                if image.image_type == "image" and any(hint in haystack for hint in CHART_HINTS):
                    image.image_type = "chart_candidate"
                    image.metadata["image_normalization"] = "caption_hint_chart_candidate"
                    chart_candidates += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=normalized > 0,
            report={
                "images_normalized": normalized,
                "chart_candidates": chart_candidates,
                "decorative_images": decorative_images,
            },
        )
