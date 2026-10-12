"""The Rust adapter must resolve indirect font Encoding/Differences arrays."""
from documa.adapters.rust_pdf_adapter import RustPdfAdapter


def test_rust_adapter_resolves_indirect_encoding_differences(tmp_path):
    content = b"BT /F1 12 Tf 72 720 Td (A) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /BaseEncoding /WinAnsiEncoding /Differences 6 0 R >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"7 0 R",
        b"[65 /Z]",
    ]
    body = b"%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 8\n0000000000 65535 f \n"
    body += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    body += b"trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n"
    source = tmp_path / "indirect-differences.pdf"
    source.write_bytes(body)

    document = RustPdfAdapter().parse(source)

    assert document.parser == "rust_pdf"
    assert len(document.pages) == 1
    text = "".join(block.text.raw_text for block in document.pages[0].blocks if block.text)
    assert text.strip() == "Z"
    assert source.read_bytes() == body
