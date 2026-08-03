use std::{fs, process::Command};

fn geometry_pdf() -> Vec<u8> {
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 210 380] \
          /CropBox [10 20 110 220] /UserUnit 2 /Rotate 90 /Resources << >> >>"
            .to_vec(),
    ];
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::with_capacity(objects.len());
    for (index, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(object);
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

#[test]
fn geometry_command_emits_canonical_layout_contract() {
    let path =
        std::env::temp_dir().join(format!("rust-pdf-cli-geometry-{}.pdf", std::process::id()));
    fs::write(&path, geometry_pdf()).expect("write generated fixture");

    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args([
            "geometry",
            path.to_str().expect("UTF-8 temp path"),
            "--json",
        ])
        .output()
        .expect("run geometry command");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("valid JSON output");
    assert_eq!(value["coordinate_space"], "layout_unrotated_top_left");
    let geometry = &value["pages"][0]["geometry"];
    assert_eq!(geometry["coordinate_space"], "layout_unrotated_top_left");
    assert_eq!(geometry["rotation"], 90);
    assert_eq!(geometry["layout_bounds"]["x1"], 200.0);
    assert_eq!(geometry["layout_bounds"]["y1"], 400.0);
    assert_eq!(geometry["display_bounds"]["x1"], 400.0);
    assert_eq!(geometry["display_bounds"]["y1"], 200.0);
    assert_eq!(geometry["pdf_to_layout"]["a"], 2.0);
    assert_eq!(geometry["pdf_to_layout"]["d"], -2.0);
    assert_eq!(geometry["pdf_to_layout"]["e"], -20.0);
    assert_eq!(geometry["pdf_to_layout"]["f"], 440.0);

    fs::remove_file(path).expect("remove generated fixture");
}
