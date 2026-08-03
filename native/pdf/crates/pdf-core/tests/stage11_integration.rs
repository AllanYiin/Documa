use pdf_core::{ErrorCode, ExtractionMode, PdfDocument, TextExtractionOptionsV2, TextOrigin};

#[test]
fn producer_font_batches_are_reassembled_by_geometry_in_auto_mode() {
    let document = PdfDocument::parse(&font_batch_pdf()).expect("valid font batch PDF");
    let content = extract(&document, ExtractionMode::ContentOrder).expect("content order");
    let layout = extract(&document, ExtractionMode::Layout).expect("legacy layout");
    let auto = extract(&document, ExtractionMode::Auto).expect("auto layout");

    assert_eq!(content.text, "ACBD");
    assert_eq!(layout.text, "A B C D");
    assert_eq!(auto.text, "ABCD");
    assert_eq!(auto.pages.len(), 1);
    assert_eq!(auto.glyphs.len(), 4);
    assert!(auto.warnings.is_empty());
    assert_eq!(auto.quality.expect("quality").inserted_spaces, 0);
}

#[test]
fn one_to_many_tounicode_ligature_is_emitted_as_one_glyph() {
    let cmap = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
                 1 beginbfchar <01> <006600660069> endbfchar";
    let pdf = single_font_pdf(b"BT /F1 12 Tf <01> Tj ET", cmap);
    let document = PdfDocument::parse(&pdf).expect("valid ligature PDF");

    for mode in [
        ExtractionMode::ContentOrder,
        ExtractionMode::Layout,
        ExtractionMode::Auto,
    ] {
        let result = extract(&document, mode).expect("ligature extraction");
        assert_eq!(result.text, "ffi");
        assert_eq!(result.pages.len(), 1);
        assert_eq!(result.glyphs.len(), 1);
        assert_eq!(result.glyphs[0].unicode, "ffi");
        assert_eq!(result.glyphs[0].text_origin, TextOrigin::ToUnicode);
        assert!(result.warnings.is_empty());
    }
}

#[test]
fn invalid_tounicode_destinations_have_a_distinct_aggregated_warning() {
    let cmap = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
                 1 beginbfchar <01> <D800> endbfchar";
    let codes = "01".repeat(64);
    let content = format!("BT /F1 12 Tf <{codes}> Tj ET");
    let pdf = single_font_pdf(content.as_bytes(), cmap);
    let document = PdfDocument::parse(&pdf).expect("valid invalid-destination PDF");
    let result = extract(&document, ExtractionMode::ContentOrder).expect("bounded recovery");

    assert_eq!(result.text.chars().count(), 64);
    assert!(result.text.chars().all(|character| character == '\u{fffd}'));
    assert_eq!(
        result
            .warnings
            .iter()
            .map(|warning| warning.code.as_str())
            .collect::<Vec<_>>(),
        vec!["unicode_mapping_invalid"]
    );
    assert_eq!(result.quality.expect("quality").replacement_characters, 64);
}

#[test]
fn cyclic_indirect_encoding_fails_with_stable_error() {
    let document = PdfDocument::parse(&cyclic_encoding_pdf()).expect("structurally valid PDF");
    let error =
        extract(&document, ExtractionMode::ContentOrder).expect_err("cyclic Encoding must fail");

    assert_eq!(error.code, ErrorCode::InvalidReference);
    assert!(error.message.contains("cyclic font Encoding reference"));
}

#[test]
fn repeated_missing_mappings_and_malformed_nesting_are_aggregated() {
    let cmap = b"1 begincodespacerange <00> <ff> endcodespacerange";
    let codes = "02".repeat(128);
    let content = format!("EMC EMC EMC BT /F1 12 Tf <{codes}> Tj ET EMC EMC");
    let pdf = single_font_pdf(content.as_bytes(), cmap);
    let document = PdfDocument::parse(&pdf).expect("valid amplification PDF");
    let result = extract(&document, ExtractionMode::ContentOrder).expect("bounded extraction");

    assert_eq!(result.text.chars().count(), 128);
    assert!(result.text.chars().all(|character| character == '\u{fffd}'));
    assert_eq!(
        result
            .warnings
            .iter()
            .filter(|warning| warning.code == "unicode_mapping_missing")
            .count(),
        1
    );
    assert_eq!(
        result
            .warnings
            .iter()
            .filter(|warning| warning.code == "actual_text_invalid")
            .count(),
        1
    );
    assert_eq!(result.warnings.len(), 2);
    assert_eq!(result.quality.expect("quality").replacement_characters, 128);
}

#[test]
fn actual_text_parser_path_handles_every_truncated_content_prefix() {
    let cmap = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
                 1 beginbfchar <01> <0041> endbfchar";
    let content = b"/Span << /ActualText <FEFF006600660069> >> BDC \
                    BT /F1 12 Tf <01> Tj ET EMC";

    for end in 0..=content.len() {
        let pdf = single_font_pdf(&content[..end], cmap);
        let document = PdfDocument::parse(&pdf).expect("prefix wrapper remains valid");
        if let Err(error) = extract(&document, ExtractionMode::Auto) {
            assert!(!error.code.as_str().is_empty());
        }
    }

    let complete = PdfDocument::parse(&single_font_pdf(content, cmap)).expect("complete PDF");
    let result = extract(&complete, ExtractionMode::Auto).expect("complete extraction");
    assert_eq!(result.text, "ffi");
    assert_eq!(result.glyphs.len(), 1);
    assert_eq!(result.glyphs[0].text_origin, TextOrigin::ActualText);
}

fn extract(
    document: &PdfDocument,
    mode: ExtractionMode,
) -> pdf_core::PdfResult<pdf_core::ExtractedTextV2> {
    document.extract_text_v2(TextExtractionOptionsV2 {
        normalize_unicode: false,
        mode,
        include_quality_metadata: true,
    })
}

fn font_batch_pdf() -> Vec<u8> {
    let content = b"BT\n\
        /F1 12 Tf 1 0 0 1 10 700 Tm <01> Tj 1 0 0 1 22 700 Tm <02> Tj\n\
        /F2 12 Tf 1 0 0 1 16 700 Tm <01> Tj 1 0 0 1 28 700 Tm <02> Tj\n\
        ET";
    let cmap_one = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
        2 beginbfchar <01> <0041> <02> <0043> endbfchar";
    let cmap_two = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
        2 beginbfchar <01> <0042> <02> <0044> endbfchar";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R /F2 7 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap_one),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 8 0 R >>".to_vec(),
        stream_body(cmap_two),
    ];
    classic_pdf(&objects)
}

fn single_font_pdf(content: &[u8], cmap: &[u8]) -> Vec<u8> {
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap),
    ];
    classic_pdf(&objects)
}

fn cyclic_encoding_pdf() -> Vec<u8> {
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(b"BT /F1 12 Tf (A) Tj ET"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding 6 0 R >>".to_vec(),
        b"7 0 R".to_vec(),
        b"6 0 R".to_vec(),
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
