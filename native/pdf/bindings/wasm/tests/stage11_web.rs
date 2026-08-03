#![cfg(target_arch = "wasm32")]

use serde::Serialize;
use wasm_bindgen::JsValue;
use wasm_bindgen_test::wasm_bindgen_test;

#[wasm_bindgen_test]
fn v2_modes_and_quality_are_exposed() {
    let data = reverse_position_pdf();
    for (mode, expected) in [("content-order", "BA"), ("layout", "A B"), ("auto", "A B")] {
        let options = js_options(&serde_json::json!({
            "mode": mode,
            "quality": true,
        }));
        let result =
            pdf_wasm::extract_with_options(&data, options).expect("extract with V2 options");
        let value: serde_json::Value =
            serde_wasm_bindgen::from_value(result).expect("deserialize V2 result");
        assert_eq!(value["mode"], mode);
        assert_eq!(value["text"], expected);
        assert_eq!(value["glyphs"][0]["source_ordinal"], 0);
        assert_eq!(value["glyphs"][1]["source_ordinal"], 1);
        assert_eq!(value["pages"].as_array().expect("pages array").len(), 1);
        assert_eq!(
            value["warnings"]
                .as_array()
                .expect("warnings array")
                .iter()
                .map(|warning| warning["code"].as_str().expect("warning code"))
                .collect::<Vec<_>>(),
            vec!["font_fallback_encoding"]
        );
        assert_eq!(
            value["quality"],
            serde_json::json!({
                "inserted_spaces": usize::from(mode == "auto"),
                "inserted_line_breaks": 0,
                "fallback_glyphs": 2,
                "replacement_characters": 0,
                "ambiguous_boundaries": 0,
            })
        );
    }
}

#[wasm_bindgen_test]
fn stage12_layout_ir_schema_and_coordinates_are_exposed() {
    let options = js_options(&serde_json::json!({
        "debugGlyphs": true,
    }));
    let result =
        pdf_wasm::extract_layout(&reverse_position_pdf(), options).expect("extract Layout IR");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(result).expect("deserialize Layout IR");
    assert_eq!(value["schema_version"], 1);
    assert_eq!(value["coordinate_space"], "layout_unrotated_top_left");
    assert_eq!(value["text"], "BA");
    assert_eq!(value["pages"][0]["orders"]["source_order"][0], "p0-n0");
    assert_eq!(
        value["pages"][0]["orders"]["inferred_order"],
        serde_json::json!(["p0-n0"])
    );
    let origin = &value["pages"][0]["debug_glyphs"][0]["origin"];
    assert_eq!(origin["x"].as_f64(), Some(24.0));
    assert_eq!(origin["y"].as_f64(), Some(92.0));
}
#[wasm_bindgen_test]
fn stage12_tagged_structure_schema_is_exposed() {
    let result = pdf_wasm::extract_layout(&tagged_pdf(), js_options(&serde_json::json!({})))
        .expect("extract tagged Layout IR");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(result).expect("deserialize tagged Layout IR");
    assert_eq!(value["text"], "Visible");
    assert_eq!(value["capabilities"]["tagged_order"], true);
    assert_eq!(value["capabilities"]["semantic_roles"], true);
    assert_eq!(
        value["pages"][0]["orders"]["tagged_order"],
        serde_json::json!(["p0-n0"])
    );
    let node = &value["pages"][0]["semantic_nodes"][0];
    assert_eq!(node["tag"], "CustomP");
    assert_eq!(node["role"], "paragraph");
    assert_eq!(node["alt_text"], "description only");
    assert_eq!(node["structure_object"]["number"], 7);
}
#[wasm_bindgen_test]
fn stage12_table_schema_is_exposed() {
    let result = pdf_wasm::extract_layout(&ruled_table_pdf(), js_options(&serde_json::json!({})))
        .expect("extract table Layout IR");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(result).expect("deserialize table Layout IR");
    assert_eq!(value["capabilities"]["tables"], true);
    let table = &value["pages"][0]["tables"][0];
    assert_eq!(table["evidence"], "vector_lattice");
    assert_eq!(
        (table["rows"].as_u64(), table["columns"].as_u64()),
        (Some(2), Some(2))
    );
    assert_eq!(table["cells"][0]["text"], "A");
    assert_eq!(table["cells"][3]["text"], "D");
}

#[wasm_bindgen_test]
fn stage12_image_placement_schema_is_exposed() {
    let result = pdf_wasm::extract_layout(&image_pdf(), js_options(&serde_json::json!({})))
        .expect("extract image Layout IR");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(result).expect("deserialize image Layout IR");
    assert_eq!(value["capabilities"]["image_placements"], true);
    let placement = &value["pages"][0]["image_placements"][0];
    assert_eq!(placement["id"], "p0-i0");
    assert_eq!(placement["paint_ordinal"], 0);
    assert_eq!(placement["resource_name"], "Im");
    assert_eq!(placement["object"]["number"], 6);
    assert_eq!(placement["bbox"]["x0"].as_f64(), Some(20.0));
    assert_eq!(placement["bbox"]["y0"].as_f64(), Some(120.0));
    assert_eq!(placement["bbox"]["x1"].as_f64(), Some(120.0));
    assert_eq!(placement["bbox"]["y1"].as_f64(), Some(170.0));
    assert_eq!(placement["quad"]["top_left"]["x"], 20.0);
    assert_eq!(placement["quad"]["top_left"]["y"], 120.0);
    assert_eq!(placement["tag"], "Figure");
    assert_eq!(placement["structure_object"]["number"], 8);
    assert_eq!(placement["alt_text"], "author alt");
    assert_eq!(placement["source_node_ids"], serde_json::json!(["p0-n0"]));
    assert_eq!(placement["rule_id"], "stage5b_tagged_figure_v1");
}

#[wasm_bindgen_test]
fn stage12_navigation_schema_is_exposed() {
    let result = pdf_wasm::extract_layout(&navigation_pdf(), js_options(&serde_json::json!({})))
        .expect("extract navigation Layout IR");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(result).expect("deserialize navigation Layout IR");
    assert_eq!(value["capabilities"]["navigation"], true);
    assert_eq!(value["pages"][0]["links"][0]["target"]["kind"], "uri");
    assert_eq!(
        value["pages"][0]["links"][1]["target"]["destination_name"],
        "chapter"
    );
    assert_eq!(value["named_destinations"][0]["name"], "chapter");
    assert_eq!(value["named_destinations"][0]["target"]["page_index"], 0);
    assert_eq!(value["outlines"][0]["title"], "Intro");
}

#[wasm_bindgen_test]
fn stage12_wasm_timings_fail_without_panicking() {
    let options = js_options(&serde_json::json!({
        "timings": true,
    }));
    let error = pdf_wasm::extract_layout(&reverse_position_pdf(), options)
        .expect_err("WASM timing is unsupported");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(error).expect("deserialize timing error");
    assert_eq!(value["code"], "unsupported_feature");
}
#[wasm_bindgen_test]
fn invalid_mode_returns_stable_error_code() {
    let options = js_options(&serde_json::json!({
        "mode": "automatic",
    }));
    let error = pdf_wasm::extract_with_options(&reverse_position_pdf(), options)
        .expect_err("reject unsupported mode");
    let value: serde_json::Value =
        serde_wasm_bindgen::from_value(error).expect("deserialize structured error");
    assert_eq!(value["code"], "invalid_option");
}

fn js_options(value: &serde_json::Value) -> JsValue {
    value
        .serialize(&serde_wasm_bindgen::Serializer::json_compatible())
        .expect("serialize JavaScript options object")
}

fn reverse_position_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 1 0 0 1 24 700 Tm (B) Tj 1 0 0 1 10 700 Tm (A) Tj ET";
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
    ];
    classic_pdf(&objects)
}

fn navigation_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /Dests << /chapter [3 0 R /Fit] >> /Outlines 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R 5 0 R] >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 10 60 30] /A << /S /URI /URI (https://example.invalid) >> >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 40 60 60] /Dest /chapter >>".to_vec(),
        b"<< /Type /Outlines /First 7 0 R /Last 7 0 R >>".to_vec(),
        b"<< /Title (Intro) /Dest /chapter >>".to_vec(),
    ])
}

fn image_pdf() -> Vec<u8> {
    let mut image = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n".to_vec();
    image.push(0);
    image.extend_from_slice(b"\nendstream");
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 7 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Rotate 90 /StructParents 0 /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(b"/Figure << /MCID 0 >> BDC q 100 0 0 50 20 30 cm /Im Do Q BT /F1 10 Tf 1 0 0 1 20 20 Tm (Figure 1) Tj ET EMC"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        image,
        b"<< /Type /StructTreeRoot /K 8 0 R /ParentTree 9 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /Figure /Pg 3 0 R /Alt (author alt) /K 0 >>".to_vec(),
        b"<< /Nums [0 [8 0 R]] >>".to_vec(),
    ])
}

fn ruled_table_pdf() -> Vec<u8> {
    let content = b"50 50 200 200 re S 50 150 m 250 150 l S 150 50 m 150 250 l S \
        BT /F1 10 Tf 1 0 0 1 70 200 Tm (A) Tj 1 0 0 1 170 200 Tm (B) Tj \
        1 0 0 1 70 100 Tm (C) Tj 1 0 0 1 170 100 Tm (D) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
    ])
}

fn tagged_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /RoleMap << /CustomP /P >> /K 7 0 R /ParentTree 8 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /CustomP /Pg 3 0 R /Alt (description only) /K 0 >>".to_vec(),
        b"<< /Nums [0 [7 0 R]] >>".to_vec(),
    ])
}
fn stream_body(data: &[u8]) -> Vec<u8> {
    let mut body = format!("<< /Length {} >>\nstream\n", data.len()).into_bytes();
    body.extend_from_slice(data);
    body.extend_from_slice(b"\nendstream");
    body
}

fn classic_pdf(objects: &[Vec<u8>]) -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::with_capacity(objects.len());
    for (index, body) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(body);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    pdf
}
