"""Python API for the from-scratch Rust PDF text extractor."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ._native import (
    PdfParseError,
    LayoutJsonStream as _LayoutJsonStream,
    extract_images_json as _extract_images_json,
    extract_json as _extract_json,
    extract_layout_json as _extract_layout_json,
    extract_layout_stream as _extract_layout_stream,
    extract_text,
    extract_v2_json as _extract_v2_json,
    inspect_json as _inspect_json,
    version_info,
)


def extract(
    data: bytes,
    *,
    normalize_unicode: bool = False,
    layout: bool = True,
) -> dict[str, Any]:
    """Extract structured text, spans, pages, and warnings from PDF bytes."""
    return json.loads(_extract_json(data, normalize_unicode, layout))


def extract_v2(
    data: bytes,
    *,
    mode: str = "auto",
    normalize_unicode: bool = False,
    quality: bool = True,
) -> dict[str, Any]:
    """Extract V2 text with content-order, layout, or auto mode."""
    return json.loads(_extract_v2_json(data, mode, normalize_unicode, quality))


def extract_layout(
    data: bytes,
    *,
    normalize_unicode: bool = False,
    quality: bool = True,
    debug_glyphs: bool = False,
    timings: bool = False,
) -> dict[str, Any]:
    """Extract versioned coordinate-normalized Layout IR from PDF bytes."""
    return json.loads(
        _extract_layout_json(
            data,
            normalize_unicode,
            quality,
            debug_glyphs,
            timings,
        )
    )


class LayoutStream(Iterator[dict[str, Any]]):
    """Native lazy page iterator with in-place final metadata updates."""

    def __init__(self, native: _LayoutJsonStream) -> None:
        self._native = native
        self.metadata: dict[str, Any] = json.loads(native.metadata_json)

    @property
    def remaining_pages(self) -> int:
        """Return the number of pages still owned by native memory."""
        return self._native.remaining_pages

    @property
    def remaining_finalizations(self) -> int:
        """Return the number of final page patches still owned by native memory."""
        return self._native.remaining_finalizations

    def finalizations(self) -> Iterator[dict[str, Any]]:
        """Drain stable-ID page finalizations after page iteration completes."""
        while True:
            value = self._native.next_finalization_json()
            if value is None:
                return
            yield json.loads(value)

    def __iter__(self) -> LayoutStream:
        return self

    def __next__(self) -> dict[str, Any]:
        page_json = self._native.next_page_json()
        if page_json is None:
            final_metadata = json.loads(self._native.metadata_json)
            self.metadata.clear()
            self.metadata.update(final_metadata)
            raise StopIteration
        return json.loads(page_json)


def extract_layout_stream(
    data: bytes,
    *,
    normalize_unicode: bool = False,
    quality: bool = True,
    debug_glyphs: bool = False,
    timings: bool = False,
) -> LayoutStream:
    """Produce normalized pages lazily and finalize metadata after exhaustion."""
    return LayoutStream(
        _extract_layout_stream(
            data,
            normalize_unicode,
            quality,
            debug_glyphs,
            timings,
        )
    )

def extract_images(data: bytes) -> list[dict[str, Any]]:
    """Extract image XObjects without rendering pages."""
    return json.loads(_extract_images_json(data))


def inspect(data: bytes) -> dict[str, Any]:
    """Return the parsed PDF version and cross-reference summary."""
    return json.loads(_inspect_json(data))


__all__ = [
    "PdfParseError",
    "extract",
    "extract_text",
    "extract_v2",
    "extract_images",
    "extract_layout",
    "extract_layout_stream",
    "LayoutStream",
    "inspect",
    "version_info",
]
