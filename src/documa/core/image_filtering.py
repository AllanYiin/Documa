"""Geometry-based image filtering helpers."""

from __future__ import annotations

from typing import Any

from documa.core.ir import BBox, ImageIR


DECORATIVE_IMAGE_MAX_AREA_RATIO = 0.005
DECORATIVE_IMAGE_EXTREME_ASPECT_RATIO = 8.0


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _bbox_size(bbox: BBox | None) -> tuple[float, float] | None:
    if bbox is None:
        return None
    width = max(0.0, float(bbox[2]) - float(bbox[0]))
    height = max(0.0, float(bbox[3]) - float(bbox[1]))
    if width <= 0 or height <= 0:
        return None
    return width, height


def decorative_image_reason(
    *,
    bbox: BBox | None,
    page_width: float | None,
    page_height: float | None,
    intrinsic_width: Any = None,
    intrinsic_height: Any = None,
    max_area_ratio: float = DECORATIVE_IMAGE_MAX_AREA_RATIO,
    extreme_aspect_ratio: float = DECORATIVE_IMAGE_EXTREME_ASPECT_RATIO,
) -> str | None:
    """Return why an image should be treated as decorative, if applicable."""

    size = _bbox_size(bbox)
    if size is None:
        width = _positive_float(intrinsic_width)
        height = _positive_float(intrinsic_height)
        size = (width, height) if width is not None and height is not None else None
    if size is None:
        return None

    width, height = size
    aspect_ratio = max(width / height, height / width)
    if aspect_ratio >= extreme_aspect_ratio:
        return "extreme_aspect_ratio"

    page_w = _positive_float(page_width)
    page_h = _positive_float(page_height)
    if page_w is None or page_h is None:
        return None

    area_ratio = (width * height) / (page_w * page_h)
    if area_ratio <= max_area_ratio:
        return "small_area"
    return None


def is_decorative_image(image: ImageIR) -> bool:
    """Return whether an image has been marked as decorative."""

    return image.image_type == "decorative" or bool(image.metadata.get("decorative"))
