"""Python API for the from-scratch Rust PDF text extractor."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ._native import (
    PdfParseError,
    LayoutJsonStream as _LayoutJsonStream,
    analyze_direct_ocr_json as _analyze_direct_ocr_json,
    extract_images_json as _extract_images_json,
    extract_json as _extract_json,
    extract_layout_json as _extract_layout_json,
    extract_layout_stream as _extract_layout_stream,
    extract_text as _extract_text,
    extract_v2_json as _extract_v2_json,
    inspect_json as _inspect_json,
    version_info,
    build_info_json as _build_info_json,
)
from .document import Document, OcrCandidateStream, OcrPageStream

try:
    from ._native import ocr_direct_json as _ocr_direct_json
except ImportError:
    _ocr_direct_json = None


def extract(
    data: bytes,
    *,
    normalize_unicode: bool = False,
    layout: bool = True,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Extract structured text, spans, pages, and warnings from PDF bytes."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract(
            normalize_unicode=normalize_unicode, layout=layout, pages=pages
        )
    return json.loads(_extract_json(data, normalize_unicode, layout))


def extract_v2(
    data: bytes,
    *,
    mode: str = "auto",
    normalize_unicode: bool = False,
    quality: bool = True,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Extract V2 text with content-order, layout, or auto mode."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract_v2(
            mode=mode, normalize_unicode=normalize_unicode, quality=quality, pages=pages
        )
    return json.loads(_extract_v2_json(data, mode, normalize_unicode, quality))


def extract_layout(
    data: bytes,
    *,
    normalize_unicode: bool = False,
    quality: bool = True,
    debug_glyphs: bool = False,
    timings: bool = False,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Extract versioned coordinate-normalized Layout IR from PDF bytes."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract_layout(
            normalize_unicode=normalize_unicode,
            quality=quality,
            debug_glyphs=debug_glyphs,
            timings=timings,
            pages=pages,
        )
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

    def close(self) -> None:
        """Cancel pending native work; partial metadata remains provisional."""
        self._native.close()
        self.metadata.clear()
        self.metadata.update(json.loads(self._native.metadata_json))

    def __enter__(self) -> LayoutStream:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

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
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> LayoutStream:
    """Produce normalized pages lazily and finalize metadata after exhaustion."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract_layout_stream(
            normalize_unicode=normalize_unicode,
            quality=quality,
            debug_glyphs=debug_glyphs,
            timings=timings,
            pages=pages,
        )
    return LayoutStream(
        _extract_layout_stream(
            data,
            normalize_unicode,
            quality,
            debug_glyphs,
            timings,
        )
    )


def extract_images(
    data: bytes, *, pages: str | None = None, limits: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """Extract image XObjects without rendering pages."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract_images(pages=pages)
    return json.loads(_extract_images_json(data))


def analyze_direct_ocr(
    data: bytes,
    *,
    min_page_coverage: float = 0.9,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return pages eligible for direct-image OCR without page rasterization."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).analyze_direct_ocr(
            min_page_coverage=min_page_coverage, pages=pages
        )
    return json.loads(_analyze_direct_ocr_json(data, min_page_coverage))


def ocr_direct(
    data: bytes,
    detection_model: bytes,
    recognition_model: bytes,
    dictionary: bytes,
    *,
    min_page_coverage: float = 0.9,
    min_confidence: float = 0.0,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
    ocr_limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run the optional RapidOCR-compatible native ONNX backend."""
    if _ocr_direct_json is None:
        raise RuntimeError("rust_pdf was built without the ocr-onnx feature")
    if pages is not None or limits is not None or ocr_limits is not None:
        return Document(data, limits=limits).ocr_direct(
            detection_model,
            recognition_model,
            dictionary,
            min_page_coverage=min_page_coverage,
            min_confidence=min_confidence,
            pages=pages,
            ocr_limits=ocr_limits,
        )
    return json.loads(
        _ocr_direct_json(
            data,
            detection_model,
            recognition_model,
            dictionary,
            min_page_coverage,
            min_confidence,
        )
    )


def inspect(data: bytes, *, limits: dict[str, int] | None = None) -> dict[str, Any]:
    """Return the parsed PDF version and cross-reference summary."""
    if limits is not None:
        return Document(data, limits=limits).inspect()
    return json.loads(_inspect_json(data))


def extract_text(
    data: bytes,
    normalize_unicode: bool = False,
    layout: bool = True,
    *,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> str:
    """Extract legacy plain text with optional page ranges and parser limits."""
    if pages is not None or limits is not None:
        return Document(data, limits=limits).extract_text(
            normalize_unicode, layout, pages=pages
        )
    return _extract_text(data, normalize_unicode, layout)


def direct_ocr_pages(
    data: bytes,
    *,
    min_page_coverage: float = 0.9,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
) -> OcrCandidateStream:
    """Lazily analyze direct OCR candidates; close the stream to cancel."""
    return Document(data, limits=limits).direct_ocr_pages(
        min_page_coverage=min_page_coverage, pages=pages
    )


def ocr_direct_stream(
    data: bytes,
    detection_model: bytes,
    recognition_model: bytes,
    dictionary: bytes,
    *,
    min_page_coverage: float = 0.9,
    min_confidence: float = 0.0,
    pages: str | None = None,
    limits: dict[str, int] | None = None,
    ocr_limits: dict[str, int] | None = None,
) -> OcrPageStream:
    """Initialize the optional ONNX engine once and recognize one page at a time."""
    if _ocr_direct_json is None:
        raise RuntimeError("rust_pdf was built without the ocr-onnx feature")
    return Document(data, limits=limits).ocr_direct_stream(
        detection_model,
        recognition_model,
        dictionary,
        min_page_coverage=min_page_coverage,
        min_confidence=min_confidence,
        pages=pages,
        ocr_limits=ocr_limits,
    )


__all__ = [
    "build_info",
    "Document",
    "OcrCandidateStream",
    "OcrPageStream",
    "ocr_direct_stream",
    "direct_ocr_pages",
    "PdfParseError",
    "analyze_direct_ocr",
    "ocr_direct",
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


def build_info() -> dict[str, Any]:
    """Report development features without changing the legacy version tuple."""
    return json.loads(_build_info_json())
