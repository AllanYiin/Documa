use std::{collections::BTreeSet, fmt::Write as _};

use pdf_core::{
    ErrorCode, LayoutExtractionOptions, LayoutNodeRole, LayoutTableCellRole, LayoutTableEvidence,
    ParseLimits, PdfDocument,
};

#[test]
fn tagged_table_preserves_spans_headers_text_geometry_and_node_links() {
    let layout = PdfDocument::parse(&tagged_table_pdf(false))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    let table = &page.tables[0];
    assert!(layout.capabilities.tables);
    assert_eq!(table.evidence, LayoutTableEvidence::Tagged);
    assert_eq!((table.rows, table.columns, table.cells.len()), (3, 3, 7));
    assert_eq!(table.structure_object.unwrap().number, 7);
    assert_eq!(
        (table.cells[0].row_span, table.cells[0].column_span),
        (1, 2)
    );
    assert_eq!(table.cells[0].role, LayoutTableCellRole::ColumnHeader);
    assert_eq!(
        (table.cells[2].row_span, table.cells[2].column_span),
        (2, 1)
    );
    assert_eq!(table.cells[2].role, LayoutTableCellRole::RowHeader);
    assert!(table.cells.iter().all(|cell| cell.bbox.is_some()));
    assert_eq!(table.source_node_ids.len(), 7);
    let inferred = page
        .orders
        .inferred_order
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>();
    assert!(table.source_node_ids.iter().all(|id| inferred.contains(id)));
    assert_eq!(
        page.semantic_nodes
            .iter()
            .filter(|node| node.role == LayoutNodeRole::TableHeader)
            .count(),
        3
    );
}

#[test]
fn matching_tagged_and_vector_evidence_fuses_without_losing_author_semantics() {
    let drawing = b"40 150 260 120 re S 40 190 m 300 190 l S \
        40 230 m 300 230 l S 140 150 m 140 270 l S 220 150 m 220 270 l S";
    let layout = PdfDocument::parse(&tagged_table_pdf_with_grid(false, drawing))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    assert_eq!(page.tables.len(), 1);
    let table = &page.tables[0];
    assert_eq!(table.evidence, LayoutTableEvidence::Fused);
    assert_eq!((table.rows, table.columns, table.cells.len()), (3, 3, 7));
    assert_eq!(table.cells[0].role, LayoutTableCellRole::ColumnHeader);
    assert_eq!(
        (table.cells[0].row_span, table.cells[0].column_span),
        (1, 2)
    );
    assert_eq!(table.cells[2].role, LayoutTableCellRole::RowHeader);
    assert_eq!(
        (table.cells[2].row_span, table.cells[2].column_span),
        (2, 1)
    );
    assert!(table.cells.iter().all(|cell| cell.bbox.is_some()));
    let bbox = table.bbox.unwrap();
    assert_eq!(
        (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
        (40.0, 30.0, 300.0, 150.0)
    );
    assert!(!has_warning(&layout, "table_evidence_conflict"));
}

#[test]
fn conflicting_vector_topology_preserves_tagged_table_and_warns_once() {
    let drawing = b"40 150 260 120 re S 40 190 m 300 190 l S \
        40 230 m 300 230 l S 100 150 m 100 270 l S 180 150 m 180 270 l S \
        230 150 m 230 270 l S";
    let layout = PdfDocument::parse(&tagged_table_pdf_with_grid(false, drawing))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    assert_eq!(page.tables.len(), 1);
    assert_eq!(page.tables[0].evidence, LayoutTableEvidence::Tagged);
    assert_eq!((page.tables[0].rows, page.tables[0].columns), (3, 3));
    assert_eq!(
        layout
            .warnings
            .iter()
            .filter(|warning| warning.code == "table_evidence_conflict")
            .count(),
        1
    );
}

#[test]
fn invalid_tagged_span_preserves_text_and_warns_without_emitting_table() {
    let layout = PdfDocument::parse(&tagged_table_pdf(true))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.text, "HeaderValueGroupA1B2");
    assert!(layout.pages[0].tables.is_empty());
    assert!(has_warning(&layout, "tagged_table_invalid"));
    assert_eq!(layout.pages[0].semantic_nodes.len(), 7);
}

#[test]
fn tagged_table_limits_have_exact_and_one_short_boundaries() {
    let pdf = tagged_table_pdf(false);
    let exact = ParseLimits {
        max_tables: 1,
        max_table_cells: 9,
        ..ParseLimits::default()
    };
    assert_eq!(
        PdfDocument::parse_with_limits(&pdf, exact)
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .tables
            .len(),
        1
    );
    for limits in [
        ParseLimits {
            max_tables: 0,
            ..ParseLimits::default()
        },
        ParseLimits {
            max_table_cells: 8,
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

#[test]
fn role_map_aliases_and_empty_tagged_cells_preserve_honest_optional_geometry() {
    let layout = PdfDocument::parse(&tagged_alias_empty_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let table = &layout.pages[0].tables[0];
    assert_eq!((table.rows, table.columns, table.cells.len()), (2, 2, 4));
    assert_eq!(table.cells[0].role, LayoutTableCellRole::ColumnHeader);
    let empty = &table.cells[3];
    assert!(empty.text.is_empty());
    assert!(empty.bbox.is_none());
    assert!(empty.provenance.is_none());
    assert!(empty.source_node_ids.is_empty());
    assert!(has_warning(&layout, "table_cell_unassigned"));
}

#[test]
fn ruled_table_uses_layout_space_and_preserves_cell_text() {
    let drawing = b"50 50 200 200 re S 50 150 m 250 150 l S 150 50 m 150 250 l S";
    let layout = PdfDocument::parse(&ruled_table_pdf(drawing))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let table = &layout.pages[0].tables[0];
    assert_eq!(table.evidence, LayoutTableEvidence::VectorLattice);
    assert_eq!((table.rows, table.columns), (2, 2));
    let bbox = table.bbox.unwrap();
    assert_eq!(
        (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
        (50.0, 50.0, 250.0, 250.0)
    );
    assert_eq!(
        table
            .cells
            .iter()
            .map(|cell| cell.text.as_str())
            .collect::<Vec<_>>(),
        ["A", "B", "C", "D"]
    );
}

#[test]
fn cjk_multiline_mixed_sizes_and_rotated_metadata_keep_layout_space_cells() {
    let layout = PdfDocument::parse(&cjk_multiline_rotated_ruled_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let table = &layout.pages[0].tables[0];
    assert_eq!(table.evidence, LayoutTableEvidence::VectorLattice);
    assert_eq!((table.rows, table.columns), (2, 2));
    assert_eq!(table.cells[0].text, "\u{53f0}\n\u{7063}");
    let bbox = table.bbox.unwrap();
    assert_eq!(
        (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
        (50.0, 50.0, 250.0, 250.0)
    );
    assert_eq!(layout.pages[0].geometry.rotation, 90);
}

#[test]
fn fragmented_rules_join_but_fill_only_boxes_do_not_become_tables() {
    let fragmented = b"50 50 m 149.75 50 l S 150.25 50 m 250 50 l S \
        50 150 m 149.75 150 l S 150.25 150 m 250 150 l S \
        50 250 m 149.75 250 l S 150.25 250 m 250 250 l S \
        50 50 m 50 149.75 l S 50 150.25 m 50 250 l S \
        150 50 m 150 149.75 l S 150 150.25 m 150 250 l S \
        250 50 m 250 149.75 l S 250 150.25 m 250 250 l S";
    assert_eq!(
        PdfDocument::parse(&ruled_table_pdf(fragmented))
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .tables
            .len(),
        1
    );
    let fill = b"50 50 100 100 re f 150 50 100 100 re f \
        50 150 100 100 re f 150 150 100 100 re f";
    assert!(
        PdfDocument::parse(&ruled_table_pdf(fill))
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .tables
            .is_empty()
    );
}

#[test]
fn form_matrix_and_page_projection_are_applied_exactly_once() {
    let layout = PdfDocument::parse(&form_ruled_table_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let bbox = layout.pages[0].tables[0].bbox.unwrap();
    assert_eq!(
        (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
        (50.0, 50.0, 250.0, 250.0)
    );

    let direct = b"q 2 0 0 2 10 20 cm 20 15 100 100 re S \
        20 65 m 120 65 l S 70 15 m 70 115 l S Q";
    let layout = PdfDocument::parse(&ruled_table_pdf(direct))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let bbox = layout.pages[0].tables[0].bbox.unwrap();
    assert_eq!(
        (bbox.x0, bbox.y0, bbox.x1, bbox.y1),
        (50.0, 50.0, 250.0, 250.0)
    );
}

#[test]
fn malformed_optional_vector_path_warns_and_preserves_text() {
    let layout = PdfDocument::parse(&ruled_table_pdf(b"50 50 l S"))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.text, "ABCD");
    assert!(layout.pages[0].tables.is_empty());
    assert!(has_warning(&layout, "vector_path_invalid"));
}

#[test]
fn vector_segment_and_candidate_limits_have_exact_boundaries() {
    let drawing = b"50 50 200 200 re S 50 150 m 250 150 l S 150 50 m 150 250 l S";
    let pdf = ruled_table_pdf(drawing);
    let exact = ParseLimits {
        max_path_segments: 6,
        max_table_candidates: 4,
        ..ParseLimits::default()
    };
    assert_eq!(
        PdfDocument::parse_with_limits(&pdf, exact)
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .tables
            .len(),
        1
    );
    for limits in [
        ParseLimits {
            max_path_segments: 5,
            ..ParseLimits::default()
        },
        ParseLimits {
            max_table_candidates: 3,
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

#[test]
fn borderless_three_column_table_uses_stable_text_alignment() {
    let rows = [
        ["Item", "Qty", "Price"],
        ["Alpha", "2", "10"],
        ["Beta", "3", "20"],
        ["Gamma", "4", "30"],
    ];
    let layout = PdfDocument::parse(&borderless_pdf(&rows, &[50.0, 160.0, 240.0]))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let table = layout.pages[0]
        .tables
        .iter()
        .find(|table| table.evidence == LayoutTableEvidence::TextAlignment)
        .unwrap();
    assert_eq!((table.rows, table.columns, table.cells.len()), (4, 3, 12));
    assert_eq!(table.cells[0].text, "Item");
    assert_eq!(table.cells[11].text, "30");
    let main = layout.pages[0]
        .orders
        .main_flow
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>();
    assert!(table.source_node_ids.iter().all(|id| main.contains(id)));
}

#[test]
fn text_alignment_limits_have_exact_and_one_short_boundaries() {
    let rows = [
        ["Item", "Qty", "Price"],
        ["Alpha", "2", "10"],
        ["Beta", "3", "20"],
        ["Gamma", "4", "30"],
    ];
    let pdf = borderless_pdf(&rows, &[50.0, 160.0, 240.0]);
    let exact = ParseLimits {
        max_table_candidates: 1,
        max_table_cells: 12,
        ..ParseLimits::default()
    };
    assert_eq!(
        PdfDocument::parse_with_limits(&pdf, exact)
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .tables
            .len(),
        1
    );
    for limits in [
        ParseLimits {
            max_table_candidates: 0,
            ..ParseLimits::default()
        },
        ParseLimits {
            max_table_cells: 11,
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
#[test]
fn two_column_numeric_table_is_supported_but_key_value_and_prose_are_rejected() {
    let numeric = [
        ["Item", "Total"],
        ["Alpha", "10"],
        ["Beta", "20"],
        ["Gamma", "30"],
    ];
    let layout = PdfDocument::parse(&borderless_pdf(&numeric, &[50.0, 220.0]))
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert!(
        layout.pages[0]
            .tables
            .iter()
            .any(|table| table.evidence == LayoutTableEvidence::TextAlignment)
    );

    let key_value = [
        ["Name:", "10"],
        ["Age:", "20"],
        ["Code:", "30"],
        ["Level:", "40"],
    ];
    let prose = [
        ["Left one", "Right one"],
        ["Left two", "Right two"],
        ["Left three", "Right three"],
        ["Left four", "Right four"],
    ];
    for rows in [&key_value, &prose] {
        let layout = PdfDocument::parse(&borderless_pdf(rows, &[50.0, 220.0]))
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap();
        assert!(layout.pages[0].tables.is_empty());
    }
}

#[test]
fn misaligned_borderless_candidate_stays_text_and_warns_once() {
    let rows = [
        ["Item", "Qty", "Price"],
        ["Alpha", "2", "10"],
        ["Beta", "3", "20"],
        ["Gamma", "4", "30"],
    ];
    let mut pdf = borderless_pdf(&rows, &[50.0, 160.0, 240.0]);
    replace_bytes(&mut pdf, b"1 0 0 1 240 130 Tm", b"1 0 0 1 265 130 Tm");
    let layout = PdfDocument::parse(&pdf)
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert!(layout.pages[0].tables.is_empty());
    assert_eq!(
        layout
            .warnings
            .iter()
            .filter(|warning| warning.code == "table_detection_ambiguous")
            .count(),
        1
    );
}

fn has_warning(layout: &pdf_core::DocumentLayout, code: &str) -> bool {
    layout.warnings.iter().any(|warning| warning.code == code)
}

fn replace_bytes(bytes: &mut Vec<u8>, old: &[u8], new: &[u8]) {
    let position = bytes
        .windows(old.len())
        .position(|window| window == old)
        .unwrap();
    bytes.splice(position..position + old.len(), new.iter().copied());
}

fn borderless_pdf<const C: usize>(rows: &[[&str; C]], xs: &[f64]) -> Vec<u8> {
    assert_eq!(C, xs.len());
    let mut content = String::from("BT /F1 10 Tf ");
    let mut y = 250.0;
    for values in rows {
        for (column, value) in values.iter().enumerate() {
            write!(content, "1 0 0 1 {} {y} Tm ({value}) Tj ", xs[column])
                .expect("writing to String cannot fail");
        }
        y -= 40.0;
    }
    content.push_str("ET");
    plain_page_pdf(content.as_bytes(), b"", b"")
}

fn ruled_table_pdf(drawing: &[u8]) -> Vec<u8> {
    let text = b"BT /F1 10 Tf 1 0 0 1 70 200 Tm (A) Tj \
        1 0 0 1 170 200 Tm (B) Tj 1 0 0 1 70 100 Tm (C) Tj \
        1 0 0 1 170 100 Tm (D) Tj ET";
    plain_page_pdf(text, drawing, b"")
}

fn plain_page_pdf(text: &[u8], prefix: &[u8], resources_extra: &[u8]) -> Vec<u8> {
    let mut content = prefix.to_vec();
    content.push(b' ');
    content.extend_from_slice(text);
    let mut page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /Resources << /Font << /F1 5 0 R >> ".to_vec();
    page.extend_from_slice(resources_extra);
    page.extend_from_slice(b" >> /Contents 4 0 R >>");
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        page,
        stream_body(&content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ])
}

fn cjk_multiline_rotated_ruled_pdf() -> Vec<u8> {
    let content = b"50 50 200 200 re S 50 150 m 250 150 l S 150 50 m 150 250 l S \
        BT /F1 14 Tf 1 0 0 1 70 235 Tm <81> Tj 1 0 0 1 70 165 Tm <82> Tj \
        /F1 8 Tf 1 0 0 1 170 200 Tm (B) Tj /F1 12 Tf 1 0 0 1 70 100 Tm (C) Tj \
        /F1 10 Tf 1 0 0 1 170 100 Tm (D) Tj ET";
    let cmap = b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n\
        /CIDSystemInfo << /Registry (Project) /Ordering (Unicode) /Supplement 0 >> def\n\
        /CMapName /Stage4-CJK def\n/CMapType 2 def\n\
        1 begincodespacerange <00> <ff> endcodespacerange\n\
        1 beginbfrange <20> <7e> <0020> endbfrange\n\
        2 beginbfchar\n<81> <53F0>\n<82> <7063>\nendbfchar\n\
        endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /Rotate 90 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap),
    ])
}

fn form_ruled_table_pdf() -> Vec<u8> {
    let page_text = b"/Fm Do BT /F1 10 Tf 1 0 0 1 70 200 Tm (A) Tj \
        1 0 0 1 170 200 Tm (B) Tj 1 0 0 1 70 100 Tm (C) Tj \
        1 0 0 1 170 100 Tm (D) Tj ET";
    let form_content = b"20 15 100 100 re S 20 65 m 120 65 l S 70 15 m 70 115 l S";
    let mut form = format!("<< /Type /XObject /Subtype /Form /BBox [0 0 150 150] /Matrix [2 0 0 2 10 20] /Length {} >>\nstream\n", form_content.len()).into_bytes();
    form.extend_from_slice(form_content);
    form.extend_from_slice(b"\nendstream");
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /Resources << /Font << /F1 5 0 R >> /XObject << /Fm 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(page_text),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        form,
    ])
}

fn tagged_table_pdf(invalid: bool) -> Vec<u8> {
    tagged_table_pdf_with_grid(invalid, b"")
}

fn tagged_table_pdf_with_grid(invalid: bool, drawing: &[u8]) -> Vec<u8> {
    let text = b"BT /F1 10 Tf 1 0 0 1 50 250 Tm /Span << /MCID 0 >> BDC (Header) Tj EMC 1 0 0 1 240 250 Tm /Span << /MCID 1 >> BDC (Value) Tj EMC 1 0 0 1 50 210 Tm /Span << /MCID 2 >> BDC (Group) Tj EMC 1 0 0 1 150 210 Tm /Span << /MCID 3 >> BDC (A) Tj EMC 1 0 0 1 240 210 Tm /Span << /MCID 4 >> BDC (1) Tj EMC 1 0 0 1 150 170 Tm /Span << /MCID 5 >> BDC (B) Tj EMC 1 0 0 1 240 170 Tm /Span << /MCID 6 >> BDC (2) Tj EMC ET";
    let mut content = drawing.to_vec();
    content.push(b' ');
    content.extend_from_slice(text);
    let span = if invalid { 4 } else { 2 };
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(&content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R /ParentTree 18 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /Table /Pg 3 0 R /K [8 0 R 11 0 R 15 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /TR /K [9 0 R 10 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /TH /A << /O /Table /ColSpan 2 /Scope /Column >> /K 0 >>".to_vec(),
        b"<< /Type /StructElem /S /TH /A << /O /Table /Scope /Column >> /K 1 >>".to_vec(),
        b"<< /Type /StructElem /S /TR /K [12 0 R 13 0 R 14 0 R] >>".to_vec(),
        format!("<< /Type /StructElem /S /TH /A << /O /Table /RowSpan {span} /Scope /Row >> /K 2 >>").into_bytes(),
        b"<< /Type /StructElem /S /TD /K 3 >>".to_vec(),
        b"<< /Type /StructElem /S /TD /K 4 >>".to_vec(),
        b"<< /Type /StructElem /S /TR /K [16 0 R 17 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /TD /K 5 >>".to_vec(),
        b"<< /Type /StructElem /S /TD /K 6 >>".to_vec(),
        b"<< /Nums [0 [9 0 R 10 0 R 12 0 R 13 0 R 14 0 R 16 0 R 17 0 R]] >>".to_vec(),
    ])
}

fn tagged_alias_empty_pdf() -> Vec<u8> {
    let content = b"BT /F1 10 Tf 1 0 0 1 50 250 Tm /Span << /MCID 0 >> BDC (H) Tj EMC 1 0 0 1 150 250 Tm /Span << /MCID 1 >> BDC (A) Tj EMC 1 0 0 1 50 210 Tm /Span << /MCID 2 >> BDC (B) Tj EMC ET";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 300] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /RoleMap << /Grid /Table /R /TR /Head /TH /Cell /TD >> /K 7 0 R /ParentTree 14 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /Grid /Pg 3 0 R /K [8 0 R 11 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /R /K [9 0 R 10 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /Head /A << /O /Table /Scope /Column >> /K 0 >>".to_vec(),
        b"<< /Type /StructElem /S /Cell /K 1 >>".to_vec(),
        b"<< /Type /StructElem /S /R /K [12 0 R 13 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /Cell /K 2 >>".to_vec(),
        b"<< /Type /StructElem /S /Cell >>".to_vec(),
        b"<< /Nums [0 [9 0 R 10 0 R 12 0 R]] >>".to_vec(),
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
    let mut offsets = Vec::new();
    for (index, body) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(body);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref = pdf.len();
    pdf.extend_from_slice(
        format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).as_bytes(),
    );
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    pdf
}
