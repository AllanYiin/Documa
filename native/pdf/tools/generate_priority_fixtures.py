"""Generate tiny original PDF fixtures; uses no PDF parser or external document."""

from pathlib import Path
import base64
import hashlib
import json
import zlib


def classic_pdf(objects: list[bytes]) -> bytes:
    pdf = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)


def stream(data: bytes, extra: bytes = b"") -> bytes:
    return (
        f"<< /Length {len(data)} ".encode()
        + extra
        + b">>\nstream\n"
        + data
        + b"\nendstream"
    )


def selection_pdf() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R] /Count 3 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for index, text in enumerate((b"Alpha", None, b"Omega")):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + index * 2} 0 R >>".encode()
        )
        content = (
            b"BT (unterminated"
            if text is None
            else b"BT /F1 10 Tf 1 0 0 1 10 195 Tm (Header) Tj 1 0 0 1 10 100 Tm ("
            + text
            + b") Tj ET"
        )
        objects.append(stream(content))
    return classic_pdf(objects)


def vertical_pdf() -> bytes:
    return classic_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            stream(b"BT /F1 10 Tf 1 0 0 1 50 100 Tm <00010002> Tj ET"),
            b"<< /Type /Font /Subtype /Type0 /Encoding /Identity-V /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>",
            b"<< /Type /Font /Subtype /CIDFontType2 /DW 1000 /DW2 [880 -1000] /W2 [1 [-1200 300 900]] >>",
            stream(
                b"1 begincodespacerange <0000> <ffff> endcodespacerange 2 beginbfchar <0001> <4E00> <0002> <4E8C> endbfchar"
            ),
        ]
    )


def ocr_pdf(
    pages: int = 2, image_bytes: bytes = b"\xff", width: int = 1, height: int = 1
) -> bytes:
    children = " ".join(f"{5 + index} 0 R" for index in range(pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{children}] /Count {pages} >>".encode(),
        stream(
            image_bytes,
            f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /BitsPerComponent 8 /ColorSpace /DeviceGray ".encode(),
        ),
        stream(b"q 100 0 0 100 0 0 cm /Im Do Q"),
    ]
    objects.extend(
        [
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /XObject << /Im 3 0 R >> >> /Contents 4 0 R >>"
        ]
        * pages
    )
    return classic_pdf(objects)


def filters_pdf() -> bytes:
    content = b"BT /F1 10 Tf (Filtered text) Tj ET"
    encoded = base64.a85encode(zlib.compress(content)) + b"~>"
    return classic_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            stream(encoded, b"/Filter [/ASCII85Decode /FlateDecode] "),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        ]
    )


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / "tests/fixtures/priority"
    output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, data in {
        "selected-pages.pdf": selection_pdf(),
        "vertical-w2.pdf": vertical_pdf(),
        "ocr-pages.pdf": ocr_pdf(),
        "filters.pdf": filters_pdf(),
    }.items():
        (output / name).write_bytes(data)
        manifest[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
