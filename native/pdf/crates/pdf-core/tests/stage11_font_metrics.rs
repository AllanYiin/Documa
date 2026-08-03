use pdf_core::{ExtractionMode, PdfDocument, TextExtractionOptionsV2, WritingMode};

#[test]
fn cid_default_explicit_and_range_widths_preserve_vertical_geometry() {
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
    assert_vertical(&result.glyphs[0], [50.0, 100.0], [0.0, -5.0]);
    assert_vertical(&result.glyphs[1], [50.0, 95.0], [0.0, -7.5]);
    assert_vertical(&result.glyphs[2], [50.0, 87.5], [0.0, -6.0]);
    assert_eq!(result.quality.expect("quality").fallback_glyphs, 0);
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
