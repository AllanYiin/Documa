use pdf_core::{
    CoordinateSpace, ErrorCode, LayoutEvent, LayoutExtractionOptions, LayoutNodeFinalization,
    LayoutNodeRole, LayoutPageFinalization, ParseLimits, PdfDocument, collect_layout_events,
};

const FIXTURE: &[u8] = include_bytes!("../../../tests/fixtures/valid/text-minimal.pdf");

fn extracted_layout() -> pdf_core::DocumentLayout {
    PdfDocument::parse(FIXTURE)
        .expect("fixture parses")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("fixture layout extracts")
}

#[test]
fn compatibility_event_stream_is_ordered_serializable_and_exact() {
    let original = extracted_layout();
    let page_count = original.pages.len();
    let mut stream = original.clone().into_event_stream();
    assert_eq!(stream.len(), page_count + 2);

    let start = stream.next().expect("document_start");
    let encoded = serde_json::to_value(&start).expect("event serializes");
    assert_eq!(encoded["event"], "document_start");
    assert_eq!(
        encoded["payload"]["coordinate_space"],
        "layout_unrotated_top_left"
    );
    assert_eq!(
        encoded["payload"]["page_count"],
        serde_json::json!(page_count)
    );
    assert!(matches!(stream.next(), Some(LayoutEvent::Page(_))));
    assert!(matches!(
        stream.next(),
        Some(LayoutEvent::DocumentFinalize(_))
    ));
    assert!(stream.next().is_none());
    assert_eq!(stream.len(), 0);

    let collected = collect_layout_events(
        original.clone().into_event_stream(),
        &ParseLimits::default(),
    )
    .expect("compatibility stream collects");
    assert_eq!(collected, original);
}

#[test]
fn pdf_document_event_api_collects_to_the_stable_complete_layout() {
    let expected = extracted_layout();
    let document = PdfDocument::parse(FIXTURE).expect("fixture parses");
    let events = document
        .extract_layout_events(LayoutExtractionOptions::default())
        .expect("event extraction succeeds");
    let actual = collect_layout_events(events, &ParseLimits::default())
        .expect("native compatibility events collect");
    assert_eq!(actual, expected);
}

#[test]
fn finalization_patch_restores_delayed_role_and_main_flow_exactly() {
    let original = extracted_layout();
    let page = original.pages.first().expect("fixture page");
    let node = page.semantic_nodes.first().expect("fixture node");
    let finalization = LayoutPageFinalization {
        page_index: page.page_index,
        node_updates: vec![LayoutNodeFinalization {
            node_id: node.id.clone(),
            role: node.role,
            confidence: node.confidence,
            rule_id: node.rule_id.clone(),
        }],
        main_flow: page.orders.main_flow.clone(),
    };

    let mut events = original.clone().into_event_stream().collect::<Vec<_>>();
    for event in &mut events {
        match event {
            LayoutEvent::Page(page) => {
                let provisional = page.semantic_nodes.first_mut().expect("fixture node");
                provisional.role = LayoutNodeRole::Unclassified;
                provisional.confidence = 0.0;
                provisional.rule_id = "stage6c2_provisional".to_owned();
                page.orders.main_flow.clear();
            }
            LayoutEvent::DocumentFinalize(value) => {
                value.page_finalizations.push(finalization.clone());
            }
            LayoutEvent::DocumentStart(_) => {}
        }
    }

    let collected =
        collect_layout_events(events, &ParseLimits::default()).expect("finalization patch applies");
    assert_eq!(collected, original);
}

#[test]
fn event_page_and_node_patch_limits_have_exact_boundaries() {
    let original = extracted_layout();
    let page = original.pages.first().expect("fixture page");
    let node = page.semantic_nodes.first().expect("fixture node");
    let mut events = original.clone().into_event_stream().collect::<Vec<_>>();
    let patch = LayoutPageFinalization {
        page_index: 0,
        node_updates: vec![LayoutNodeFinalization {
            node_id: node.id.clone(),
            role: node.role,
            confidence: node.confidence,
            rule_id: node.rule_id.clone(),
        }],
        main_flow: page.orders.main_flow.clone(),
    };
    for event in &mut events {
        if let LayoutEvent::DocumentFinalize(value) = event {
            value.page_finalizations.push(patch.clone());
        }
    }

    let exact = ParseLimits {
        max_pages: original.pages.len(),
        max_text_spans: 1,
        ..ParseLimits::default()
    };
    collect_layout_events(events.clone(), &exact).expect("exact event limits pass");

    let mut one_short_page = exact.clone();
    one_short_page.max_pages = original.pages.len() - 1;
    let error = collect_layout_events(events.clone(), &one_short_page)
        .expect_err("one-short page limit fails");
    assert_eq!(error.code, ErrorCode::LimitExceeded);

    let mut one_short_update = exact;
    one_short_update.max_text_spans = 0;
    let error = collect_layout_events(events, &one_short_update)
        .expect_err("one-short node update limit fails");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn malformed_event_sequences_and_coordinate_mixing_are_rejected() {
    let original = extracted_layout();
    let limits = ParseLimits::default();
    let error = collect_layout_events(Vec::<LayoutEvent>::new(), &limits)
        .expect_err("empty sequence fails");
    assert_eq!(error.code, ErrorCode::InvalidObject);

    let mut missing_finalize = original.clone().into_event_stream().collect::<Vec<_>>();
    missing_finalize.pop();
    let error =
        collect_layout_events(missing_finalize, &limits).expect_err("missing finalize fails");
    assert_eq!(error.code, ErrorCode::InvalidObject);

    let mut mixed_coordinates = original.into_event_stream().collect::<Vec<_>>();
    for event in &mut mixed_coordinates {
        if let LayoutEvent::Page(page) = event {
            page.coordinate_space = CoordinateSpace::DisplaySpace;
        }
    }
    let error =
        collect_layout_events(mixed_coordinates, &limits).expect_err("coordinate mixing fails");
    assert_eq!(error.code, ErrorCode::InvalidObject);
}
#[test]
fn native_event_api_emits_delayed_furniture_patches() {
    let pdf = event_three_page_pdf();
    let document = PdfDocument::parse(&pdf).expect("fixture parses");
    let expected = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("complete layout");
    let events = document
        .extract_layout_events(LayoutExtractionOptions::default())
        .expect("event extraction")
        .collect::<pdf_core::PdfResult<Vec<_>>>()
        .expect("page production succeeds");

    let mut page_events = 0;
    let mut finalizations = None;
    for event in &events {
        match event {
            LayoutEvent::Page(page) => {
                page_events += 1;
                assert!(page.orders.main_flow.is_empty());
                assert!(page.semantic_nodes.iter().all(|node| {
                    !matches!(
                        node.role,
                        LayoutNodeRole::Header
                            | LayoutNodeRole::Footer
                            | LayoutNodeRole::PageNumber
                    )
                }));
            }
            LayoutEvent::DocumentFinalize(finalize) => {
                finalizations = Some(finalize.page_finalizations.clone());
            }
            LayoutEvent::DocumentStart(_) => {}
        }
    }
    assert_eq!(page_events, 3);
    let finalizations = finalizations.expect("document finalization");
    assert_eq!(finalizations.len(), 3);
    assert!(
        finalizations
            .iter()
            .all(|page| page.node_updates.len() == 3)
    );
    assert!(finalizations.iter().all(|page| page.main_flow.len() == 1));

    let collected =
        collect_layout_events(events, &ParseLimits::default()).expect("patches collect exactly");
    assert_eq!(collected, expected);
}

#[test]
fn native_event_producer_can_be_cancelled_after_a_page_without_external_state() {
    let pdf = event_three_page_pdf();
    let document = PdfDocument::parse(&pdf).expect("fixture parses");
    let mut producer = document
        .extract_layout_events(LayoutExtractionOptions::default())
        .expect("event producer");
    assert!(matches!(
        producer.next().expect("start").expect("start succeeds"),
        LayoutEvent::DocumentStart(_)
    ));
    assert!(matches!(
        producer.next().expect("page").expect("page succeeds"),
        LayoutEvent::Page(_)
    ));
    drop(producer);

    let complete = PdfDocument::parse(&pdf)
        .expect("fresh parse after cancellation")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("complete extraction after cancellation");
    assert_eq!(complete.pages.len(), 3);
}

#[test]
fn native_event_producer_yields_before_a_later_page_error() {
    let valid = b"BT /F1 12 Tf 72 700 Td (First) Tj ET";
    let invalid = b"BT /F1 12 Tf 72 700 Td (unterminated";
    let pdf = event_classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 5 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>".to_vec(),
        event_stream_body(valid),
        event_stream_body(invalid),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
    ]);
    let document = PdfDocument::parse(&pdf).expect("fixture parses");
    let mut producer = document
        .extract_layout_events(LayoutExtractionOptions::default())
        .expect("document indexes prepare without decoding later page content");
    assert_eq!(producer.remaining_pages(), 2);
    assert!(matches!(
        producer
            .next()
            .expect("start event")
            .expect("start succeeds"),
        LayoutEvent::DocumentStart(_)
    ));
    let first = producer
        .next()
        .expect("first page event")
        .expect("first page is delivered");
    let LayoutEvent::Page(first) = first else {
        panic!("expected first page event");
    };
    assert_eq!(first.text, "First");
    assert_eq!(producer.remaining_pages(), 1);
    assert!(producer.next().expect("later page result").is_err());
    assert!(producer.next().is_none());
}

fn event_three_page_pdf() -> Vec<u8> {
    let contents = (1..=3)
        .map(|page| {
            format!(
                "BT /F1 12 Tf 1 0 0 1 50 770 Tm (Report 2026) Tj 1 0 0 1 50 600 Tm (Body{page}) Tj 1 0 0 1 50 35 Tm (Confidential) Tj 1 0 0 1 300 15 Tm ({page}) Tj ET"
            )
            .into_bytes()
        })
        .collect::<Vec<_>>();
    event_classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 6 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 7 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 8 0 R >>".to_vec(),
        event_stream_body(&contents[0]),
        event_stream_body(&contents[1]),
        event_stream_body(&contents[2]),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
    ])
}

fn event_stream_body(data: &[u8]) -> Vec<u8> {
    let mut body = format!("<< /Length {} >>\nstream\n", data.len()).into_bytes();
    body.extend_from_slice(data);
    body.extend_from_slice(b"\nendstream");
    body
}

fn event_classic_pdf(objects: &[Vec<u8>]) -> Vec<u8> {
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
