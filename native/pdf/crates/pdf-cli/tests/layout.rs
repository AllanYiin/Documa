use std::{fs, process::Command};

#[test]
fn layout_command_exposes_versioned_layout_ir_and_summary() {
    let path = std::env::temp_dir().join(format!("rust-pdf-cli-layout-{}.pdf", std::process::id()));
    fs::write(&path, text_pdf()).expect("write generated fixture");
    let path_text = path.to_str().expect("UTF-8 temp path");

    let json_output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path_text, "--json", "--debug-glyphs"])
        .output()
        .expect("run layout command");
    assert!(
        json_output.status.success(),
        "{}",
        String::from_utf8_lossy(&json_output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&json_output.stdout).expect("valid Layout IR JSON");
    assert_eq!(value["schema_version"], 1);
    assert_eq!(value["coordinate_space"], "layout_unrotated_top_left");
    assert_eq!(value["text"], "AB");
    assert_eq!(value["pages"][0]["orders"]["source_order"][0], "p0-n0");
    assert_eq!(
        value["pages"][0]["semantic_nodes"][0]["spans"][0]["origin"]["x"],
        20.0
    );
    assert_eq!(
        value["pages"][0]["semantic_nodes"][0]["spans"][0]["origin"]["y"],
        40.0
    );
    assert_eq!(
        value["pages"][0]["debug_glyphs"]
            .as_array()
            .expect("debug glyph array")
            .len(),
        2
    );

    let summary = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path_text])
        .output()
        .expect("run layout summary");
    assert!(summary.status.success());
    let summary = String::from_utf8(summary.stdout).expect("UTF-8 summary");
    assert!(summary.contains("Layout IR v1"));
    assert!(summary.contains("layout_unrotated_top_left"));

    fs::remove_file(path).expect("remove generated fixture");
}

#[test]
fn layout_command_exposes_stage2_tagged_schema() {
    let path = std::env::temp_dir().join(format!(
        "rust-pdf-cli-tagged-layout-{}.pdf",
        std::process::id()
    ));
    fs::write(&path, tagged_text_pdf()).expect("write tagged fixture");
    let path_text = path.to_str().expect("UTF-8 temp path");
    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path_text, "--json"])
        .output()
        .expect("run tagged layout command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("valid tagged Layout IR JSON");
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

    fs::remove_file(path).expect("remove generated tagged fixture");
}
#[test]
fn layout_command_exposes_stage4_table_schema() {
    let path = std::env::temp_dir().join(format!(
        "rust-pdf-cli-table-layout-{}.pdf",
        std::process::id()
    ));
    fs::write(&path, ruled_table_pdf()).expect("write table fixture");
    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path.to_str().expect("UTF-8 temp path"), "--json"])
        .output()
        .expect("run table layout command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("valid table Layout IR JSON");
    assert_eq!(value["capabilities"]["tables"], true);
    let table = &value["pages"][0]["tables"][0];
    assert_eq!(table["evidence"], "vector_lattice");
    assert_eq!(
        (table["rows"].as_u64(), table["columns"].as_u64()),
        (Some(2), Some(2))
    );
    assert_eq!(table["cells"][0]["text"], "A");
    assert_eq!(table["cells"][3]["text"], "D");
    fs::remove_file(path).expect("remove generated table fixture");
}

#[test]
fn layout_command_exposes_stage5_image_placement_schema() {
    let path = std::env::temp_dir().join(format!(
        "rust-pdf-cli-image-layout-{}.pdf",
        std::process::id()
    ));
    fs::write(&path, image_pdf()).expect("write image fixture");
    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path.to_str().expect("UTF-8 temp path"), "--json"])
        .output()
        .expect("run image layout command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("valid image Layout IR JSON");
    assert_eq!(value["capabilities"]["image_placements"], true);
    let placement = &value["pages"][0]["image_placements"][0];
    assert_eq!(placement["id"], "p0-i0");
    assert_eq!(placement["paint_ordinal"], 0);
    assert_eq!(placement["resource_name"], "Im");
    assert_eq!(placement["object"]["number"], 6);
    assert_eq!(
        placement["bbox"],
        serde_json::json!({
            "x0": 20.0, "y0": 120.0, "x1": 120.0, "y1": 170.0
        })
    );
    assert_eq!(placement["tag"], "Figure");
    assert_eq!(placement["structure_object"]["number"], 8);
    assert_eq!(placement["alt_text"], "author alt");
    assert_eq!(placement["source_node_ids"], serde_json::json!(["p0-n0"]));
    assert_eq!(placement["rule_id"], "stage5b_tagged_figure_v1");
    fs::remove_file(path).expect("remove generated image fixture");
}

#[test]
fn layout_command_exposes_stage5_navigation_schema() {
    let path = std::env::temp_dir().join(format!(
        "rust-pdf-cli-navigation-layout-{}.pdf",
        std::process::id()
    ));
    fs::write(&path, navigation_pdf()).expect("write navigation fixture");
    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["layout", path.to_str().expect("UTF-8 temp path"), "--json"])
        .output()
        .expect("run navigation layout command");
    assert!(output.status.success());
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("valid navigation Layout IR JSON");
    assert_eq!(value["capabilities"]["navigation"], true);
    assert_eq!(value["pages"][0]["links"][0]["target"]["kind"], "uri");
    assert_eq!(
        value["pages"][0]["links"][1]["target"]["destination_name"],
        "chapter"
    );
    assert_eq!(value["named_destinations"][0]["name"], "chapter");
    assert_eq!(value["named_destinations"][0]["target"]["page_index"], 0);
    assert_eq!(value["outlines"][0]["title"], "Intro");
    fs::remove_file(path).expect("remove generated navigation fixture");
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

fn text_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 1 0 0 1 20 200 Tm (AB) Tj ET";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 210 380] \
          /CropBox [10 20 110 220] /UserUnit 2 /Rotate 90 \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ];
    classic_pdf(&objects)
}

fn tagged_text_pdf() -> Vec<u8> {
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
