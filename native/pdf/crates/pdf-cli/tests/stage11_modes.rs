use std::{fs, process::Command};

#[test]
fn version_reports_stage_11_release_candidate() {
    let output = run(&["version", "--json"]);
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).expect("version JSON");
    assert_eq!(value["version"], "0.2.0");
    assert_eq!(value["stage"], "stage-11");
}
#[test]
fn v2_modes_are_available_without_changing_legacy_json_shape() {
    let path =
        std::env::temp_dir().join(format!("rust-pdf-cli-stage11-{}.pdf", std::process::id()));
    fs::write(&path, reverse_position_pdf()).expect("write generated fixture");
    let file = path.to_str().expect("UTF-8 temp path");

    let legacy = run(&["extract", file, "--json"]);
    assert!(legacy.status.success());
    let legacy_json: serde_json::Value =
        serde_json::from_slice(&legacy.stdout).expect("legacy JSON");
    assert_eq!(legacy_json["text"], "A B");
    assert!(legacy_json.get("mode").is_none());
    assert!(legacy_json.get("quality").is_none());

    for (mode, expected) in [("content-order", "BA"), ("layout", "A B"), ("auto", "A B")] {
        let output = run(&["extract", file, "--mode", mode, "--json"]);
        assert!(
            output.status.success(),
            "{mode}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let value: serde_json::Value =
            serde_json::from_slice(&output.stdout).expect("V2 JSON output");
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
            value["warnings"][0]["code"],
            legacy_json["warnings"][0]["code"]
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

    let conflict = run(&["extract", file, "--mode", "auto", "--no-layout"]);
    assert!(!conflict.status.success());
    assert!(String::from_utf8_lossy(&conflict.stderr).contains("cannot be used with"));

    let invalid = run(&["extract", file, "--mode", "automatic"]);
    assert!(!invalid.status.success());
    assert!(String::from_utf8_lossy(&invalid.stderr).contains("invalid value"));

    fs::remove_file(path).expect("remove generated fixture");
}

fn run(arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(arguments)
        .output()
        .expect("run rust-pdf")
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
