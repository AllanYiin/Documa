"""OcrStage: page-level and image-level OCR for scanned or image-only documents.

Runs before ReadingOrderStage so OCR-derived blocks join reading-order
analysis. OCR text is always tagged with ``metadata["origin"] = "ocr"`` plus
``ocr_engine`` / ``ocr_confidence`` and is never merged silently with native
parser text: when a page is fully OCR'd, its native noise blocks move into
``page.metadata["suppressed_native_blocks"]``.

The stage is opt-in (``PipelineContext.settings["ocr"] = True``) because the
OCR model download and inference are heavyweight and would make snapshot
benchmarks nondeterministic. It needs ``settings["source_path"]`` to re-open
the PDF; without it (or without the ``documa[ocr]`` extra) it degrades to a
skip with an explicit reason instead of failing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, TextContent, to_plain_data
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult

DEFAULT_DENSITY_THRESHOLD = 0.05  # native chars per thousand square points
LOW_CONFIDENCE_FLAG = 0.3
_RENDER_ZOOM = 2.0

_ENGINE_CACHE: dict[str, Any] = {}


def _load_engine():
    """Return (engine, engine_label) or (None, skip_reason)."""
    if "engine" in _ENGINE_CACHE:
        return _ENGINE_CACHE["engine"], _ENGINE_CACHE["label"]
    try:
        import rapidocr_onnxruntime
    except ImportError:
        return None, "ocr_extra_not_installed"
    engine = rapidocr_onnxruntime.RapidOCR()
    try:
        from importlib.metadata import version as _dist_version

        version = _dist_version("rapidocr-onnxruntime")
    except Exception:
        version = getattr(rapidocr_onnxruntime, "__version__", "unknown")
    _ENGINE_CACHE["engine"] = engine
    _ENGINE_CACHE["label"] = f"rapidocr/{version}"
    return engine, _ENGINE_CACHE["label"]


def _page_text_density(page) -> float:
    char_count = sum(
        len(block.text.raw_text) for block in page.blocks if block.text is not None
    )
    area_kpt = max(page.width * page.height, 1.0) / 1000.0
    return char_count / area_kpt


def _run_engine(engine, png_bytes: bytes) -> list[tuple[list, str, float]]:
    result, _elapse = engine(png_bytes)
    if not result:
        return []
    return [(box, str(text), float(score)) for box, text, score in result]


def _box_to_bbox(box: list, zoom: float) -> tuple[float, float, float, float]:
    xs = [point[0] / zoom for point in box]
    ys = [point[1] / zoom for point in box]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass(slots=True)
class OcrStage(PipelineStage):
    """Density-triggered full-page OCR plus embedded-image OCR (RapidOCR)."""

    name: str = "ocr"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        settings = context.settings if context else {}

        def skip(reason: str) -> StageResult:
            return StageResult(
                document=document,
                stage_name=self.name,
                changed=False,
                report={"skipped_reason": reason},
            )

        if not settings.get("ocr"):
            return skip("ocr_disabled")
        source_path = settings.get("source_path")
        if not source_path or not str(source_path).lower().endswith(".pdf"):
            return skip("source_path_unavailable_or_not_pdf")
        if not Path(source_path).is_file():
            return skip("source_file_missing")

        engine, engine_label = _load_engine()
        if engine is None:
            return skip(engine_label)

        try:
            import pymupdf
        except ImportError:
            return skip("pymupdf_not_installed")

        threshold = float(settings.get("ocr_density_threshold", DEFAULT_DENSITY_THRESHOLD))
        pages_report: list[dict[str, Any]] = []
        blocks_created = 0
        images_ocr = 0

        pdf = pymupdf.open(str(source_path))
        try:
            pdf_pages = {page_number + 1: pdf[page_number] for page_number in range(pdf.page_count)}
            for page in document.pages:
                pdf_page = pdf_pages.get(page.page_number)
                if pdf_page is None:
                    continue
                try:
                    if _page_text_density(page) < threshold:
                        created, confidence_avg = self._full_page_ocr(
                            page, pdf_page, engine, engine_label
                        )
                        blocks_created += created
                        pages_report.append(
                            {
                                "page": page.page_number,
                                "mode": "full-page",
                                "blocks_created": created,
                                "confidence_avg": confidence_avg,
                            }
                        )
                    else:
                        ocr_count = self._image_ocr(page, pdf, engine, engine_label)
                        images_ocr += ocr_count
                        if ocr_count:
                            pages_report.append(
                                {"page": page.page_number, "mode": "image", "images_ocr": ocr_count}
                            )
                except Exception as exc:  # per-page isolation: one bad page must not kill the parse
                    page.metadata["ocr_error"] = str(exc)
                    pages_report.append({"page": page.page_number, "mode": "error", "error": str(exc)})
        finally:
            pdf.close()

        changed = blocks_created > 0 or images_ocr > 0
        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed,
            report={
                "engine": engine_label,
                "blocks_created": blocks_created,
                "images_ocr": images_ocr,
                "pages": pages_report,
            },
        )

    def _full_page_ocr(self, page, pdf_page, engine, engine_label: str) -> tuple[int, float | None]:
        import pymupdf

        pixmap = pdf_page.get_pixmap(matrix=pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
        lines = _run_engine(engine, pixmap.tobytes("png"))

        # Full-page mode: park native noise blocks so OCR and parser text never mix.
        if page.blocks:
            page.metadata["suppressed_native_blocks"] = [to_plain_data(block) for block in page.blocks]
            page.blocks = []

        confidences: list[float] = []
        for index, (box, text, score) in enumerate(lines, start=1):
            if not text.strip():
                continue
            confidences.append(score)
            page.blocks.append(
                BlockIR(
                    id=f"p{page.page_number}_ocr{index}",
                    type=BlockType.TEXT,
                    page_number=page.page_number,
                    text=TextContent(text),
                    bbox=_box_to_bbox(box, _RENDER_ZOOM),
                    confidence=Confidence.MEDIUM if score >= LOW_CONFIDENCE_FLAG else Confidence.LOW,
                    metadata={
                        "origin": "ocr",
                        "ocr_engine": engine_label,
                        "ocr_confidence": round(score, 4),
                    },
                )
            )
        confidence_avg = round(sum(confidences) / len(confidences), 4) if confidences else None
        page.metadata["ocr"] = {
            "mode": "full-page",
            "engine": engine_label,
            "confidence_avg": confidence_avg,
        }
        if confidence_avg is not None and confidence_avg < LOW_CONFIDENCE_FLAG:
            page.metadata["ocr_low_confidence"] = True
        return len([c for c in confidences]), confidence_avg

    def _image_ocr(self, page, pdf, engine, engine_label: str) -> int:
        ocr_count = 0
        for image in page.images:
            xref = image.metadata.get("xref")
            if not isinstance(xref, int):
                continue
            try:
                extracted = pdf.extract_image(xref)
            except Exception:
                continue
            lines = _run_engine(engine, extracted["image"])
            texts = [text for _box, text, _score in lines if text.strip()]
            if not texts:
                continue
            scores = [score for _box, _text, score in lines]
            image.metadata["ocr_text"] = "\n".join(texts)
            image.metadata["ocr_confidence"] = round(sum(scores) / len(scores), 4)
            image.metadata["ocr_engine"] = engine_label
            ocr_count += 1
        return ocr_count
