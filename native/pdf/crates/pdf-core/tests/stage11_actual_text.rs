use pdf_core::{
    ErrorCode, ExtractionMode, ParseLimits, PdfDocument, TextExtractionOptionsV2, TextOrigin,
};

#[test]
fn actual_text_replaces_enclosed_glyphs_once_and_preserves_mcid() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span << /ActualText <FEFF006600660069> /MCID 7 >> BDC (X) Tj EMC ET",
        None,
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("valid ActualText PDF");

    for mode in [
        ExtractionMode::ContentOrder,
        ExtractionMode::Layout,
        ExtractionMode::Auto,
    ] {
        let extracted = extract(&document, mode);
        assert_eq!(extracted.text, "ffi");
        assert_eq!(extracted.glyphs.len(), 1);
        assert_eq!(extracted.glyphs[0].text_origin, TextOrigin::ActualText);
        assert_eq!(extracted.glyphs[0].mcid, Some(7));
        assert!(extracted.warnings.is_empty());
    }
}

#[test]
fn outer_actual_text_suppresses_nested_replacements_and_originals() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span << /ActualText (outer) /MCID 1 >> BDC (A) Tj /Span << /ActualText (inner) /MCID 2 >> BDC (B) Tj EMC (C) Tj EMC ET",
        None,
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("valid nested ActualText PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "outer");
    assert_eq!(extracted.glyphs.len(), 1);
    assert_eq!(extracted.glyphs[0].text_origin, TextOrigin::ActualText);
    assert_eq!(extracted.glyphs[0].mcid, Some(1));
}

#[test]
fn named_property_list_resolves_actual_text_and_mcid() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span /P1 BDC (A) Tj EMC ET",
        Some(b"/Properties << /P1 << /ActualText (named) /MCID 4 >> >>"),
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("valid named property PDF");
    let extracted = extract(&document, ExtractionMode::Auto);

    assert_eq!(extracted.text, "named");
    assert_eq!(extracted.glyphs[0].mcid, Some(4));
    assert_eq!(extracted.glyphs[0].text_origin, TextOrigin::ActualText);
}

#[test]
fn invalid_actual_text_warns_once_and_retains_original_glyphs() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span << /ActualText 42 >> BDC (A) Tj EMC /Span << /ActualText 43 >> BDC (B) Tj EMC ET",
        None,
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("structurally valid invalid ActualText PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "AB");
    assert_eq!(
        extracted
            .warnings
            .iter()
            .filter(|warning| warning.code == "actual_text_invalid")
            .count(),
        1
    );
    assert!(
        extracted
            .warnings
            .iter()
            .any(|warning| warning.code == "font_fallback_encoding")
    );
}

#[test]
fn missing_emc_is_bounded_warned_and_closed_implicitly() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span << /ActualText (replacement) >> BDC (A) Tj ET",
        None,
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("valid unterminated marked-content PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "replacement");
    assert_eq!(extracted.glyphs.len(), 1);
    assert!(
        extracted
            .warnings
            .iter()
            .any(|warning| warning.code == "actual_text_invalid")
    );
}

#[test]
fn cyclic_named_property_warns_and_falls_back_without_looping() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span /P1 BDC (A) Tj EMC ET",
        Some(b"/Properties << /P1 6 0 R >>"),
        &[b"6 0 R".to_vec()],
    );
    let document = PdfDocument::parse(&pdf).expect("valid cyclic property PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "A");
    assert!(
        extracted
            .warnings
            .iter()
            .any(|warning| warning.code == "actual_text_invalid")
    );
}

#[test]
fn parent_actual_text_suppresses_text_inside_form_xobject() {
    let form_content = b"BT /F1 12 Tf (A) Tj ET";
    let mut form = format!(
        "<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Resources << /Font << /F1 5 0 R >> >> /Length {} >>\nstream\n",
        form_content.len()
    )
    .into_bytes();
    form.extend_from_slice(form_content);
    form.extend_from_slice(b"\nendstream");
    let page_content = b"/Span << /ActualText (form-replacement) /MCID 9 >> BDC /Fm Do EMC";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> /XObject << /Fm 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(page_content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
        form,
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("valid Form ActualText PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "form-replacement");
    assert_eq!(extracted.glyphs.len(), 1);
    assert_eq!(extracted.glyphs[0].text_origin, TextOrigin::ActualText);
    assert_eq!(extracted.glyphs[0].mcid, Some(9));
}

#[test]
fn empty_actual_text_suppresses_enclosed_text_without_placeholder() {
    let pdf = text_pdf(
        b"BT /F1 12 Tf /Span << /ActualText () >> BDC (A) Tj EMC ET",
        None,
        &[],
    );
    let document = PdfDocument::parse(&pdf).expect("valid empty ActualText PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert!(extracted.text.is_empty());
    assert!(extracted.glyphs.is_empty());
    assert!(extracted.pages[0].spans.is_empty());
}

#[test]
fn unmatched_emc_warns_without_losing_following_text() {
    let pdf = text_pdf(b"EMC BT /F1 12 Tf (A) Tj ET", None, &[]);
    let document = PdfDocument::parse(&pdf).expect("valid unmatched EMC PDF");
    let extracted = extract(&document, ExtractionMode::ContentOrder);

    assert_eq!(extracted.text, "A");
    assert!(
        extracted
            .warnings
            .iter()
            .any(|warning| warning.code == "actual_text_invalid")
    );
}

#[test]
fn marked_content_depth_is_bounded() {
    let content = b"/Span BMC /Span BMC /Span BMC /Span BMC /Span BMC EMC EMC EMC EMC EMC";
    let pdf = text_pdf(content, None, &[]);
    let limits = ParseLimits {
        max_object_depth: 4,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&pdf, limits).expect("page tree within limit");
    let error = document
        .extract_text_v2(TextExtractionOptionsV2::default())
        .expect_err("marked-content depth must be bounded");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

fn extract(document: &PdfDocument, mode: ExtractionMode) -> pdf_core::ExtractedTextV2 {
    document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode,
            include_quality_metadata: true,
        })
        .expect("text extraction")
}

fn text_pdf(content: &[u8], property_resources: Option<&[u8]>, extra: &[Vec<u8>]) -> Vec<u8> {
    let mut resources = b"<< /Font << /F1 5 0 R >>".to_vec();
    if let Some(properties) = property_resources {
        resources.push(b' ');
        resources.extend_from_slice(properties);
    }
    resources.extend_from_slice(b" >>");
    let mut page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources ".to_vec();
    page.extend_from_slice(&resources);
    page.extend_from_slice(b" /Contents 4 0 R >>");

    let mut objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        page,
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
    ];
    objects.extend_from_slice(extra);
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
