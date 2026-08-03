use std::{fs, process::Command};

fn text_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 10 10 Td (CLI text) Tj ET";
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> \
          /Contents 4 0 R >>"
            .to_vec(),
        {
            let mut stream = format!("<< /Length {} >>\nstream\n", content.len()).into_bytes();
            stream.extend_from_slice(content);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ];
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(object);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f\n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n\n").as_bytes());
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
fn extract_command_emits_text_and_structured_json() {
    let path =
        std::env::temp_dir().join(format!("rust-pdf-cli-extract-{}.pdf", std::process::id()));
    fs::write(&path, text_pdf()).expect("write generated fixture");

    let plain = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["extract", path.to_str().expect("UTF-8 temp path")])
        .output()
        .expect("run extract");
    assert!(plain.status.success());
    assert!(String::from_utf8_lossy(&plain.stdout).contains("CLI text"));

    let structured = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["extract", path.to_str().expect("UTF-8 temp path"), "--json"])
        .output()
        .expect("run JSON extract");
    assert!(structured.status.success());
    let value: serde_json::Value =
        serde_json::from_slice(&structured.stdout).expect("valid JSON output");
    assert_eq!(value["pages"][0]["text"], "CLI text");

    fs::remove_file(path).expect("remove generated fixture");
}
