use pdf_core::{
    CoordinateSpace, ExtractionMode, LAYOUT_IR_SCHEMA_VERSION, LayoutExtractionOptions,
    PdfDocument, TextExtractionOptionsV2, layout_coordinate_space,
};

const TOLERANCE: f64 = 1.0e-6;

#[test]
fn layout_ir_is_versioned_deterministic_and_coordinate_safe() {
    let document = PdfDocument::parse(&text_pdf()).expect("valid text PDF");
    let legacy = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("legacy positioned glyphs");
    assert_point(legacy.glyphs[0].origin, [20.0, 200.0]);

    let first = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("layout IR");
    let second = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("deterministic layout IR");
    assert_eq!(first, second);
    assert_eq!(first.schema_version, LAYOUT_IR_SCHEMA_VERSION);
    assert_eq!(first.schema_version, 1);
    assert_eq!(first.coordinate_space, CoordinateSpace::LayoutSpace);
    assert_eq!(layout_coordinate_space(), "layout_unrotated_top_left");
    assert_eq!(first.options_digest.len(), 64);
    assert_eq!(first.text, "AB");
    assert!(first.timings.is_none());
    assert!(first.quality.is_some());
    assert!(first.capabilities.source_order);
    assert!(first.capabilities.text_blocks);
    assert!(!first.capabilities.tagged_order);
    assert!(first.capabilities.inferred_order);
    assert!(first.capabilities.main_flow);
    assert!(!first.capabilities.semantic_roles);
    assert!(first.capabilities.tables);
    assert!(first.capabilities.image_placements);
    assert!(first.capabilities.navigation);
    assert!(first.named_destinations.is_empty());
    assert!(first.outlines.is_empty());

    let page = &first.pages[0];
    assert_eq!(page.page_index, 0);
    assert_eq!(page.page_number, 1);
    assert_eq!(page.object.number, 3);
    assert_eq!(page.coordinate_space, CoordinateSpace::LayoutSpace);
    assert_close(page.geometry.layout_bounds.width(), 200.0);
    assert_close(page.geometry.layout_bounds.height(), 400.0);
    assert_close(page.geometry.display_bounds.width(), 400.0);
    assert_close(page.geometry.display_bounds.height(), 200.0);
    assert!(page.debug_glyphs.is_none());
    assert!(page.tables.is_empty());
    assert!(page.image_placements.is_empty());
    assert!(page.links.is_empty());
    assert_eq!(page.orders.source_order, ["p0-n0"]);
    assert!(page.orders.tagged_order.is_empty());
    assert_eq!(page.orders.inferred_order, ["p0-n0"]);
    assert_eq!(page.orders.main_flow, ["p0-n0"]);

    let node = &page.semantic_nodes[0];
    assert_eq!(node.id, "p0-n0");
    assert_eq!(node.text, "AB");
    assert_eq!(node.rule_id, "stage3_paragraph_geometry_v1");
    assert_eq!(node.provenance.page_object.number, 3);
    assert_eq!(node.provenance.source_ordinal_start, 0);
    assert_eq!(node.provenance.source_ordinal_end, 1);
    assert!(!node.spans.is_empty());
    let span = &node.spans[0];
    assert_eq!(span.id, "p0-s0");
    assert_eq!(span.rule_id, "stage1b_source_span_v1");
    assert_close(span.origin.x, 20.0);
    assert_close(span.origin.y, 40.0);
    assert!(span.bbox.width() > 0.0);
    assert!(span.bbox.height() > 0.0);
    assert!(span.confidence > 0.0 && span.confidence < 1.0);
    assert_eq!(span.provenance.page_object.number, 3);
    assert!(
        first
            .warnings
            .iter()
            .any(|warning| warning.code == "layout_text_bbox_estimated")
    );

    let serialized = serde_json::to_value(&first).expect("serialize Layout IR");
    assert_eq!(serialized["coordinate_space"], "layout_unrotated_top_left");
    assert_eq!(
        serialized["pages"][0]["geometry"]["coordinate_space"],
        "layout_unrotated_top_left"
    );
    assert!(serialized.get("timings").is_none());
    assert!(serialized["pages"][0].get("debug_glyphs").is_none());
}

#[test]
fn debug_glyphs_and_timings_are_explicit_opt_ins() {
    let document = PdfDocument::parse(&text_pdf()).expect("valid text PDF");
    let default = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("default layout");
    let options = LayoutExtractionOptions {
        include_debug_glyphs: true,
        include_timings: true,
        ..LayoutExtractionOptions::default()
    };
    let detailed = document.extract_layout(options).expect("detailed layout");
    assert_ne!(default.options_digest, detailed.options_digest);
    assert!(detailed.timings.is_some());
    let glyphs = detailed.pages[0]
        .debug_glyphs
        .as_ref()
        .expect("debug glyphs");
    assert_eq!(glyphs.len(), 2);
    assert_close(glyphs[0].origin.x, 20.0);
    assert_close(glyphs[0].origin.y, 40.0);
    assert_eq!(glyphs[0].rule_id, "stage1b_glyph_projection_v1");
    assert!(glyphs[0].bbox.width() > 0.0);
    assert!(glyphs[0].bbox.height() > 0.0);
}

#[test]
fn text_bbox_uses_effective_text_matrix_scale_and_font_vertical_metrics() {
    let document = PdfDocument::parse(&scaled_text_pdf()).expect("valid scaled text PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..LayoutExtractionOptions::default()
        })
        .expect("scaled layout IR");
    let glyph = &layout.pages[0].debug_glyphs.as_ref().expect("debug glyphs")[0];

    assert_close(glyph.origin.x, 20.0);
    assert_close(glyph.origin.y, 100.0);
    assert_close(glyph.bbox.x0, 20.0);
    assert_close(glyph.bbox.x1, 26.0);
    assert_close(glyph.bbox.y0, 91.0);
    assert_close(glyph.bbox.y1, 103.0);
    assert_close(glyph.bbox.height(), 12.0);
}
#[test]
fn text_bbox_uses_ctm_scale_and_bounded_default_vertical_metrics() {
    let document = PdfDocument::parse(&ctm_scaled_text_pdf()).expect("valid CTM text PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..LayoutExtractionOptions::default()
        })
        .expect("CTM layout IR");
    let glyph = &layout.pages[0].debug_glyphs.as_ref().expect("debug glyphs")[0];

    assert_close(glyph.origin.x, 20.0);
    assert_close(glyph.origin.y, 340.0);
    assert_close(glyph.bbox.x0, 20.0);
    assert_close(glyph.bbox.x1, 28.0);
    assert_close(glyph.bbox.y0, 316.0);
    assert_close(glyph.bbox.y1, 346.0);
    assert_close(glyph.bbox.height(), 30.0);
}

#[test]
fn empty_pages_keep_all_orders_explicit_and_empty() {
    let document = PdfDocument::parse(&empty_pdf()).expect("valid empty page PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("empty layout");
    assert_eq!(layout.pages.len(), 1);
    let page = &layout.pages[0];
    assert!(page.semantic_nodes.is_empty());
    assert!(page.orders.source_order.is_empty());
    assert!(page.orders.tagged_order.is_empty());
    assert!(page.orders.inferred_order.is_empty());
    assert!(page.orders.main_flow.is_empty());
    assert!(
        !layout
            .warnings
            .iter()
            .any(|warning| warning.code == "layout_text_bbox_estimated")
    );
}

fn text_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 1 0 0 1 20 200 Tm (AB) Tj ET";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 210 380] \
          /CropBox [10 20 110 220] /UserUnit 2 /Rotate 90 \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ];
    classic_pdf(&objects)
}

fn scaled_text_pdf() -> Vec<u8> {
    let content = b"BT /F1 1 Tf 12 0 0 12 20 100 Tm (A) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding /FontDescriptor 6 0 R >>"
            .to_vec(),
        b"<< /Type /FontDescriptor /FontName /Helvetica /Ascent 750 /Descent -250 >>".to_vec(),
    ])
}
fn ctm_scaled_text_pdf() -> Vec<u8> {
    let content = b"2 0 0 3 0 0 cm BT /F1 2 Tf 4 0 0 5 10 20 Tm (A) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ])
}

fn empty_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>".to_vec(),
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

fn assert_close(actual: f64, expected: f64) {
    assert!(
        (actual - expected).abs() <= TOLERANCE,
        "{actual} != {expected}"
    );
}

fn assert_point(actual: [f64; 2], expected: [f64; 2]) {
    assert_close(actual[0], expected[0]);
    assert_close(actual[1], expected[1]);
}
