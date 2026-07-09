"""Generate the redistributable PDF fixtures under fixtures/pdf/real/.

These PDFs are self-made (license-clean) but exercise real-world layout
features: tables, multi-column reading order, footnotes, TOC/outline,
embedded images, and inline styling. They are generated once and committed;
this script exists for provenance and regeneration, not as part of the tests.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf

REAL_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pdf" / "real"

A4 = pymupdf.paper_rect("a4")
MARGIN = 56.0
FIXED_METADATA = {
    "title": "",
    "author": "Documa fixtures",
    "subject": "Synthetic test document",
    "creator": "scripts/make_real_fixtures.py",
    "producer": "Documa",
    "creationDate": "D:20260101000000Z",
    "modDate": "D:20260101000000Z",
}


def _tiny_png(width: int = 64, height: int = 48) -> bytes:
    """Build a deterministic RGB gradient PNG without external dependencies."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter type 0 per scanline
        for x in range(width):
            raw += bytes((int(255 * x / width), int(255 * y / height), 96))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _draw_table(page: pymupdf.Page, top: float, rows: list[list[str]], col_widths: list[float]) -> float:
    """Draw a ruled table starting at `top`; returns the y just below it."""
    left = MARGIN
    row_height = 22.0
    total_width = sum(col_widths)
    shape = page.new_shape()
    for i in range(len(rows) + 1):
        y = top + i * row_height
        shape.draw_line((left, y), (left + total_width, y))
    x = left
    for width in [0.0, *col_widths]:
        x += width
        shape.draw_line((x, top), (x, top + row_height * len(rows)))
    shape.finish(color=(0.2, 0.2, 0.2), width=0.7)
    shape.commit()
    for r, row in enumerate(rows):
        x = left
        for c, cell in enumerate(row):
            fontname = "hebo" if r == 0 else "helv"
            page.insert_text((x + 6, top + r * row_height + 15), cell, fontsize=9, fontname=fontname)
            x += col_widths[c]
    return top + row_height * len(rows)


def make_annual_report(path: Path) -> None:
    doc = pymupdf.open()

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 120), "Aurora Materials Annual Report", fontsize=22, fontname="hebo")
    page.insert_text((MARGIN, 150), "Fiscal Year Summary", fontsize=13, fontname="heit")
    page.insert_text((MARGIN, 220), "Contents", fontsize=14, fontname="hebo")
    for i, (label, target) in enumerate([("1. Overview", 2), ("2. Financial Results", 2), ("3. Outlook", 3)]):
        page.insert_text((MARGIN, 250 + i * 20), f"{label} " + "." * 48 + f" {target}", fontsize=10)

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 90), "1. Overview", fontsize=16, fontname="hebo")
    body = (
        "Aurora Materials develops high-durability composite panels for the "
        "construction sector. During the fiscal year the company expanded its "
        "coating facility and completed certification for two new panel "
        "families. Revenue grew across all regions"
    )
    rect = pymupdf.Rect(MARGIN, 105, A4.width - MARGIN, 190)
    page.insert_textbox(rect, body, fontsize=10, fontname="helv", align=pymupdf.TEXT_ALIGN_LEFT)
    # Superscript footnote marker right after the paragraph tail.
    page.insert_text((MARGIN + 172, 156.5), "1", fontsize=6.5, fontname="helv")
    page.insert_text((MARGIN + 178, 160), ", led by the Asia-Pacific segment.", fontsize=10, fontname="helv")

    page.insert_text((MARGIN, 220), "2. Financial Results", fontsize=16, fontname="hebo")
    bottom = _draw_table(
        page,
        240,
        [
            ["Region", "Revenue (M)", "Growth", "Share"],
            ["Asia-Pacific", "412.5", "18%", "41%"],
            ["Europe", "301.2", "9%", "30%"],
            ["Americas", "289.9", "6%", "29%"],
        ],
        [140.0, 110.0, 90.0, 90.0],
    )
    page.insert_textbox(
        pymupdf.Rect(MARGIN, bottom + 12, A4.width - MARGIN, bottom + 70),
        "Gross margin improved by two percentage points as raw-material costs "
        "stabilized. Operating expenses remained flat in absolute terms.",
        fontsize=10,
        fontname="helv",
    )
    page.insert_text((MARGIN, A4.height - 70), "1", fontsize=6.5, fontname="helv")
    page.insert_text(
        (MARGIN + 6, A4.height - 66),
        " Revenue figures are unaudited management estimates.",
        fontsize=8.5,
        fontname="helv",
    )

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 90), "3. Outlook", fontsize=16, fontname="hebo")
    _draw_table(
        page,
        110,
        [
            ["Initiative", "Target Year", "Status"],
            ["Coating line 2", "Next year", "Approved"],
            ["Recycled feedstock", "Two years", "Pilot"],
        ],
        [180.0, 120.0, 100.0],
    )
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 190, A4.width - MARGIN, 280),
        "Management expects demand to remain firm. Capacity utilisation is "
        "projected to stay above ninety percent through the planning horizon, "
        "supported by the order backlog carried into the new fiscal year.",
        fontsize=10,
        fontname="helv",
    )

    doc.set_toc([[1, "Overview", 2], [1, "Financial Results", 2], [1, "Outlook", 3]])
    doc.set_metadata(FIXED_METADATA)
    doc.save(path, deflate=True)
    doc.close()


def make_two_column_article(path: Path) -> None:
    doc = pymupdf.open()
    col_gap = 18.0
    col_width = (A4.width - 2 * MARGIN - col_gap) / 2

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 80), "Signal Propagation in Layered Media", fontsize=17, fontname="hebo")
    page.insert_text((MARGIN, 100), "A short synthetic article for layout testing", fontsize=10, fontname="heit")

    left_col = pymupdf.Rect(MARGIN, 130, MARGIN + col_width, 620)
    right_col = pymupdf.Rect(MARGIN + col_width + col_gap, 130, A4.width - MARGIN, 620)
    left_text = (
        "Layered media introduce boundary effects that dominate propagation "
        "behaviour at short wavelengths. When a signal crosses an interface, "
        "part of its energy reflects while the remainder refracts into the "
        "next layer. The balance between the two depends on the impedance "
        "contrast at the boundary.\n\n"
        "In practical assemblies the layers are rarely uniform. Thickness "
        "variation of only a few percent shifts the interference pattern "
        "enough to move resonance peaks by a measurable margin, which is why "
        "manufacturing tolerance is the first parameter engineers examine."
    )
    right_text = (
        "The second parameter is loss. Dissipative layers flatten the "
        "resonance structure and widen the usable band at the cost of peak "
        "transmission. Designers therefore alternate low-loss and high-loss "
        "layers to shape the response.\n\n"
        "Figure 1 summarises the measurement setup used throughout this "
        "article. A swept source drives the stack from the left while a "
        "calibrated receiver samples the transmitted field on the right."
    )
    page.insert_textbox(left_col, left_text, fontsize=10, fontname="helv", align=pymupdf.TEXT_ALIGN_LEFT)
    page.insert_textbox(right_col, right_text, fontsize=10, fontname="helv", align=pymupdf.TEXT_ALIGN_LEFT)

    fig_rect = pymupdf.Rect(MARGIN + col_width + col_gap, 400, A4.width - MARGIN, 520)
    shape = page.new_shape()
    shape.draw_rect(fig_rect)
    shape.finish(color=(0.1, 0.1, 0.4), width=1.2)
    shape.draw_line((fig_rect.x0 + 15, fig_rect.y1 - 25), (fig_rect.x1 - 15, fig_rect.y0 + 25))
    shape.draw_circle((fig_rect.x0 + 30, fig_rect.y1 - 35), 8)
    shape.finish(color=(0.6, 0.2, 0.2), width=1.0)
    shape.commit()
    page.insert_text(
        (fig_rect.x0, fig_rect.y1 + 16),
        "Figure 1: Measurement setup with swept source and receiver.",
        fontsize=8.5,
        fontname="heit",
    )

    page = doc.new_page(width=A4.width, height=A4.height)
    left_col2 = pymupdf.Rect(MARGIN, 90, MARGIN + col_width, 640)
    right_col2 = pymupdf.Rect(MARGIN + col_width + col_gap, 90, A4.width - MARGIN, 640)
    page.insert_textbox(
        left_col2,
        "Results across forty fabricated stacks show that the model predicts "
        "the first resonance within two percent once layer thickness is "
        "measured rather than assumed. The residual error correlates with "
        "surface roughness, suggesting scattering as the next refinement.",
        fontsize=10,
        fontname="helv",
    )
    page.insert_textbox(
        right_col2,
        "We conclude that a two-parameter model - impedance contrast and "
        "loss tangent - captures the dominant behaviour of layered stacks "
        "for engineering purposes. Higher-order corrections matter only "
        "near grazing incidence.",
        fontsize=10,
        fontname="helv",
    )

    doc.set_metadata(FIXED_METADATA)
    doc.save(path, deflate=True)
    doc.close()


def make_mixed_media_brief(path: Path) -> None:
    doc = pymupdf.open()

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 80), "Product Brief: Helios Sensor Array", fontsize=17, fontname="hebo")
    page.insert_text((MARGIN, 110), "Key Specifications", fontsize=13, fontname="hebo")
    page.insert_text((MARGIN, 132), "Sampling rate: ", fontsize=10, fontname="helv")
    page.insert_text((MARGIN + 76, 132), "4 kHz", fontsize=10, fontname="hebo")
    page.insert_text((MARGIN + 112, 132), " per channel, 32 channels total.", fontsize=10, fontname="helv")
    page.insert_text((MARGIN, 150), "Noise floor: 0.8 uV", fontsize=10, fontname="helv")
    page.insert_text((MARGIN + 96, 146.5), "2", fontsize=6.5, fontname="helv")
    page.insert_text((MARGIN + 102, 150), " under shielded conditions.", fontsize=10, fontname="helv")

    img_rect = pymupdf.Rect(MARGIN, 180, MARGIN + 192, 324)
    page.insert_image(img_rect, stream=_tiny_png())
    page.insert_text(
        (MARGIN, 340),
        "Image 1: Thermal distribution across the array face.",
        fontsize=8.5,
        fontname="heit",
    )

    page.insert_text((MARGIN, 380), "Deployment Notes", fontsize=13, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 395, A4.width - MARGIN, 520),
        "The array ships pre-calibrated. Field recalibration is only required "
        "after mechanical shock or when operating temperature moves outside "
        "the rated band for more than an hour. Mount the unit on the vibration "
        "isolators provided; rigid mounting couples structural noise directly "
        "into the low-frequency channels and can mask weak signals entirely.",
        fontsize=10,
        fontname="helv",
    )

    page = doc.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 80), "Integration Checklist", fontsize=13, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 95, A4.width - MARGIN, 260),
        "Verify supply ripple stays below the published limit before "
        "connecting the analog front end. Route the sensor harness away from "
        "switching converters. Confirm that the acquisition host applies the "
        "vendor timestamp rather than re-stamping frames on arrival, since "
        "downstream alignment assumes source timestamps.\n\n"
        "After first power-up, run the built-in self test and archive the "
        "report it produces. The report is the baseline used by support when "
        "diagnosing drift claims during the warranty period.",
        fontsize=10,
        fontname="helv",
    )
    page.insert_text((MARGIN, 300), "2", fontsize=6.5, fontname="helv")
    page.insert_text((MARGIN + 6, 303.5), " Measured with inputs shorted at the connector.", fontsize=8.5, fontname="helv")

    doc.set_metadata(FIXED_METADATA)
    doc.save(path, deflate=True)
    doc.close()


def make_scanned_note(path: Path) -> None:
    """Simulate a scanned page: render a text page to a bitmap, embed image-only."""
    source = pymupdf.open()
    page = source.new_page(width=A4.width, height=A4.height)
    page.insert_text((MARGIN, 100), "MAINTENANCE NOTICE", fontsize=20, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(MARGIN, 130, A4.width - MARGIN, 420),
        "The cooling loop on line 3 will be serviced on Friday. All sensors "
        "connected to junction box B must be powered down before 08:00. "
        "Contact the shift supervisor to confirm the lockout checklist has "
        "been completed and signed.",
        fontsize=14,
        fontname="helv",
    )
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    source.close()

    scanned = pymupdf.open()
    scan_page = scanned.new_page(width=A4.width, height=A4.height)
    scan_page.insert_image(pymupdf.Rect(0, 0, A4.width, A4.height), stream=pixmap.tobytes("png"))
    scanned.set_metadata(FIXED_METADATA)
    scanned.save(path, deflate=True)
    scanned.close()


def main() -> None:
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    make_annual_report(REAL_DIR / "annual-report.pdf")
    make_two_column_article(REAL_DIR / "two-column-article.pdf")
    make_mixed_media_brief(REAL_DIR / "mixed-media-brief.pdf")
    make_scanned_note(REAL_DIR / "scanned-note.pdf")
    print(f"Fixtures written to {REAL_DIR}")


if __name__ == "__main__":
    main()
