import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import rust_pdf
from rust_pdf import _native
from test_stage11 import _classic_pdf, _reverse_position_pdf


def _stream(data: bytes) -> bytes:
    return f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _pages(contents: list[bytes]) -> bytes:
    children = " ".join(f"{4 + i * 2} 0 R" for i in range(len(contents)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{children}] /Count {len(contents)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for index, content in enumerate(contents):
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + index * 2} 0 R >>".encode(),
                _stream(content),
            ]
        )
    return _classic_pdf(objects)


def test_reusable_document_and_concurrent_calls_match_existing_functions():
    data = _reverse_position_pdf()
    with rust_pdf.Document(data) as document:
        assert document.extract_v2() == rust_pdf.extract_v2(data)
        assert document.extract() == rust_pdf.extract(data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: document.extract_v2(), range(4)))
        assert all(result == results[0] for result in results)
        assert "decoded_bytes" in document.decode_metrics()
    with pytest.raises(rust_pdf.PdfParseError, match="document is closed"):
        document.extract_text()


def test_selection_skips_bad_pages_and_stream_survives_handle_close():
    data = _pages([b"BT /F1 10 Tf (A) Tj ET", b"BT (bad", b"BT /F1 10 Tf (C) Tj ET"])
    assert rust_pdf.extract_text(data, pages="1,3") == "A\n\nC"
    document = rust_pdf.Document(data)
    stream = document.extract_layout_stream(pages="1,3")
    document.close()
    pages = list(stream)
    assert [page["page_number"] for page in pages] == [1, 3]
    assert [patch["page_index"] for patch in stream.finalizations()] == [0, 2]
    assert stream.metadata["streaming"]["metadata_finalized"] is True
    with pytest.raises(rust_pdf.PdfParseError):
        rust_pdf.extract_text(data)


def test_cancel_keeps_metadata_provisional_and_releases_pending_pages():
    data = _pages([b"", b"BT (bad"])
    stream = rust_pdf.extract_layout_stream(data)
    assert next(stream)["page_index"] == 0
    stream.close()
    assert list(stream) == []
    assert stream.remaining_pages == 0
    assert stream.metadata["streaming"]["cancelled"] is True
    assert stream.metadata["streaming"]["metadata_finalized"] is False
    with pytest.raises(rust_pdf.PdfParseError):
        list(stream.finalizations())


def test_limits_and_page_errors_keep_machine_codes():
    data = _reverse_position_pdf()
    for kwargs, code in [
        ({"limits": {"max_file_bytes": 1}}, "limit_exceeded"),
        ({"limits": {"typo": 1}}, "invalid_option"),
        ({"pages": "0"}, "invalid_option"),
        ({"pages": "2"}, "invalid_option"),
    ]:
        with pytest.raises(rust_pdf.PdfParseError) as failure:
            rust_pdf.extract_v2(data, **kwargs)
        assert json.loads(str(failure.value))["code"] == code


def test_native_work_releases_the_gil():
    # The long switch interval prevents Python's periodic scheduler from making
    # the worker progress between start.set() and the final assertion. Native
    # extraction must detach for the waiting worker to run during this call.
    data = _pages([b"BT /F1 10 Tf " + b"(A) Tj " * 30_000 + b"ET"])
    document = _native.Document(data)
    ready, start, progressed = threading.Event(), threading.Event(), threading.Event()

    def worker():
        ready.set()
        start.wait()
        progressed.set()

    thread = threading.Thread(target=worker)
    thread.start()
    ready.wait()
    old_interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(10)
        start.set()
        result = document.extract_text(False, False)
        assert progressed.is_set(), "native extraction held the GIL"
        assert len(result) == 30_000
    finally:
        sys.setswitchinterval(old_interval)
        start.set()
        thread.join(timeout=10)


def test_vertical_metrics_are_preserved_by_python_binding():
    data = _classic_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            _stream(b"BT /F1 10 Tf 1 0 0 1 50 100 Tm <00010002> Tj ET"),
            b"<< /Type /Font /Subtype /Type0 /Encoding /Identity-V /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
            b"<< /Type /Font /Subtype /CIDFontType2 /DW 1000 /DW2 [880 -1000] /W2 [1 [-1200 300 900]] >>",
            _stream(
                b"1 begincodespacerange <0000> <ffff> endcodespacerange 2 beginbfchar <0001> <4E00> <0002> <4E8C> endbfchar"
            ),
        ]
    )
    result = rust_pdf.extract_v2(data, mode="content-order")
    assert result["text"] == "一二"
    assert result["glyphs"][0]["origin"] == [47.0, 91.0]
    assert result["glyphs"][0]["advance"] == [0.0, -12.0]
    assert result["glyphs"][1]["origin"] == [45.0, 79.2]
