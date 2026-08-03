import pytest

from rust_pdf import (
    PdfParseError,
    extract,
    extract_layout,
    extract_layout_stream,
    extract_v2,
)


def test_v2_modes_and_legacy_shape():
    data = _reverse_position_pdf()

    legacy = extract(data)
    assert legacy["text"] == "A B"
    assert "mode" not in legacy
    assert "quality" not in legacy

    expected = {
        "content-order": "BA",
        "layout": "A B",
        "auto": "A B",
    }
    for mode, text in expected.items():
        result = extract_v2(data, mode=mode)
        assert result["mode"] == mode
        assert result["text"] == text
        assert len(result["pages"]) == 1
        assert [glyph["source_ordinal"] for glyph in result["glyphs"]] == [0, 1]
        assert [warning["code"] for warning in result["warnings"]] == [
            warning["code"] for warning in legacy["warnings"]
        ]
        assert result["quality"] == {
            "inserted_spaces": 1 if mode == "auto" else 0,
            "inserted_line_breaks": 0,
            "fallback_glyphs": 2,
            "replacement_characters": 0,
            "ambiguous_boundaries": 0,
        }

    without_quality = extract_v2(data, quality=False)
    assert without_quality["text"] == "A B"
    assert "quality" not in without_quality


def test_stage12_layout_ir_schema_and_coordinates():
    result = extract_layout(_reverse_position_pdf(), debug_glyphs=True)
    assert result["schema_version"] == 1
    assert result["coordinate_space"] == "layout_unrotated_top_left"
    assert result["text"] == "BA"
    assert result["capabilities"]["source_order"] is True
    assert result["capabilities"]["inferred_order"] is True
    page = result["pages"][0]
    assert page["orders"]["source_order"] == ["p0-n0"]
    assert page["orders"]["inferred_order"] == ["p0-n0"]
    assert page["orders"]["main_flow"] == ["p0-n0"]
    assert page["debug_glyphs"][0]["origin"] == {"x": 24.0, "y": 92.0}
    assert page["debug_glyphs"][1]["origin"] == {"x": 10.0, "y": 92.0}


def test_stage12_layout_stream_drains_pages_with_metadata_parity():
    data = _reverse_position_pdf()
    complete = extract_layout(data, debug_glyphs=True)
    stream = extract_layout_stream(data, debug_glyphs=True)

    assert stream.metadata["schema_version"] == complete["schema_version"]
    assert stream.metadata["coordinate_space"] == complete["coordinate_space"]
    assert stream.metadata["page_count"] == len(complete["pages"])
    assert stream.metadata["streaming"] == {
        "page_transfer": "native_events_v2",
        "metadata_finalized": False,
        "page_finalization": "draining_stable_id_patches_v1",
        "document_text_omitted": True,
    }
    assert "text" not in stream.metadata
    assert "pages" not in stream.metadata
    assert stream.remaining_pages == 1
    pages = list(stream)
    assert stream.remaining_pages == 0
    assert stream.metadata["streaming"]["metadata_finalized"] is True
    assert stream.metadata["capabilities"] == complete["capabilities"]
    assert stream.metadata["warnings"] == complete["warnings"]
    assert stream.metadata["named_destinations"] == complete["named_destinations"]
    assert stream.metadata["outlines"] == complete["outlines"]
    assert stream.remaining_finalizations == len(pages)
    for finalization in stream.finalizations():
        page = pages[finalization["page_index"]]
        nodes = {node["id"]: node for node in page["semantic_nodes"]}
        for update in finalization["node_updates"]:
            node = nodes[update["node_id"]]
            node["role"] = update["role"]
            node["confidence"] = update["confidence"]
            node["rule_id"] = update["rule_id"]
        page["orders"]["main_flow"] = finalization["main_flow"]
    assert stream.remaining_finalizations == 0
    assert pages == complete["pages"]
    assert list(stream) == []

def test_stage12_tagged_structure_schema_is_exposed():
    result = extract_layout(_tagged_pdf())
    assert result["text"] == "Visible"
    assert result["capabilities"]["tagged_order"] is True
    assert result["capabilities"]["semantic_roles"] is True
    page = result["pages"][0]
    assert page["orders"]["tagged_order"] == ["p0-n0"]
    node = page["semantic_nodes"][0]
    assert node["tag"] == "CustomP"
    assert node["role"] == "paragraph"
    assert node["alt_text"] == "description only"
    assert node["structure_object"]["number"] == 7

def test_stage12_table_schema_is_exposed():
    result = extract_layout(_ruled_table_pdf())
    assert result["capabilities"]["tables"] is True
    table = result["pages"][0]["tables"][0]
    assert table["evidence"] == "vector_lattice"
    assert (table["rows"], table["columns"], len(table["cells"])) == (2, 2, 4)
    assert [cell["text"] for cell in table["cells"]] == ["A", "B", "C", "D"]
    assert all(cell["bbox"] for cell in table["cells"])


def test_stage12_image_placement_schema_is_exposed():
    result = extract_layout(_image_pdf())
    assert result["capabilities"]["image_placements"] is True
    placement = result["pages"][0]["image_placements"][0]
    assert placement["id"] == "p0-i0"
    assert placement["paint_ordinal"] == 0
    assert placement["resource_name"] == "Im"
    assert placement["object"]["number"] == 6
    assert placement["bbox"] == {"x0": 20.0, "y0": 120.0, "x1": 120.0, "y1": 170.0}
    assert placement["quad"]["top_left"] == {"x": 20.0, "y": 120.0}
    assert placement["tag"] == "Figure"
    assert placement["structure_object"]["number"] == 8
    assert placement["alt_text"] == "author alt"
    assert placement["source_node_ids"] == ["p0-n0"]
    assert placement["rule_id"] == "stage5b_tagged_figure_v1"


def test_stage12_navigation_schema_is_exposed():
    result = extract_layout(_navigation_pdf())
    assert result["capabilities"]["navigation"] is True
    assert result["pages"][0]["links"][0]["target"]["kind"] == "uri"
    assert result["pages"][0]["links"][1]["target"]["destination_name"] == "chapter"
    assert result["named_destinations"][0]["name"] == "chapter"
    assert result["named_destinations"][0]["target"]["page_index"] == 0
    assert result["outlines"][0]["title"] == "Intro"


def test_v2_invalid_mode_has_stable_error_code():
    with pytest.raises(PdfParseError, match="invalid_option"):
        extract_v2(_reverse_position_pdf(), mode="automatic")


def _navigation_pdf() -> bytes:
    return _classic_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R /Dests << /chapter [3 0 R /Fit] >> /Outlines 6 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R 5 0 R] >>",
            b"<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] /A << /S /URI /URI (https://example.invalid) >> >>",
            b"<< /Type /Annot /Subtype /Link /Rect [10 40 60 60] /Dest /chapter >>",
            b"<< /Type /Outlines /First 7 0 R /Last 7 0 R >>",
            b"<< /Title (Intro) /Dest /chapter >>",
        ]
    )


def _image_pdf() -> bytes:
    content = b"/Figure << /MCID 0 >> BDC q 100 0 0 50 20 30 cm /Im Do Q BT /F1 10 Tf 1 0 0 1 20 20 Tm (Figure 1) Tj ET EMC"
    image = (
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n"
        + bytes([0])
        + b"\nendstream"
    )
    return _classic_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 7 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Rotate 90 /StructParents 0 /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>",
            f"<< /Length {len(content)} >>\nstream\n".encode()
            + content
            + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            image,
            b"<< /Type /StructTreeRoot /K 8 0 R /ParentTree 9 0 R >>",
            b"<< /Type /StructElem /S /Figure /Pg 3 0 R /Alt (author alt) /K 0 >>",
            b"<< /Nums [0 [8 0 R]] >>",
        ]
    )


def _ruled_table_pdf() -> bytes:
    content = (
        b"50 50 200 200 re S 50 150 m 250 150 l S 150 50 m 150 250 l S "
        b"BT /F1 10 Tf 1 0 0 1 70 200 Tm (A) Tj 1 0 0 1 170 200 Tm (B) Tj "
        b"1 0 0 1 70 100 Tm (C) Tj 1 0 0 1 170 100 Tm (D) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    return _classic_pdf(objects)


def _tagged_pdf() -> bytes:
    content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n".encode()
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /StructTreeRoot /RoleMap << /CustomP /P >> /K 7 0 R /ParentTree 8 0 R >>",
        b"<< /Type /StructElem /S /CustomP /Pg 3 0 R /Alt (description only) /K 0 >>",
        b"<< /Nums [0 [7 0 R]] >>",
    ]
    return _classic_pdf(objects)


def _classic_pdf(objects: list[bytes]) -> bytes:
    pdf = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode())
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(pdf)

def _reverse_position_pdf() -> bytes:
    content = (
        b"BT /F1 12 Tf "
        b"1 0 0 1 24 700 Tm (B) Tj "
        b"1 0 0 1 10 700 Tm (A) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            f"<< /Length {len(content)} >>\nstream\n".encode()
            + content
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
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
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(pdf)
