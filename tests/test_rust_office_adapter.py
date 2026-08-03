from __future__ import annotations

from pathlib import Path

import pytest

from documa.adapters.base import ParseOptions
from documa.adapters.registry import (
    PythonOfficeAdapter,
    RustFirstOfficeAdapter,
    UnsupportedLegacyOfficeAdapter,
    adapter_for_source,
)
from documa.adapters.rust_office_adapter import RustOfficeAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import DocumentIR


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "native" / "office" / "fixtures"


def _error(code: str, *, recoverable: bool = True) -> DocumaError:
    return DocumaError(
        DocumaErrorDetail(code=code, message=code, recoverable=recoverable)
    )


def test_registry_exposes_strict_office_provider_contract() -> None:
    assert isinstance(adapter_for_source("sample.docx"), RustFirstOfficeAdapter)
    assert isinstance(
        adapter_for_source("sample.xlsx", office_provider="rust"), RustOfficeAdapter
    )
    assert isinstance(
        adapter_for_source("sample.pptx", office_provider="python"), PythonOfficeAdapter
    )
    assert isinstance(adapter_for_source("sample.doc"), UnsupportedLegacyOfficeAdapter)

    with pytest.raises(DocumaError) as caught:
        adapter_for_source("sample.xlsx", office_provider="python")
    assert caught.value.detail.code == "OFFICE_PROVIDER_CAPABILITY_UNAVAILABLE"


def test_legacy_word_and_powerpoint_have_stable_error() -> None:
    for name in ("sample.doc", "sample.ppt"):
        with pytest.raises(DocumaError) as caught:
            adapter_for_source(name).parse(name)
        assert caught.value.detail.code == "LEGACY_OFFICE_NOT_SUPPORTED"
        assert caught.value.detail.recoverable is False


def test_auto_fallback_is_limited_to_binding_or_capability_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def missing_binding(self, source, options=None):
        raise _error("RUST_OFFICE_NOT_INSTALLED")

    def python_parse(self, source, options=None):
        calls.append(str(source))
        return DocumentIR(id="doc-test", source_name=str(source), parser="docx")

    monkeypatch.setattr(RustOfficeAdapter, "parse", missing_binding)
    monkeypatch.setattr(PythonOfficeAdapter, "parse", python_parse)
    document = RustFirstOfficeAdapter().parse("sample.docx")
    assert calls == ["sample.docx"]
    assert document.metadata["office_provider"] == {
        "requested": "auto",
        "actual": "python_docx",
        "fallback": True,
        "reason_code": "RUST_OFFICE_NOT_INSTALLED",
        "reason": "RUST_OFFICE_NOT_INSTALLED",
    }


def test_auto_never_hides_corruption_with_python_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupt(self, source, options=None):
        raise _error("OOXML_ZIP_CORRUPT", recoverable=True)

    def forbidden_python_parse(self, source, options=None):
        raise AssertionError("corrupt input must not fall back")

    monkeypatch.setattr(RustOfficeAdapter, "parse", corrupt)
    monkeypatch.setattr(PythonOfficeAdapter, "parse", forbidden_python_parse)
    with pytest.raises(DocumaError) as caught:
        RustFirstOfficeAdapter().parse("sample.docx")
    assert caught.value.detail.code == "OOXML_ZIP_CORRUPT"


def test_sidecar_generation_changes_with_office_provider_and_binding() -> None:
    from documa.search.sidecar import source_digest

    document = DocumentIR(
        id="doc-office",
        source_name="sample.docx",
        parser="rust_office",
        adapter_version="office-layout-v1",
        metadata={
            "office_binding_version": "0.1.0",
            "office_provider": {
                "requested": "auto",
                "actual": "rust",
                "fallback": False,
            },
        },
    )
    rust_digest = source_digest(document)
    document.metadata["office_provider"] = {
        "requested": "auto",
        "actual": "python_docx",
        "fallback": True,
    }
    assert source_digest(document) != rust_digest
    document.metadata["office_binding_version"] = "0.1.1"
    assert source_digest(document) != rust_digest


@pytest.mark.parametrize(
    ("fixture_name", "expected_kind", "expected_text"),
    [
        ("smoke.docx", "logical_flow", "策略總覽"),
        ("smoke.xls", "worksheet", "BIFF8"),
        ("smoke.xlsx", "worksheet", "流動性"),
        ("smoke.pptx", "slide", "產品路線"),
    ],
)
def test_real_rust_office_vertical_slices(
    fixture_name: str,
    expected_kind: str,
    expected_text: str,
    tmp_path: Path,
) -> None:
    try:
        __import__("rust_office")
    except ImportError:
        pytest.skip("bundled rust_office extension is not built")
    source = FIXTURE_ROOT / fixture_name
    if not source.exists():
        pytest.skip("bundled Rust Office fixtures are unavailable")

    document = RustOfficeAdapter().parse(
        source,
        ParseOptions(asset_dir=tmp_path / "assets"),
    )
    assert document.metadata["office_provider"]["actual"] == "rust"
    assert document.pages
    assert document.pages[0].metadata["source"] == expected_kind
    assert any(
        expected_text in (block.text.raw_text if block.text else "")
        for block in document.pages[0].blocks
    )

    if fixture_name.endswith(".pptx"):
        assert any(block.bbox is not None for block in document.pages[0].blocks)
        assert document.pages[0].metadata["citation_geometry"] == "visual"
    else:
        assert all(block.bbox is None for block in document.pages[0].blocks)
        assert document.pages[0].metadata["citation_geometry"] == "structural"
