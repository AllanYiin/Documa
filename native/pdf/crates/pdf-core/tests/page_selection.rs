use pdf_core::{
    ErrorCode, LayoutEvent, LayoutExtractionOptions, PageSelection, ParseLimits, PdfDocument,
    TextExtractionOptionsV2, collect_layout_events, collect_selected_layout_events,
};

fn document() -> PdfDocument {
    let stream = |text: &[u8]| {
        let mut body = format!("<< /Length {} >>\nstream\n", text.len()).into_bytes();
        body.extend_from_slice(text);
        body.extend_from_slice(b"\nendstream");
        body
    };
    let page = |content| {
        format!("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 9 0 R >> >> /Contents {content} 0 R >>").into_bytes()
    };
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>".to_vec(),
        page(4),
        stream(b"BT /F1 10 Tf 1 0 0 1 10 195 Tm (Header) Tj 1 0 0 1 10 100 Tm (Alpha) Tj ET"),
        page(6),
        stream(b"BT (unterminated"),
        page(8),
        stream(b"BT /F1 10 Tf 1 0 0 1 10 195 Tm (Header) Tj 1 0 0 1 10 100 Tm (Omega) Tj ET"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ];
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (i, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", i + 1).as_bytes());
        pdf.extend_from_slice(object);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref = pdf.len();
    pdf.extend_from_slice(b"xref\n0 10\n0000000000 65535 f \n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    pdf.extend_from_slice(
        format!("trailer\n<< /Size 10 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").as_bytes(),
    );
    PdfDocument::parse(&pdf).unwrap()
}

#[test]
fn selections_are_bounded_sorted_unique_and_one_based() {
    assert_eq!(
        "3, 1-2,2"
            .parse::<PageSelection>()
            .unwrap()
            .indices(3)
            .unwrap(),
        [0, 1, 2]
    );
    assert_eq!(
        "all".parse::<PageSelection>().unwrap().indices(3).unwrap(),
        [0, 1, 2]
    );
    for invalid in [
        "",
        "0",
        "-1",
        "2-1",
        "1-",
        "1,,2",
        "1.5",
        "999999999999999999999999999999",
    ] {
        assert_eq!(
            invalid.parse::<PageSelection>().unwrap_err().code,
            ErrorCode::InvalidOption
        );
    }
    assert_eq!(
        "4".parse::<PageSelection>()
            .unwrap()
            .indices(3)
            .unwrap_err()
            .code,
        ErrorCode::InvalidOption
    );
}

#[test]
fn text_and_layout_skip_bad_unselected_content_and_retain_page_identity() {
    let doc = document();
    let selection = "1,3".parse::<PageSelection>().unwrap();
    let text = doc
        .extract_text_v2_pages(TextExtractionOptionsV2::default(), &selection)
        .unwrap();
    assert_eq!(
        text.pages
            .iter()
            .map(|page| page.page_index)
            .collect::<Vec<_>>(),
        [0, 2]
    );
    assert!(text.text.contains("Alpha") && text.text.contains("Omega"));
    assert!(
        doc.extract_text_v2(TextExtractionOptionsV2::default())
            .is_err()
    );
    let layout = doc
        .extract_layout_pages(LayoutExtractionOptions::default(), &selection)
        .unwrap();
    assert_eq!(
        layout
            .pages
            .iter()
            .map(|page| page.page_number)
            .collect::<Vec<_>>(),
        [1, 3]
    );
    assert!(layout.pages.iter().all(|page| {
        page.semantic_nodes
            .iter()
            .any(|node| node.role == pdf_core::LayoutNodeRole::Header)
    }));
    let events = doc
        .extract_layout_events_pages(LayoutExtractionOptions::default(), &selection)
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    let streamed =
        collect_selected_layout_events(events.clone(), &ParseLimits::default(), &[0, 2]).unwrap();
    assert_eq!(streamed, layout);
    // Do not weaken the original contiguous event protocol.
    assert_eq!(
        collect_layout_events(events, &ParseLimits::default())
            .unwrap_err()
            .code,
        ErrorCode::InvalidObject
    );
}

#[test]
fn selected_layout_is_lazy_and_option_digests_include_the_selection() {
    let doc = document();
    let one = doc
        .extract_layout_pages(LayoutExtractionOptions::default(), &"1".parse().unwrap())
        .unwrap();
    let three = doc
        .extract_layout_pages(LayoutExtractionOptions::default(), &"3".parse().unwrap())
        .unwrap();
    assert_ne!(one.options_digest, three.options_digest);
    let mut events = doc
        .extract_layout_events_pages(LayoutExtractionOptions::default(), &"1-2".parse().unwrap())
        .unwrap();
    assert!(matches!(
        events.next().unwrap().unwrap(),
        LayoutEvent::DocumentStart(_)
    ));
    assert!(matches!(
        events.next().unwrap().unwrap(),
        LayoutEvent::Page(_)
    ));
    assert!(events.next().unwrap().is_err());
    assert!(events.next().is_none());
}

#[test]
fn serde_limits_accept_partial_overrides_and_reject_unknown_fields() {
    let limits: ParseLimits = serde_json::from_str(r#"{"max_file_bytes":1024}"#).unwrap();
    assert_eq!(limits.max_file_bytes, 1024);
    assert_eq!(
        limits.max_object_depth,
        ParseLimits::default().max_object_depth
    );
    assert!(serde_json::from_str::<ParseLimits>(r#"{"max_flie_bytes":1024}"#).is_err());
}
