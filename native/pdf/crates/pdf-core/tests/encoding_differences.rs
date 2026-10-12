use pdf_core::{ErrorCode, ParseLimits, PdfDocument, TextExtractionOptions};

fn pdf(differences: &str, extra: &[&str]) -> Vec<u8> {
    let content = b"BT /F1 12 Tf 72 720 Td (A) Tj ET";
    let mut stream = format!("<< /Length {} >>\nstream\n", content.len()).into_bytes();
    stream.extend_from_slice(content);
    stream.extend_from_slice(b"\nendstream");
    let mut objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".to_vec(),
        format!("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /BaseEncoding /WinAnsiEncoding /Differences {differences} >> >>").into_bytes(),
        stream,
    ];
    objects.extend(extra.iter().map(|value| value.as_bytes().to_vec()));
    let mut bytes = b"%PDF-1.4\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(bytes.len());
        bytes.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        bytes.extend_from_slice(object);
        bytes.extend_from_slice(b"\nendobj\n");
    }
    let xref = bytes.len();
    bytes.extend_from_slice(
        format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).as_bytes(),
    );
    for offset in offsets {
        bytes.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    bytes.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    bytes
}

#[test]
fn direct_and_indirect_differences_preserve_glyph_mapping() {
    for (differences, extra) in [
        ("[65 /Z]", vec![]),
        ("6 0 R", vec!["[65 /Z]"]),
        ("6 0 R", vec!["7 0 R", "[65 /Z]"]),
    ] {
        let document = PdfDocument::parse(&pdf(differences, &extra)).expect("valid PDF");
        let result = document
            .extract_text(TextExtractionOptions::default())
            .expect("font mapping");
        assert_eq!(result.text.trim(), "Z");
    }
}

#[test]
fn cyclic_differences_fail_with_a_stable_error() {
    for extra in [vec!["6 0 R"], vec!["7 0 R", "6 0 R"]] {
        let document = PdfDocument::parse(&pdf("6 0 R", &extra)).expect("valid structure");
        let error = document
            .extract_text(TextExtractionOptions::default())
            .expect_err("cycle");
        assert_eq!(error.code, ErrorCode::InvalidReference);
        assert!(error.message.contains("cyclic font Encoding reference"));
    }
}

#[test]
fn differences_reference_depth_is_bounded() {
    let limits = ParseLimits {
        max_object_depth: 8,
        ..ParseLimits::default()
    };
    let mut extra = (7..=15).map(|id| format!("{id} 0 R")).collect::<Vec<_>>();
    extra.push("[65 /Z]".to_string());
    let values = extra.iter().map(String::as_str).collect::<Vec<_>>();
    let document =
        PdfDocument::parse_with_limits(&pdf("6 0 R", &values), limits).expect("valid structure");
    let error = document
        .extract_text(TextExtractionOptions::default())
        .expect_err("depth limit");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn malformed_differences_still_fail() {
    for value in ["42", "[65 true]", "[/Z]", "[256 /Z]"] {
        let document = PdfDocument::parse(&pdf("6 0 R", &[value])).expect("valid structure");
        let error = document
            .extract_text(TextExtractionOptions::default())
            .expect_err("invalid Differences");
        assert_eq!(error.code, ErrorCode::InvalidObject);
    }
}
