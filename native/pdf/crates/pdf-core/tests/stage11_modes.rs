use pdf_core::{
    ErrorCode, ExtractionMode, PdfDocument, SeparatorOrigin, TextExtractionOptions,
    TextExtractionOptionsV2, TextQuality,
};

#[test]
fn v2_modes_preserve_legacy_mapping_and_auto_adds_traced_separator() {
    let document = PdfDocument::parse(&reverse_position_pdf()).expect("valid fixture");

    let legacy_content = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .expect("legacy content order");
    let legacy_layout = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: true,
        })
        .expect("legacy layout");

    let content = document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode: ExtractionMode::ContentOrder,
            include_quality_metadata: true,
        })
        .expect("V2 content order");
    let layout = document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode: ExtractionMode::Layout,
            include_quality_metadata: true,
        })
        .expect("V2 layout");
    let auto = document
        .extract_text_v2(TextExtractionOptionsV2::default())
        .expect("V2 auto");

    assert_eq!(content.text, "BA");
    assert_eq!(layout.text, "A B");
    assert_eq!(content.text, legacy_content.text);
    assert_eq!(layout.text, legacy_layout.text);
    assert_eq!(auto.text, layout.text);
    for result in [&content, &layout, &auto] {
        assert_eq!(result.pages.len(), 1);
        assert_eq!(
            result
                .warnings
                .iter()
                .map(|warning| warning.code.as_str())
                .collect::<Vec<_>>(),
            vec!["font_fallback_encoding"]
        );
    }
    assert_eq!(content.warnings, legacy_content.warnings);
    assert_eq!(layout.warnings, legacy_layout.warnings);
    assert_eq!(auto.warnings, layout.warnings);
    assert_eq!(
        content
            .warnings
            .iter()
            .map(|warning| warning.code.as_str())
            .collect::<Vec<_>>(),
        vec!["font_fallback_encoding"]
    );
    assert_eq!(
        content.quality,
        Some(TextQuality {
            fallback_glyphs: 2,
            ..TextQuality::default()
        })
    );
    assert_eq!(layout.quality, content.quality);
    assert_eq!(
        auto.quality,
        Some(TextQuality {
            inserted_spaces: 1,
            fallback_glyphs: 2,
            ..TextQuality::default()
        })
    );
    assert_eq!(auto.separators.len(), 1);
    assert_eq!(auto.separators[0].origin, SeparatorOrigin::GeometrySpace);
}

#[test]
fn quality_metadata_can_be_omitted_without_changing_text() {
    let document = PdfDocument::parse(&reverse_position_pdf()).expect("valid fixture");
    let with_quality = document
        .extract_text_v2(TextExtractionOptionsV2::default())
        .expect("quality enabled");
    let without_quality = document
        .extract_text_v2(TextExtractionOptionsV2 {
            include_quality_metadata: false,
            ..TextExtractionOptionsV2::default()
        })
        .expect("quality disabled");

    assert_eq!(without_quality.text, with_quality.text);
    assert_eq!(without_quality.pages, with_quality.pages);
    assert_eq!(without_quality.warnings, with_quality.warnings);
    assert!(without_quality.quality.is_none());
}

#[test]
fn mode_names_and_invalid_option_error_are_stable() {
    assert_eq!(ExtractionMode::ContentOrder.as_str(), "content-order");
    assert_eq!(ExtractionMode::Layout.as_str(), "layout");
    assert_eq!(ExtractionMode::Auto.as_str(), "auto");

    assert_eq!(ErrorCode::InvalidOption.as_str(), "invalid_option");
}

fn reverse_position_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 1 0 0 1 24 700 Tm (B) Tj 1 0 0 1 10 700 Tm (A) Tj ET";
    let objects = vec![
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
