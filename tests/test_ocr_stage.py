"""OcrStage tests (Stage 5).

Engine-dependent tests carry the ``ocr`` marker and skip when the
``documa[ocr]`` extra is missing; degradation-path tests run everywhere.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.pipeline import PipelineContext, run_default_pipeline
from documa.pipeline.ocr import OcrStage, _page_text_density
from documa.interfaces.tools import process_document_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED_PDF = REPO_ROOT / "fixtures" / "pdf" / "real" / "scanned-note.pdf"
NATIVE_PDF = REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf"

_HAS_OCR = True
try:  # noqa: SIM105
    import rapidocr_onnxruntime  # noqa: F401
except ImportError:
    _HAS_OCR = False

needs_ocr = pytest.mark.ocr


def _parse(source: Path):
    return adapter_for_source(str(source)).parse(str(source), ParseOptions())


class TestDegradationPaths:
    def test_stage_skips_when_disabled(self):
        document = _parse(NATIVE_PDF)
        result = OcrStage().run(document, PipelineContext(settings={}))
        assert result.changed is False
        assert result.report["skipped_reason"] == "ocr_disabled"

    def test_stage_skips_without_source_path(self):
        document = _parse(NATIVE_PDF)
        result = OcrStage().run(document, PipelineContext(settings={"ocr": True}))
        assert result.report["skipped_reason"] == "source_path_unavailable_or_not_pdf"

    def test_stage_skips_gracefully_when_extra_missing(self, monkeypatch):
        import documa.pipeline.ocr as ocr_module

        monkeypatch.setattr(ocr_module, "_ENGINE_CACHE", {})
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "rapidocr_onnxruntime":
                raise ImportError("simulated missing extra")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        document = _parse(SCANNED_PDF)
        result = OcrStage().run(
            document, PipelineContext(settings={"ocr": True, "source_path": str(SCANNED_PDF)})
        )
        assert result.changed is False
        assert result.report["skipped_reason"] == "ocr_extra_not_installed"

    def test_process_tool_surfaces_warning_when_extra_missing(self, monkeypatch, tmp_path):
        import documa.pipeline.ocr as ocr_module

        monkeypatch.setattr(ocr_module, "_ENGINE_CACHE", {})
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "rapidocr_onnxruntime":
                raise ImportError("simulated missing extra")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        payload = process_document_tool(source=str(SCANNED_PDF), out=str(tmp_path), ocr=True)
        assert payload["status"] == "ok"
        assert "ocr: ocr_extra_not_installed" in payload["warnings"]

    def test_ocr_off_by_default_keeps_native_pipeline_untouched(self, tmp_path):
        payload = process_document_tool(source=str(NATIVE_PDF), out=str(tmp_path))
        assert payload["status"] == "ok"
        assert payload["warnings"] == []
        ocr_stage = next(s for s in payload["pipeline"]["stages"] if s["stage_name"] == "ocr")
        assert ocr_stage["skipped_reason"] == "ocr_disabled"


class _FakeEngine:
    """Engine double: returns one fixed line for any image."""

    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error

    def __call__(self, _png_bytes):
        if self._error is not None:
            raise self._error
        return self._result, 0.0


class TestWithFakeEngine:
    def _run(self, source: Path, engine, monkeypatch):
        import documa.pipeline.ocr as ocr_module

        monkeypatch.setattr(ocr_module, "_ENGINE_CACHE", {"engine": engine, "label": "rapidocr/fake"})
        document = _parse(source)
        result = OcrStage().run(
            document, PipelineContext(settings={"ocr": True, "source_path": str(source)})
        )
        return document, result

    def test_stage_skips_when_source_file_missing(self, monkeypatch):
        import documa.pipeline.ocr as ocr_module

        monkeypatch.setattr(ocr_module, "_ENGINE_CACHE", {"engine": _FakeEngine(), "label": "rapidocr/fake"})
        document = _parse(NATIVE_PDF)
        result = OcrStage().run(
            document, PipelineContext(settings={"ocr": True, "source_path": "gone/missing.pdf"})
        )
        assert result.report["skipped_reason"] == "source_file_missing"

    def test_full_page_ocr_suppresses_native_blocks_and_tags_output(self, monkeypatch):
        fake_lines = [([[10, 10], [200, 10], [200, 40], [10, 40]], "RECOGNIZED LINE", 0.91)]
        document, result = self._run(SCANNED_PDF, _FakeEngine(result=fake_lines), monkeypatch)

        page = document.pages[0]
        assert result.report["blocks_created"] == 1
        assert page.blocks[0].metadata == {
            "origin": "ocr",
            "ocr_engine": "rapidocr/fake",
            "ocr_confidence": 0.91,
        }
        # zoom=2 render: engine pixel coords come back halved into PDF points.
        assert page.blocks[0].bbox == (5.0, 5.0, 100.0, 20.0)
        assert "suppressed_native_blocks" in page.metadata or not page.metadata.get("suppressed_native_blocks")

    def test_low_confidence_page_is_flagged(self, monkeypatch):
        fake_lines = [([[0, 0], [10, 0], [10, 10], [0, 10]], "??", 0.11)]
        document, _ = self._run(SCANNED_PDF, _FakeEngine(result=fake_lines), monkeypatch)
        assert document.pages[0].metadata["ocr_low_confidence"] is True

    def test_engine_error_is_isolated_to_the_page(self, monkeypatch):
        document, result = self._run(SCANNED_PDF, _FakeEngine(error=RuntimeError("engine crash")), monkeypatch)
        assert result.changed is False
        assert document.pages[0].metadata["ocr_error"] == "engine crash"
        assert result.report["pages"][0]["mode"] == "error"

    def test_embedded_image_ocr_writes_image_metadata(self, monkeypatch):
        fake_lines = [([[0, 0], [10, 0], [10, 10], [0, 10]], "IMAGE TEXT", 0.88)]
        mixed = REPO_ROOT / "fixtures" / "pdf" / "real" / "mixed-media-brief.pdf"
        document, result = self._run(mixed, _FakeEngine(result=fake_lines), monkeypatch)

        image = document.pages[0].images[0]
        assert result.report["images_ocr"] >= 1
        assert image.metadata["ocr_text"] == "IMAGE TEXT"
        assert image.metadata["ocr_engine"] == "rapidocr/fake"
        assert 0.0 <= image.metadata["ocr_confidence"] <= 1.0

    def test_no_text_found_leaves_ir_unchanged(self, monkeypatch):
        document, result = self._run(SCANNED_PDF, _FakeEngine(result=None), monkeypatch)
        assert result.report["blocks_created"] == 0
        assert document.pages[0].metadata["ocr"]["confidence_avg"] is None


class TestDensityHeuristic:
    def test_scanned_page_has_near_zero_density(self):
        document = _parse(SCANNED_PDF)
        assert _page_text_density(document.pages[0]) < 0.05

    def test_native_page_has_high_density(self):
        document = _parse(NATIVE_PDF)
        assert _page_text_density(document.pages[1]) > 0.05


@needs_ocr
@pytest.mark.skipif(not _HAS_OCR, reason="documa[ocr] extra not installed")
class TestOcrExecution:
    @pytest.fixture(scope="class")
    def scanned_document(self):
        document = _parse(SCANNED_PDF)
        run_default_pipeline(
            document,
            PipelineContext(settings={"ocr": True, "source_path": str(SCANNED_PDF)}),
        )
        return document

    def test_image_only_page_produces_flagged_ocr_blocks(self, scanned_document):
        page = scanned_document.pages[0]
        ocr_blocks = [b for b in page.blocks if b.metadata.get("origin") == "ocr"]
        assert ocr_blocks, "expected OCR blocks on the scanned page"
        for block in ocr_blocks:
            assert block.metadata["ocr_engine"].startswith("rapidocr/")
            assert 0.0 <= block.metadata["ocr_confidence"] <= 1.0
        assert page.metadata["ocr"]["mode"] == "full-page"

    def test_ocr_text_contains_expected_content(self, scanned_document):
        text = " ".join(
            b.text.raw_text for b in scanned_document.pages[0].blocks if b.text is not None
        ).upper()
        assert "MAINTENANCE" in text

    def test_ocr_blocks_flow_into_chunks(self, scanned_document):
        assert scanned_document.chunks, "OCR text should be chunkable"
        combined = " ".join(c.text.raw_text for c in scanned_document.chunks).upper()
        assert "MAINTENANCE" in combined

    def test_native_pdf_with_ocr_enabled_only_touches_images(self, tmp_path):
        document = _parse(NATIVE_PDF)
        result = OcrStage().run(
            document, PipelineContext(settings={"ocr": True, "source_path": str(NATIVE_PDF)})
        )
        report_modes = {p["mode"] for p in result.report["pages"]}
        assert "full-page" not in report_modes
        native_blocks = [b for page in document.pages for b in page.blocks]
        assert all(b.metadata.get("origin") != "ocr" for b in native_blocks)
