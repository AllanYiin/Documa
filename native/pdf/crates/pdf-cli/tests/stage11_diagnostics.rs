use std::{fs, process::Command};

fn minimal_pdf() -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let object_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");
    let xref_offset = pdf.len();
    pdf.extend_from_slice(
        format!(
            "xref\n0 2\n0000000000 65535 f\n{object_offset:010} 00000 n\n\
             trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        )
        .as_bytes(),
    );
    pdf
}

#[test]
fn validate_diagnostics_are_opt_in_and_machine_readable() {
    let path = std::env::temp_dir().join(format!(
        "rust-pdf-cli-stage11-diagnostics-{}.pdf",
        std::process::id()
    ));
    fs::write(&path, minimal_pdf()).expect("write temporary fixture");

    let json_output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args([
            "validate",
            path.to_str().expect("UTF-8 temp path"),
            "--json",
            "--diagnostics",
        ])
        .output()
        .expect("run JSON diagnostics");
    assert!(json_output.status.success());
    assert!(json_output.stderr.is_empty());
    let json: serde_json::Value =
        serde_json::from_slice(&json_output.stdout).expect("valid diagnostics JSON");
    assert_eq!(json["ok"], true);
    assert_eq!(json["validated_objects"], 1);
    assert_eq!(json["decode_metrics"]["decoded_bytes"], 0);
    assert_eq!(json["decode_metrics"]["object_stream_cache_hits"], 0);
    assert_eq!(json["decode_metrics"]["peak_object_stream_cache_bytes"], 0);

    let plain_output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args([
            "validate",
            path.to_str().expect("UTF-8 temp path"),
            "--diagnostics",
        ])
        .output()
        .expect("run plain diagnostics");
    fs::remove_file(&path).expect("remove temporary fixture");
    assert!(plain_output.status.success());
    assert!(String::from_utf8_lossy(&plain_output.stdout).starts_with("valid: parsed 1"));
    let stderr = String::from_utf8_lossy(&plain_output.stderr);
    assert!(stderr.starts_with("diagnostics: {"));
    assert!(stderr.contains("\"decoded_bytes\":0"));
}
