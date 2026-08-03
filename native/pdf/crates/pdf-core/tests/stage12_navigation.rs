use pdf_core::{
    ErrorCode, LayoutExtractionOptions, LayoutNavigationTargetKind, ParseLimits, PdfDocument,
};

#[test]
fn links_named_destinations_and_outlines_are_safe_bounded_layout_metadata() {
    let layout = PdfDocument::parse(&navigation_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert!(layout.capabilities.navigation);
    let page = &layout.pages[0];
    assert_eq!(page.links.len(), 3);
    let uri = &page.links[0];
    assert_eq!(uri.id, "p0-l0");
    assert_eq!(uri.object.unwrap().number, 4);
    assert_eq!(uri.target.kind, LayoutNavigationTargetKind::Uri);
    assert_eq!(uri.target.uri.as_deref(), Some("https://example.invalid/a"));
    assert_eq!(
        (uri.bbox.x0, uri.bbox.y0, uri.bbox.x1, uri.bbox.y1),
        (20.0, 320.0, 220.0, 360.0)
    );
    assert_eq!(uri.quads.len(), 1);
    assert_eq!(
        (uri.quads[0].top_left.x, uri.quads[0].top_left.y),
        (20.0, 320.0)
    );
    assert_eq!(
        (uri.quads[0].bottom_right.x, uri.quads[0].bottom_right.y),
        (220.0, 360.0)
    );

    let local = &page.links[1].target;
    assert_eq!(local.kind, LayoutNavigationTargetKind::GoTo);
    assert_eq!(local.destination_name.as_deref(), Some("chapter1"));
    assert_eq!(local.page_index, Some(0));
    assert_eq!(local.page_object.unwrap().number, 3);
    assert_eq!(local.fit.as_deref(), Some("FitH"));

    let unsupported = &page.links[2].target;
    assert_eq!(unsupported.kind, LayoutNavigationTargetKind::Unsupported);
    assert_eq!(
        unsupported.unsupported_action.as_deref(),
        Some("JavaScript")
    );
    assert!(unsupported.uri.is_none());

    assert_eq!(layout.named_destinations.len(), 1);
    assert_eq!(layout.named_destinations[0].name, "chapter1");
    assert_eq!(layout.named_destinations[0].target.page_index, Some(0));
    assert_eq!(layout.outlines.len(), 2);
    assert_eq!(layout.outlines[0].id, "o0");
    assert_eq!(layout.outlines[0].title, "Intro");
    assert_eq!(layout.outlines[0].depth, 0);
    assert_eq!(
        layout.outlines[0].target.as_ref().unwrap().page_index,
        Some(0)
    );
    assert_eq!(layout.outlines[1].id, "o1");
    assert_eq!(layout.outlines[1].title, "Unsafe");
    assert_eq!(
        layout.outlines[1]
            .target
            .as_ref()
            .unwrap()
            .unsupported_action
            .as_deref(),
        Some("Launch")
    );
    assert!(has_warning(&layout, "navigation_action_unsupported"));
}

#[test]
fn malformed_optional_link_preserves_text_and_later_valid_link() {
    let layout = PdfDocument::parse(&recoverable_navigation_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.text, "A");
    assert_eq!(layout.pages[0].links.len(), 1);
    assert_eq!(
        layout.pages[0].links[0].target.kind,
        LayoutNavigationTargetKind::Uri
    );
    assert!(has_warning(&layout, "navigation_target_invalid"));
}

#[test]
fn navigation_limits_have_exact_and_one_short_boundaries() {
    let pdf = navigation_pdf();
    let exact = ParseLimits {
        max_annotations: 3,
        max_named_destinations: 1,
        max_outline_items: 2,
        ..ParseLimits::default()
    };
    let layout = PdfDocument::parse_with_limits(&pdf, exact)
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.pages[0].links.len(), 3);
    assert_eq!(layout.named_destinations.len(), 1);
    assert_eq!(layout.outlines.len(), 2);

    for limits in [
        ParseLimits {
            max_annotations: 2,
            ..ParseLimits::default()
        },
        ParseLimits {
            max_named_destinations: 0,
            ..ParseLimits::default()
        },
        ParseLimits {
            max_outline_items: 1,
            ..ParseLimits::default()
        },
    ] {
        assert_eq!(
            PdfDocument::parse_with_limits(&pdf, limits)
                .unwrap()
                .extract_layout(LayoutExtractionOptions::default())
                .unwrap_err()
                .code,
            ErrorCode::LimitExceeded
        );
    }
}

fn has_warning(layout: &pdf_core::DocumentLayout, code: &str) -> bool {
    layout.warnings.iter().any(|warning| warning.code == code)
}

fn navigation_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /Names << /Dests 7 0 R >> /Outlines 8 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /CropBox [0 0 200 200] /UserUnit 2 /Rotate 90 /Annots [4 0 R 5 0 R 6 0 R] >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 20 110 40] /QuadPoints [10 40 110 40 10 20 110 20] /A << /S /URI /URI (https://example.invalid/a) >> >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 50 110 70] /Dest (chapter1) >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 80 110 100] /A << /S /JavaScript /JS (do-not-run) >> >>".to_vec(),
        b"<< /Names [(chapter1) [3 0 R /FitH 180]] >>".to_vec(),
        b"<< /Type /Outlines /First 9 0 R /Last 10 0 R >>".to_vec(),
        b"<< /Title (Intro) /Dest (chapter1) /Next 10 0 R >>".to_vec(),
        b"<< /Title (Unsafe) /A << /S /Launch /F (never.exe) >> >>".to_vec(),
    ])
}

fn recoverable_navigation_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 1 0 0 1 20 20 Tm (A) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R /Annots [6 0 R 7 0 R] >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [0 0 0 0] /A << /S /URI /URI (bad) >> >>".to_vec(),
        b"<< /Type /Annot /Subtype /Link /Rect [10 10 20 20] /A << /S /URI /URI (ok) >> >>".to_vec(),
    ])
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
