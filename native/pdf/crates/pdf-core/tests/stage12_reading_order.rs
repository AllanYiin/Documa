use pdf_core::{
    ErrorCode, LayoutExtractionOptions, LayoutNodeRole, LayoutVisualBlockOrder, LayoutVisualCue,
    LayoutVisualTransitionKind, ParseLimits, PdfDocument,
};

#[test]
fn two_columns_are_inferred_left_then_right_independently_of_source_order() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 320 700 Tm (R1) Tj \
        1 0 0 1 320 680 Tm (R2) Tj \
        1 0 0 1 50 700 Tm (L1) Tj \
        1 0 0 1 50 680 Tm (L2) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert!(layout.capabilities.inferred_order);
    assert!(layout.capabilities.main_flow);
    assert_eq!(page.semantic_nodes.len(), 2);
    assert_eq!(page.semantic_nodes[0].text, "L1\nL2");
    assert_eq!(page.semantic_nodes[1].text, "R1\nR2");
    assert_eq!(page.orders.source_order, ["p0-n1", "p0-n0"]);
    assert_eq!(page.orders.inferred_order, ["p0-n0", "p0-n1"]);
    assert_eq!(page.orders.main_flow, ["p0-n0", "p0-n1"]);
    assert!(page.orders.tagged_order.is_empty());
}

#[test]
fn paragraph_gap_creates_a_stable_boundary() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 50 700 Tm (Line1) Tj \
        1 0 0 1 50 682 Tm (Line2) Tj \
        1 0 0 1 50 600 Tm (Next) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 2);
    assert_eq!(page.semantic_nodes[0].text, "Line1\nLine2");
    assert_eq!(page.semantic_nodes[1].text, "Next");
    assert!(
        page.semantic_nodes
            .iter()
            .all(|node| node.rule_id == "stage3_paragraph_geometry_v1")
    );
}

#[test]
fn repeated_furniture_is_preserved_but_excluded_from_main_flow() {
    let pdf = three_page_pdf();
    let layout = layout(&pdf);

    for (page_index, page) in layout.pages.iter().enumerate() {
        assert_eq!(page.semantic_nodes.len(), 4);
        assert_eq!(page.semantic_nodes[0].role, LayoutNodeRole::Header);
        assert_eq!(page.semantic_nodes[0].text, "Report 2026");
        assert_eq!(page.semantic_nodes[1].role, LayoutNodeRole::Unclassified);
        assert_eq!(
            page.semantic_nodes[1].text,
            format!("Body{}", page_index + 1)
        );
        assert_eq!(page.semantic_nodes[2].role, LayoutNodeRole::Footer);
        assert_eq!(page.semantic_nodes[2].text, "Confidential");
        assert_eq!(page.semantic_nodes[3].role, LayoutNodeRole::PageNumber);
        assert_eq!(page.semantic_nodes[3].text, (page_index + 1).to_string());
        assert_eq!(page.orders.inferred_order.len(), 4);
        assert_eq!(page.orders.source_order.len(), 4);
        assert_eq!(page.orders.main_flow, [format!("p{page_index}-n1")]);
    }
}

#[test]
fn unique_margin_text_is_not_silently_removed() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 50 770 Tm (Unique title) Tj \
        1 0 0 1 50 600 Tm (Body) Tj \
        1 0 0 1 50 20 Tm (Unique note) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 3);
    assert!(page.semantic_nodes.iter().all(|node| {
        !matches!(
            node.role,
            LayoutNodeRole::Header | LayoutNodeRole::Footer | LayoutNodeRole::PageNumber
        )
    }));
    assert_eq!(page.orders.main_flow, page.orders.inferred_order);
}

#[test]
fn three_columns_use_stable_left_to_right_order() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 430 700 Tm (R1) Tj \
        1 0 0 1 240 700 Tm (M1) Tj \
        1 0 0 1 50 700 Tm (L1) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 3);
    assert_eq!(
        page.semantic_nodes
            .iter()
            .map(|node| node.text.as_str())
            .collect::<Vec<_>>(),
        ["L1", "M1", "R1"]
    );
    assert_eq!(page.orders.source_order, ["p0-n2", "p0-n1", "p0-n0"]);
    assert_eq!(page.orders.inferred_order, ["p0-n0", "p0-n1", "p0-n2"]);
}

#[test]
fn list_markers_start_distinct_list_item_blocks() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 50 700 Tm (1. First) Tj \
        1 0 0 1 65 682 Tm (continued) Tj \
        1 0 0 1 50 660 Tm (2. Second) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 2);
    assert_eq!(page.semantic_nodes[0].text, "1. First\ncontinued");
    assert_eq!(page.semantic_nodes[1].text, "2. Second");
    assert!(page.semantic_nodes.iter().all(
        |node| node.role == LayoutNodeRole::ListItem && node.rule_id == "stage3_list_marker_v1"
    ));
}

#[test]
fn top_page_numbers_accept_exact_arabic_and_canonical_roman_only() {
    for label in ["1", "I"] {
        let content = format!("BT /F1 12 Tf 1 0 0 1 300 770 Tm ({label}) Tj ET");
        let layout = layout(&single_page_pdf(content.as_bytes()));
        assert_eq!(
            layout.pages[0].semantic_nodes[0].role,
            LayoutNodeRole::PageNumber
        );
        assert!(layout.pages[0].orders.main_flow.is_empty());
    }

    let invalid = layout(&single_page_pdf(
        b"BT /F1 12 Tf 1 0 0 1 300 770 Tm (IIII) Tj ET",
    ));
    assert_eq!(
        invalid.pages[0].semantic_nodes[0].role,
        LayoutNodeRole::Unclassified
    );
    assert_eq!(invalid.pages[0].orders.main_flow, ["p0-n0"]);
}
#[test]
fn bottom_year_is_not_misclassified_as_page_number() {
    let content = b"BT /F1 12 Tf \
        1 0 0 1 50 600 Tm (Body) Tj \
        1 0 0 1 300 15 Tm (2026) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 2);
    assert_eq!(page.semantic_nodes[1].role, LayoutNodeRole::Unclassified);
    assert_eq!(page.orders.main_flow, page.orders.inferred_order);
}

#[test]
fn rotated_text_uses_source_order_fallback_and_warns_once() {
    let content = b"BT /F1 12 Tf \
        0 1 -1 0 100 100 Tm (Rotated) Tj \
        1 0 0 1 50 600 Tm (Body) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.orders.source_order, page.orders.inferred_order);
    assert!(
        page.semantic_nodes
            .iter()
            .all(|node| node.rule_id == "stage3_source_fallback_v1")
    );
    assert_eq!(
        layout
            .warnings
            .iter()
            .filter(|warning| warning.code == "reading_order_ambiguous")
            .count(),
        1
    );
}

#[test]
fn artifact_is_preserved_but_excluded_from_main_flow() {
    let content = b"/Artifact BMC \
        BT /F1 12 Tf 1 0 0 1 50 700 Tm (Watermark) Tj ET EMC \
        BT /F1 12 Tf 1 0 0 1 50 600 Tm (Body) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];

    assert_eq!(page.semantic_nodes.len(), 2);
    assert_eq!(page.semantic_nodes[0].role, LayoutNodeRole::Artifact);
    assert!(page.semantic_nodes[0].artifact);
    assert_eq!(page.orders.inferred_order.len(), 2);
    assert_eq!(page.orders.main_flow, ["p0-n1"]);
}

#[test]
fn cjk_spans_do_not_receive_a_synthetic_general_gap_space() {
    let cmap = b"1 begincodespacerange <00> <ff> endcodespacerange\n\
        2 beginbfchar <01> <4f60> <02> <597d> endbfchar";
    let content = b"BT /F1 12 Tf \
        1 0 0 1 50 700 Tm <01> Tj \
        1 0 0 1 100 700 Tm <02> Tj ET";
    let layout = layout(&single_page_tounicode_pdf(content, cmap));

    assert_eq!(layout.pages[0].semantic_nodes.len(), 1);
    assert_eq!(layout.pages[0].semantic_nodes[0].text, "\u{4f60}\u{597d}");
}

#[test]
fn author_heading_role_wins_over_repeated_header_geometry() {
    let layout = layout(&three_page_author_heading_pdf());

    for page in &layout.pages {
        let heading = &page.semantic_nodes[0];
        assert_eq!(heading.text, "Chapter");
        assert_eq!(heading.role, LayoutNodeRole::Heading);
        assert!(page.orders.main_flow.contains(&heading.id));
    }
}
#[test]
fn text_span_limit_accepts_exact_boundary_and_rejects_one_over() {
    let pdf = single_page_pdf(b"BT /F1 12 Tf 1 0 0 1 50 700 Tm (AB) Tj ET");
    let exact = ParseLimits {
        max_text_spans: 2,
        ..ParseLimits::default()
    };
    PdfDocument::parse_with_limits(&pdf, exact)
        .expect("valid PDF at exact boundary")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("exact span boundary must succeed");

    let exceeded = ParseLimits {
        max_text_spans: 1,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&pdf, exceeded)
        .expect("valid bounded PDF")
        .extract_layout(LayoutExtractionOptions::default())
        .expect_err("one over the span limit must fail");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}
#[test]
fn visual_reading_uses_focus_candidates_and_a_non_linear_block_graph() {
    let content = b"BT /F1 24 Tf \
        1 0 0 1 250 650 Tm (Visual Title) Tj \
        /F1 12 Tf 1 0 0 1 50 500 Tm (First body) Tj \
        1 0 0 1 50 350 Tm (Second body) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];
    let visual = page.visual_reading.as_ref().expect("visual reading graph");

    assert!(layout.capabilities.visual_reading);
    assert_eq!(visual.blocks.len(), page.semantic_nodes.len());
    assert!(visual.blocks.iter().all(|block| {
        block.internal_order == LayoutVisualBlockOrder::Simultaneous
            && page
                .semantic_nodes
                .iter()
                .any(|node| node.id == block.node_id)
    }));

    let first_focus = visual.focus_candidates.first().expect("focus candidate");
    let focus_block = visual
        .blocks
        .iter()
        .find(|block| block.id == first_focus.block_id)
        .expect("focus block");
    let focus_node = page
        .semantic_nodes
        .iter()
        .find(|node| node.id == focus_block.node_id)
        .expect("focus node");
    assert_eq!(focus_node.text, "Visual Title");
    assert!(focus_block.cues.contains(&LayoutVisualCue::LargeText));
    assert!(visual.focus_candidates.len() > 1);

    assert!(visual.transitions.len() > visual.blocks.len() - 1);
    for kind in [
        LayoutVisualTransitionKind::Continue,
        LayoutVisualTransitionKind::SkipAhead,
        LayoutVisualTransitionKind::Regression,
    ] {
        assert!(visual.transitions.iter().any(|edge| edge.kind == kind));
    }

    let json = serde_json::to_value(&layout).expect("serializable shared Layout IR");
    assert_eq!(json["capabilities"]["visual_reading"], true);
    assert_eq!(
        json["pages"][0]["visual_reading"]["blocks"][0]["internal_order"],
        "simultaneous"
    );
    assert!(
        json["pages"][0]["visual_reading"]["transitions"]
            .as_array()
            .expect("serialized transitions")
            .iter()
            .any(|edge| edge["kind"] == "skip_ahead")
    );
}

#[test]
fn visual_reading_preserves_blocks_that_may_go_unnoticed() {
    let content = b"/Artifact BMC \
        BT /F1 12 Tf 1 0 0 1 50 700 Tm (Watermark) Tj ET EMC \
        BT /F1 12 Tf 1 0 0 1 50 500 Tm (Body) Tj ET";
    let layout = layout(&single_page_pdf(content));
    let page = &layout.pages[0];
    let visual = page.visual_reading.as_ref().expect("visual reading graph");
    let artifact_node = page
        .semantic_nodes
        .iter()
        .find(|node| node.role == LayoutNodeRole::Artifact)
        .expect("artifact node");
    let artifact_block = visual
        .blocks
        .iter()
        .find(|block| block.node_id == artifact_node.id)
        .expect("artifact remains represented");

    assert!(artifact_block.may_be_skipped);
    assert!(artifact_block.cues.contains(&LayoutVisualCue::Artifact));
    assert!(
        visual
            .focus_candidates
            .iter()
            .all(|focus| focus.block_id != artifact_block.id)
    );
}
fn layout(pdf: &[u8]) -> pdf_core::DocumentLayout {
    PdfDocument::parse(pdf)
        .expect("valid generated PDF")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("Stage 3 layout")
}

fn single_page_pdf(content: &[u8]) -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
    ])
}

fn single_page_tounicode_pdf(content: &[u8], cmap: &[u8]) -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap),
    ])
}

fn three_page_author_heading_pdf() -> Vec<u8> {
    let contents = (1..=3)
        .map(|page| {
            format!(
                "/H1 BMC BT /F1 12 Tf 1 0 0 1 50 770 Tm (Chapter) Tj ET EMC BT /F1 12 Tf 1 0 0 1 50 600 Tm (Body{page}) Tj ET"
            )
            .into_bytes()
        })
        .collect::<Vec<_>>();
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 6 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 7 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 8 0 R >>".to_vec(),
        stream_body(&contents[0]),
        stream_body(&contents[1]),
        stream_body(&contents[2]),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
    ])
}
fn three_page_pdf() -> Vec<u8> {
    let contents = (1..=3)
        .map(|page| {
            format!(
                "BT /F1 12 Tf 1 0 0 1 50 770 Tm (Report 2026) Tj 1 0 0 1 50 600 Tm (Body{page}) Tj 1 0 0 1 50 35 Tm (Confidential) Tj 1 0 0 1 300 15 Tm ({page}) Tj ET"
            )
            .into_bytes()
        })
        .collect::<Vec<_>>();
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R] /Count 3 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 6 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 7 0 R >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 9 0 R >> >> /Contents 8 0 R >>".to_vec(),
        stream_body(&contents[0]),
        stream_body(&contents[1]),
        stream_body(&contents[2]),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
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
