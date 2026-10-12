"""Opt-in checks against the exact external artifacts prepared by ocr_integration.py."""

import hashlib
import json
import os
from pathlib import Path

import pytest
import rust_pdf

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def artifacts():
    value = os.environ.get("RUST_PDF_OCR_ARTIFACT_DIR")
    if not value:
        pytest.skip(
            "set RUST_PDF_OCR_ARTIFACT_DIR and ORT_DYLIB_PATH for pinned ONNX integration"
        )
    assert rust_pdf.build_info()["native_ocr_onnx"]
    root = Path(value)
    manifest = json.loads(
        (ROOT / "tests/fixtures/ocr/onnx-manifest.json").read_text(encoding="utf-8")
    )
    names = [
        manifest["assets"][0]["name"],
        manifest["prepared"]["recognition"]["name"],
        manifest["prepared"]["dictionary"]["name"],
    ]
    expected = [
        manifest["assets"][0]["sha256"],
        manifest["prepared"]["recognition"]["sha256"],
        manifest["prepared"]["dictionary"]["sha256"],
    ]
    values = [(root / name).read_bytes() for name in names]
    assert [hashlib.sha256(value).hexdigest() for value in values] == expected
    return (ROOT / "tests/fixtures/ocr/scan.pdf").read_bytes(), values, manifest


def test_native_ocr_collection_and_owned_stream_match(artifacts):
    data, models, manifest = artifacts
    with rust_pdf.Document(data) as document:
        complete = document.ocr_direct(*models)
        stream = document.ocr_direct_stream(*models, pages="1")
    pages = list(stream)
    assert pages == complete["pages"]
    assert complete["text"] == manifest["fixture"]["text"]
    assert pages[0]["blocks"][0]["confidence"] >= manifest["fixture"]["min_confidence"]
    stream.close()


def test_native_ocr_budget_error_has_machine_code(artifacts):
    data, models, _ = artifacts
    with pytest.raises(rust_pdf.PdfParseError) as failure:
        rust_pdf.ocr_direct(data, *models, ocr_limits={"max_total_blocks": 0})
    assert json.loads(str(failure.value))["code"] == "ocr_limit_exceeded"
