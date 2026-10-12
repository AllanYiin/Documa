"""Reusable native document handles; this module only converts types and results."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ._native import Document as _Document, PdfParseError


def _options_json(options: dict[str, int] | None) -> str | None:
    try:
        return None if options is None else json.dumps(options, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise PdfParseError(
            json.dumps(
                {"code": "invalid_option", "offset": None, "message": str(error)}
            )
        ) from error


class Document:
    """Parse once; native operations release the GIL and share one decode budget.

    ``limits`` is a partial ParseLimits mapping. ``pages`` accepts one-based ranges
    such as ``"1,3-5"``. Streams retain their native state after this handle closes.
    """

    def __init__(self, data: bytes, *, limits: dict[str, int] | None = None) -> None:
        self._native = _Document(data, _options_json(limits))

    @property
    def _handle(self) -> _Document:
        if self._native is None:
            raise PdfParseError(
                json.dumps(
                    {
                        "code": "invalid_option",
                        "offset": None,
                        "message": "document is closed",
                    }
                )
            )
        return self._native

    def close(self) -> None:
        """Release this handle without cancelling streams it has already created."""
        self._native = None

    def __enter__(self) -> Document:
        self._handle
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def extract_text(
        self,
        normalize_unicode: bool = False,
        layout: bool = True,
        *,
        pages: str | None = None,
    ) -> str:
        return self._handle.extract_text(normalize_unicode, layout, pages)

    def extract(
        self,
        *,
        normalize_unicode: bool = False,
        layout: bool = True,
        pages: str | None = None,
    ) -> dict[str, Any]:
        return json.loads(self._handle.extract_json(normalize_unicode, layout, pages))

    def extract_v2(
        self,
        *,
        mode: str = "auto",
        normalize_unicode: bool = False,
        quality: bool = True,
        pages: str | None = None,
    ) -> dict[str, Any]:
        return json.loads(
            self._handle.extract_v2_json(mode, normalize_unicode, quality, pages)
        )

    def extract_layout(
        self,
        *,
        normalize_unicode: bool = False,
        quality: bool = True,
        debug_glyphs: bool = False,
        timings: bool = False,
        pages: str | None = None,
    ) -> dict[str, Any]:
        return json.loads(
            self._handle.extract_layout_json(
                normalize_unicode, quality, debug_glyphs, timings, pages
            )
        )

    def extract_layout_stream(
        self,
        *,
        normalize_unicode: bool = False,
        quality: bool = True,
        debug_glyphs: bool = False,
        timings: bool = False,
        pages: str | None = None,
    ) -> Any:
        from . import LayoutStream

        return LayoutStream(
            self._handle.extract_layout_stream(
                normalize_unicode, quality, debug_glyphs, timings, pages
            )
        )

    def extract_images(self, *, pages: str | None = None) -> list[dict[str, Any]]:
        return json.loads(self._handle.extract_images_json(pages))

    def analyze_direct_ocr(
        self, *, min_page_coverage: float = 0.9, pages: str | None = None
    ) -> dict[str, Any]:
        return json.loads(
            self._handle.analyze_direct_ocr_json(min_page_coverage, pages)
        )

    def direct_ocr_pages(
        self, *, min_page_coverage: float = 0.9, pages: str | None = None
    ) -> OcrCandidateStream:
        return OcrCandidateStream(
            self._handle.direct_ocr_pages(min_page_coverage, pages)
        )

    def inspect(self) -> dict[str, Any]:
        return json.loads(self._handle.inspect_json())

    def decode_metrics(self) -> dict[str, Any]:
        return json.loads(self._handle.decode_metrics_json())

    def ocr_direct(
        self,
        detection_model: bytes,
        recognition_model: bytes,
        dictionary: bytes,
        *,
        min_page_coverage: float = 0.9,
        min_confidence: float = 0.0,
        pages: str | None = None,
        ocr_limits: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if not hasattr(self._handle, "ocr_direct_json"):
            raise RuntimeError("rust_pdf was built without the ocr-onnx feature")
        return json.loads(
            self._handle.ocr_direct_json(
                detection_model,
                recognition_model,
                dictionary,
                min_page_coverage,
                min_confidence,
                pages,
                _options_json(ocr_limits),
            )
        )

    def ocr_direct_stream(
        self,
        detection_model: bytes,
        recognition_model: bytes,
        dictionary: bytes,
        *,
        min_page_coverage: float = 0.9,
        min_confidence: float = 0.0,
        pages: str | None = None,
        ocr_limits: dict[str, int] | None = None,
    ) -> OcrPageStream:
        if not hasattr(self._handle, "ocr_direct_stream"):
            raise RuntimeError("rust_pdf was built without the ocr-onnx feature")
        native = self._handle.ocr_direct_stream(
            detection_model,
            recognition_model,
            dictionary,
            min_page_coverage,
            min_confidence,
            pages,
            _options_json(ocr_limits),
        )
        return OcrPageStream(native)


class OcrCandidateStream(Iterator[dict[str, Any]]):
    """One image candidate per page; closing cancels further native page work."""

    def __init__(self, native: Any) -> None:
        self._native = native

    def __iter__(self) -> OcrCandidateStream:
        return self

    def __next__(self) -> dict[str, Any]:
        value = self._native.next_page_json()
        if value is None:
            raise StopIteration
        return json.loads(value)

    def close(self) -> None:
        self._native.close()

    def __enter__(self) -> OcrCandidateStream:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


class OcrPageStream(OcrCandidateStream):
    """Native OCR result pages; owns the model engine and releases it on close."""
