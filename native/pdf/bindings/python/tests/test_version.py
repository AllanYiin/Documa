import pytest

from rust_pdf import PdfParseError, extract, extract_text, version_info


def _text_pdf() -> bytes:
    content = b"BT /F1 12 Tf 10 10 Td (Python text) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << "
            b"/Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            f"<< /Length {len(content)} >>\nstream\n".encode()
            + content
            + b"\nendstream"
        ),
        (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        ),
    ]
    pdf = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f\n")
    for offset in offsets:
        pdf.extend(f"{offset:010} 00000 n\n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(pdf)


def test_version_info_reports_current_stage():
    version, stage = version_info()
    assert version == "0.2.0"
    assert stage == "stage-11"


def test_extract_text_and_structured_result():
    data = _text_pdf()
    assert extract_text(data) == "Python text"
    result = extract(data)
    assert result["text"] == "Python text"
    assert result["pages"][0]["spans"][0]["text"] == "Python text"


def test_stable_parse_exception_contains_machine_code():
    with pytest.raises(PdfParseError, match="invalid_header"):
        extract_text(b"not a PDF")
