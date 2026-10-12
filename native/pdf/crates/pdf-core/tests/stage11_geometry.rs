use pdf_core::{
    ErrorCode, ExtractionMode, ParseLimits, PdfDocument, TextExtractionOptions,
    TextExtractionOptionsV2, TextOrigin, WritingMode,
};

#[test]
fn simple_font_widths_spacing_scaling_and_source_order_have_stable_geometry() {
    let content = b"BT /F1 10 Tf 2 Tc 3 Tw 50 Tz 1 0 0 1 10 20 Tm (A B) Tj ET".to_vec();
    let font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
        /FirstChar 65 /Widths [600 700] \
        /FontDescriptor << /MissingWidth 400 >> >>"
        .to_vec();
    let document = PdfDocument::parse(&single_page_pdf(&content, &font)).expect("valid PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode: ExtractionMode::ContentOrder,
            include_quality_metadata: true,
        })
        .expect("positioned extraction");

    assert_eq!(result.text, "A B");
    assert_eq!(result.glyphs.len(), 3);
    assert_eq!(
        result
            .glyphs
            .iter()
            .map(|glyph| glyph.source_ordinal)
            .collect::<Vec<_>>(),
        vec![0, 1, 2]
    );
    assert_glyph(&result.glyphs[0], "A", [10.0, 20.0], [4.0, 0.0]);
    assert_glyph(&result.glyphs[1], " ", [14.0, 20.0], [4.5, 0.0]);
    assert_glyph(&result.glyphs[2], "B", [18.5, 20.0], [4.5, 0.0]);
    assert!(
        result
            .glyphs
            .iter()
            .all(|glyph| glyph.text_origin == TextOrigin::FontFallback)
    );
    assert_eq!(result.quality.expect("quality").fallback_glyphs, 3);
}

#[test]
fn text_matrix_tj_and_form_matrix_transform_origin_advance_and_baseline() {
    let form_content = b"BT /F1 10 Tf 0 1 -1 0 10 20 Tm [(A) 200 (B)] TJ ET".to_vec();
    let form = stream_with_dictionary(
        b"/Type /XObject /Subtype /Form /Matrix [2 0 0 2 5 7] \
          /Resources << /Font << /F1 6 0 R >> >>",
        &form_content,
    );
    let page_content = b"q /Fm1 Do Q".to_vec();
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(&page_content),
        form,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /FirstChar 65 /Widths [600 700] >>"
            .to_vec(),
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("valid Form PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("Form glyph geometry");

    assert_eq!(result.text, "AB");
    assert_eq!(result.glyphs.len(), 2);
    assert_glyph(&result.glyphs[0], "A", [25.0, 47.0], [0.0, 12.0]);
    assert_glyph(&result.glyphs[1], "B", [25.0, 55.0], [0.0, 14.0]);
    assert!((result.glyphs[0].baseline[0] - 0.0).abs() <= 1.0e-9);
    assert!((result.glyphs[0].baseline[1] - 1.0).abs() <= 1.0e-9);
    assert_eq!(result.glyphs[0].rotation_bucket, 90);

    let legacy = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .expect("legacy projection");
    assert_eq!(legacy.text, "AB");
    assert!((legacy.pages[0].spans[0].x - 25.0).abs() <= 1.0e-9);
    assert!((legacy.pages[0].spans[0].y - 47.0).abs() <= 1.0e-9);
}

#[test]
fn positioned_glyph_count_is_bounded() {
    let content = b"BT /F1 10 Tf (ABC) Tj ET".to_vec();
    let font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec();
    let limits = ParseLimits {
        max_text_spans: 2,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&single_page_pdf(&content, &font), limits)
        .expect("valid bounded PDF");
    let error = document
        .extract_text_v2(TextExtractionOptionsV2::default())
        .expect_err("glyph limit must be enforced");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn text_line_moves_apply_scaled_matrix_for_td_leading_and_quotes() {
    let content = b"BT /F1 1 Tf 10 0 0 10 100 700 Tm (A) Tj \
        0 -2 TD (B) Tj T* (C) Tj (D) ' 0 0 (E) \" ET";
    let font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
    let document = PdfDocument::parse(&single_page_pdf(content, font)).expect("scaled text PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("scaled text line moves");
    assert_eq!(result.glyphs.len(), 5);
    for (glyph, y) in result
        .glyphs
        .iter()
        .zip([700.0, 680.0, 660.0, 640.0, 620.0])
    {
        assert!((glyph.origin[0] - 100.0).abs() < 1.0e-9);
        assert!((glyph.origin[1] - y).abs() < 1.0e-9);
    }
    let legacy = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .expect("legacy text line moves");
    for (span, y) in legacy.pages[0]
        .spans
        .iter()
        .zip([700.0, 680.0, 660.0, 640.0, 620.0])
    {
        assert!((span.x - 100.0).abs() < 1.0e-9);
        assert!((span.y - y).abs() < 1.0e-9);
    }
}

#[test]
fn td_uses_rotated_line_matrix_before_page_ctm_and_ignores_text_advance() {
    let content = b"q 2 0 0 3 5 7 cm BT /F1 1 Tf \
        0 2 -3 0 100 200 Tm (AAAA) Tj 4 -5 Td (B) Tj ET Q";
    let font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
    let document = PdfDocument::parse(&single_page_pdf(content, font)).expect("rotated text PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("rotated text line move");
    let glyph = result.glyphs.last().expect("B glyph");
    assert_eq!(glyph.unicode, "B");
    // Tlm(4, -5) = (115, 208), then CTM gives (235, 631).
    assert!((glyph.origin[0] - 235.0).abs() < 1.0e-9);
    assert!((glyph.origin[1] - 631.0).abs() < 1.0e-9);
}

fn assert_glyph(
    glyph: &pdf_core::PositionedGlyph,
    text: &str,
    origin: [f64; 2],
    advance: [f64; 2],
) {
    const TOLERANCE: f64 = 1.0e-9;
    assert_eq!(glyph.unicode, text);
    assert_eq!(glyph.writing_mode, WritingMode::Horizontal);
    for index in 0..2 {
        assert!((glyph.origin[index] - origin[index]).abs() <= TOLERANCE);
        assert!((glyph.advance[index] - advance[index]).abs() <= TOLERANCE);
    }
}

fn single_page_pdf(content: &[u8], font: &[u8]) -> Vec<u8> {
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        font.to_vec(),
    ];
    classic_pdf(&objects)
}

fn stream_body(data: &[u8]) -> Vec<u8> {
    stream_with_dictionary(b"", data)
}

fn stream_with_dictionary(dictionary: &[u8], data: &[u8]) -> Vec<u8> {
    let mut body = format!(
        "<< {} /Length {} >>\nstream\n",
        String::from_utf8_lossy(dictionary),
        data.len()
    )
    .into_bytes();
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
