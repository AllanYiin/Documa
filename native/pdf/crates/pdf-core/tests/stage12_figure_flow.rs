use pdf_core::{LayoutExtractionOptions, LayoutNodeRole, PdfDocument};

#[test]
fn tagged_figure_and_caption_link_author_metadata_without_changing_orders() {
    let layout = PdfDocument::parse(&tagged_figure_caption_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    assert_eq!(page.semantic_nodes.len(), 1);
    assert_eq!(page.semantic_nodes[0].role, LayoutNodeRole::Caption);
    assert_eq!(page.orders.source_order, vec!["p0-n0"]);
    assert_eq!(page.orders.tagged_order, vec!["p0-n0"]);
    assert_eq!(page.orders.inferred_order, vec!["p0-n0"]);
    assert_eq!(page.orders.main_flow, vec!["p0-n0"]);
    let placement = &page.image_placements[0];
    assert_eq!(placement.provenance.mcids, vec![0]);
    assert_eq!(placement.tag.as_deref(), Some("Figure"));
    assert!(!placement.artifact);
    assert_eq!(placement.structure_object.unwrap().number, 8);
    assert_eq!(placement.alt_text.as_deref(), Some("author alt"));
    assert_eq!(placement.source_node_ids, vec!["p0-n0"]);
    assert_eq!(placement.rule_id, "stage5b_tagged_figure_caption_v1");
    assert!(!has_warning(&layout, "image_placement_unassigned"));
}

#[test]
fn untagged_caption_prefix_links_by_conservative_geometry() {
    let layout = PdfDocument::parse(&untagged_figure_caption_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let placement = &layout.pages[0].image_placements[0];
    assert_eq!(placement.source_node_ids, vec!["p0-n0"]);
    assert_eq!(placement.rule_id, "stage5b_geometry_caption_v1");
    assert!((placement.confidence - 0.85).abs() < f32::EPSILON);
}

#[test]
fn artifact_image_retains_context_without_becoming_main_flow_or_warning() {
    let layout = PdfDocument::parse(&artifact_image_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    let placement = &page.image_placements[0];
    assert!(placement.artifact);
    assert_eq!(placement.tag.as_deref(), Some("Artifact"));
    assert!(placement.source_node_ids.is_empty());
    assert!(page.orders.main_flow.is_empty());
    assert!(!has_warning(&layout, "image_placement_unassigned"));
}

#[test]
fn equally_plausible_captions_warn_and_remain_unlinked() {
    let layout = PdfDocument::parse(&ambiguous_caption_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.pages[0].semantic_nodes.len(), 2);
    assert!(
        layout.pages[0].image_placements[0]
            .source_node_ids
            .is_empty()
    );
    assert!(has_warning(&layout, "figure_caption_ambiguous"));
}

#[test]
fn caption_like_table_cell_is_not_linked_to_author_figure() {
    let layout = PdfDocument::parse(&tagged_figure_above_table_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    assert_eq!(page.tables.len(), 1);
    assert!(page.image_placements[0].source_node_ids.is_empty());
    assert!(has_warning(&layout, "image_placement_unassigned"));
    assert!(!has_warning(&layout, "figure_caption_ambiguous"));
}

fn has_warning(layout: &pdf_core::DocumentLayout, code: &str) -> bool {
    layout.warnings.iter().any(|warning| warning.code == code)
}

fn ambiguous_caption_pdf() -> Vec<u8> {
    let content = b"q 100 0 0 50 20 100 cm /Im Do Q BT /F1 10 Tf 1 0 0 1 20 75 Tm (Figure 1 below) Tj 1 0 0 1 20 162 Tm (Figure 1 above) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        font(),
        image_stream(),
    ])
}

fn tagged_figure_above_table_pdf() -> Vec<u8> {
    let content = b"/Figure << /MCID 0 >> BDC q 100 0 0 50 50 220 cm /Im Do Q EMC 50 50 200 150 re S 50 125 m 250 125 l S 150 50 m 150 200 l S BT /F1 10 Tf 1 0 0 1 70 180 Tm (Figure 1 cell) Tj 1 0 0 1 170 180 Tm (B) Tj 1 0 0 1 70 80 Tm (C) Tj 1 0 0 1 170 80 Tm (D) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 7 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /StructParents 0 /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        font(),
        image_stream(),
        b"<< /Type /StructTreeRoot /K 8 0 R /ParentTree 9 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /Figure /Pg 3 0 R /Alt (author figure) /K 0 >>".to_vec(),
        b"<< /Nums [0 [8 0 R]] >>".to_vec(),
    ])
}

fn tagged_figure_caption_pdf() -> Vec<u8> {
    let content = b"/Figure << /MCID 0 /Alt (content alt) >> BDC q 100 0 0 50 20 100 cm /Im Do Q EMC BT /F1 10 Tf 1 0 0 1 20 75 Tm /Caption << /MCID 1 >> BDC (Figure 1 caption) Tj EMC ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 7 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /StructParents 0 /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        font(),
        image_stream(),
        b"<< /Type /StructTreeRoot /K [8 0 R 9 0 R] /ParentTree 10 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /Figure /Pg 3 0 R /Alt (author alt) /K 0 >>".to_vec(),
        b"<< /Type /StructElem /S /Caption /Pg 3 0 R /K 1 >>".to_vec(),
        b"<< /Nums [0 [8 0 R 9 0 R]] >>".to_vec(),
    ])
}

fn untagged_figure_caption_pdf() -> Vec<u8> {
    let content =
        b"q 100 0 0 50 20 100 cm /Im Do Q BT /F1 10 Tf 1 0 0 1 20 75 Tm (Figure 1 caption) Tj ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        font(),
        image_stream(),
    ])
}

fn artifact_image_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(b"/Artifact BMC q 100 0 0 50 20 100 cm /Im Do Q EMC"),
        image_stream(),
    ])
}

fn font() -> Vec<u8> {
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec()
}

fn image_stream() -> Vec<u8> {
    let mut body = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n".to_vec();
    body.push(0);
    body.extend_from_slice(b"\nendstream");
    body
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
