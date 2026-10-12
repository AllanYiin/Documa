use pdf_core::{ExtractionMode, PdfDocument, TextExtractionOptionsV2, WritingMode};

#[test]
fn cid_horizontal_widths_set_default_vertical_origins_not_vertical_advances() {
    let content = b"BT /F1 10 Tf 1 0 0 1 50 100 Tm <00010002000A> Tj ET";
    let cmap = b"1 begincodespacerange <0000> <ffff> endcodespacerange
        3 beginbfchar
        <0001> <0041>
        <0002> <0042>
        <000A> <0043>
        endbfchar";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Fixture \
          /Encoding /Identity-V /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>"
            .to_vec(),
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Fixture \
          /DW 1000 /W [1 [500 750] 10 12 600] >>"
            .to_vec(),
        stream_body(cmap),
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("valid CID font PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("CID positioned extraction");

    assert_eq!(result.text, "ABC");
    assert_eq!(result.glyphs.len(), 3);
    assert_vertical(&result.glyphs[0], [47.5, 91.2], [0.0, -10.0]);
    assert_vertical(&result.glyphs[1], [46.25, 81.2], [0.0, -10.0]);
    assert_vertical(&result.glyphs[2], [47.0, 71.2], [0.0, -10.0]);
    assert_eq!(result.quality.expect("quality").fallback_glyphs, 0);
}

fn vertical_fixture(metrics: &str, content: &[u8]) -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type0 /Encoding /Identity-V /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>".to_vec(),
        format!("<< /Type /Font /Subtype /CIDFontType2 /DW 1000 {metrics} >>").into_bytes(),
        stream_body(b"1 begincodespacerange <0000> <ffff> endcodespacerange 3 beginbfchar <0001> <4E00> <0002> <4E8C> <0003> <4E09> endbfchar"),
    ])
}

#[test]
fn w2_arrays_ranges_and_dw2_use_vertical_displacement_position_and_tj() {
    let bytes = vertical_fixture(
        "/DW2 [900 -1100] /W2 [1 [-1200 300 900] 2 2 -800 400 700]",
        b"BT /F1 10 Tf 2 Tc 200 Tz 1 0 0 1 50 100 Tm [<0001> 100 <00020003>] TJ ET",
    );
    let document = PdfDocument::parse(&bytes).unwrap();
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .unwrap();
    assert_eq!(result.text, "一二三");
    assert_vertical(&result.glyphs[0], [44.0, 91.0], [0.0, -10.0]);
    // A positive TJ number moves down in vertical mode; Tc adds +2 points.
    assert_vertical(&result.glyphs[1], [42.0, 82.0], [0.0, -6.0]);
    assert_vertical(&result.glyphs[2], [40.0, 74.0], [0.0, -9.0]);
    let layout = document
        .extract_layout(pdf_core::LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..pdf_core::LayoutExtractionOptions::default()
        })
        .unwrap();
    let glyph = &layout.pages[0].debug_glyphs.as_ref().unwrap()[0];
    assert!((glyph.bbox.x0 - 44.0).abs() < 1e-9);
    assert!((glyph.bbox.x1 - 64.0).abs() < 1e-9);
    assert!((glyph.bbox.y0 - 101.0).abs() < 1e-9);
    assert!((glyph.bbox.y1 - 111.0).abs() < 1e-9);
}

#[test]
fn vertical_metrics_transform_with_ctm_and_text_matrix() {
    let bytes = vertical_fixture(
        "/W2 [1 [-1000 500 880]]",
        b"q 2 0 0 3 10 20 cm BT /F1 10 Tf 0 1 -1 0 50 100 Tm <0001> Tj ET Q",
    );
    let result = PdfDocument::parse(&bytes)
        .unwrap()
        .extract_text_v2(TextExtractionOptionsV2::default())
        .unwrap();
    assert_vertical(&result.glyphs[0], [127.6, 305.0], [20.0, 0.0]);
}

#[test]
fn malformed_and_oversized_vertical_metrics_are_bounded() {
    for metrics in [
        "/DW2 [880]",
        "/W2 [1 [-1000 500]]",
        "/W2 [3 1 -1000 500 880]",
        "/W2 [1 2 -1000]",
    ] {
        let bytes = vertical_fixture(metrics, b"BT /F1 10 Tf <0001> Tj ET");
        let error = PdfDocument::parse(&bytes)
            .unwrap()
            .extract_text_v2(TextExtractionOptionsV2::default())
            .unwrap_err();
        assert_eq!(error.code, pdf_core::ErrorCode::InvalidObject);
    }
    let bytes = vertical_fixture("/W2 [1 100 -1000 500 880]", b"BT /F1 10 Tf <0001> Tj ET");
    let limits = pdf_core::ParseLimits {
        max_array_items: 16,
        ..pdf_core::ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&bytes, limits)
        .unwrap()
        .extract_text_v2(TextExtractionOptionsV2::default())
        .unwrap_err();
    assert_eq!(error.code, pdf_core::ErrorCode::LimitExceeded);
}

#[test]
fn actual_text_encloses_all_replaced_vertical_glyphs() {
    let bytes = vertical_fixture(
        "",
        b"BT /F1 10 Tf 1 0 0 1 50 100 Tm /Span << /ActualText (AB) >> BDC <00010002> Tj EMC ET",
    );
    let layout = PdfDocument::parse(&bytes)
        .unwrap()
        .extract_layout(pdf_core::LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..pdf_core::LayoutExtractionOptions::default()
        })
        .unwrap();
    let glyphs = layout.pages[0].debug_glyphs.as_ref().unwrap();
    assert_eq!(glyphs.len(), 1);
    assert_eq!(glyphs[0].text, "AB");
    assert!((glyphs[0].bbox.height() - 20.0).abs() < 1e-9);
}

fn assert_vertical(glyph: &pdf_core::PositionedGlyph, origin: [f64; 2], advance: [f64; 2]) {
    const TOLERANCE: f64 = 1.0e-9;
    assert_eq!(glyph.writing_mode, WritingMode::Vertical);
    for index in 0..2 {
        assert!((glyph.origin[index] - origin[index]).abs() <= TOLERANCE);
        assert!((glyph.advance[index] - advance[index]).abs() <= TOLERANCE);
    }
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
