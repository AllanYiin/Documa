use pdf_core::{
    ErrorCode, ExtractionMode, LayoutExtractionOptions, LayoutNodeRole, ParseLimits, PdfDocument,
    TextExtractionOptionsV2,
};

#[test]
fn marked_content_metadata_reaches_layout_without_changing_legacy_json() {
    let content = b"BT /F1 12 Tf \
        /P << /MCID 0 /Alt (paragraph description) >> BDC (A) Tj EMC \
        /Artifact BMC /Span << /MCID 1 >> BDC (H) Tj EMC EMC \
        /P << /MCID 2 /ActualText (ffi) /Alt (ligature description) >> BDC (X) Tj EMC ET";
    let pdf = text_pdf(content);
    let document = PdfDocument::parse(&pdf).expect("valid marked-content PDF");

    let legacy = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("legacy V2 extraction");
    assert_eq!(legacy.text, "AHffi");
    let serialized = serde_json::to_value(&legacy).expect("serialize legacy extraction");
    let glyph = serialized["glyphs"][0]
        .as_object()
        .expect("legacy glyph object");
    for forbidden in ["tag", "alt_text", "actual_text", "artifact"] {
        assert!(
            !glyph.contains_key(forbidden),
            "legacy glyph leaked {forbidden}"
        );
    }

    let layout = document
        .extract_layout(LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..LayoutExtractionOptions::default()
        })
        .expect("Stage 2 marked-content layout");
    assert!(layout.capabilities.semantic_roles);
    assert!(!layout.capabilities.tagged_order);
    let page = &layout.pages[0];
    assert_eq!(page.semantic_nodes.len(), 3);
    assert_eq!(page.orders.source_order, ["p0-n0", "p0-n1", "p0-n2"]);
    assert!(page.orders.tagged_order.is_empty());

    let paragraph = &page.semantic_nodes[0];
    assert_eq!(paragraph.text, "A");
    assert_eq!(paragraph.tag.as_deref(), Some("P"));
    assert_eq!(paragraph.alt_text.as_deref(), Some("paragraph description"));
    assert_eq!(paragraph.role, LayoutNodeRole::Paragraph);
    assert!(!paragraph.artifact);
    assert_eq!(paragraph.provenance.mcids, [0]);

    let artifact = &page.semantic_nodes[1];
    assert_eq!(artifact.text, "H");
    assert_eq!(artifact.tag.as_deref(), Some("Span"));
    assert_eq!(artifact.role, LayoutNodeRole::Artifact);
    assert!(artifact.artifact);
    assert_eq!(artifact.provenance.mcids, [1]);

    let replacement = &page.semantic_nodes[2];
    assert_eq!(replacement.text, "ffi");
    assert_eq!(replacement.actual_text.as_deref(), Some("ffi"));
    assert_eq!(
        replacement.alt_text.as_deref(),
        Some("ligature description")
    );
    assert_eq!(replacement.provenance.mcids, [2]);
    assert_eq!(replacement.spans.len(), 1);

    let debug = page.debug_glyphs.as_ref().expect("debug glyphs requested");
    assert_eq!(debug.len(), 3);
    assert!(debug[1].artifact);
    assert_eq!(debug[2].actual_text.as_deref(), Some("ffi"));
}

#[test]
fn invalid_negative_mcid_preserves_visible_text_and_tag() {
    let pdf = text_pdf(b"BT /F1 12 Tf /P << /MCID -1 >> BDC (A) Tj EMC ET");
    let document = PdfDocument::parse(&pdf).expect("syntactically valid PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("optional marked metadata must not lose text");

    assert_eq!(layout.text, "A");
    assert_eq!(layout.pages[0].semantic_nodes[0].tag.as_deref(), Some("P"));
    assert!(
        layout.pages[0].semantic_nodes[0]
            .provenance
            .mcids
            .is_empty()
    );
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "marked_content_invalid")
    );
}

#[test]
fn structure_tree_defines_tagged_order_roles_and_accessible_metadata() {
    let pdf = tagged_pdf(b"<< /Nums [0 [8 0 R 7 0 R]] >>", b"<< /CustomP /P >>");
    let document = PdfDocument::parse(&pdf).expect("valid tagged PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("tagged layout");

    assert_eq!(layout.text, "FirstSecond");
    assert!(layout.capabilities.tagged_order);
    assert!(layout.capabilities.semantic_roles);
    let page = &layout.pages[0];
    assert_eq!(page.orders.source_order, ["p0-n0", "p0-n1"]);
    assert_eq!(page.orders.tagged_order, ["p0-n1", "p0-n0"]);

    let first = &page.semantic_nodes[0];
    assert_eq!(first.text, "First");
    assert_eq!(first.tag.as_deref(), Some("H1"));
    assert_eq!(first.role, LayoutNodeRole::Heading);
    assert_eq!(first.actual_text.as_deref(), Some("metadata only"));
    assert_eq!(first.structure_object.expect("StructElem").number, 8);

    let second = &page.semantic_nodes[1];
    assert_eq!(second.text, "Second");
    assert_eq!(second.tag.as_deref(), Some("CustomP"));
    assert_eq!(second.role, LayoutNodeRole::Paragraph);
    assert_eq!(second.alt_text.as_deref(), Some("second description"));
    assert_eq!(second.structure_object.expect("StructElem").number, 7);
    assert!(
        !layout
            .warnings
            .iter()
            .any(|warning| warning.code == "parent_tree_mismatch")
    );
}

#[test]
fn parent_tree_mismatch_and_role_cycle_warn_without_losing_tagged_text() {
    let pdf = tagged_pdf(
        b"<< /Nums [0 [7 0 R 8 0 R]] >>",
        b"<< /CustomP /Other /Other /CustomP >>",
    );
    let document = PdfDocument::parse(&pdf).expect("syntactically valid tagged PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions::default())
        .expect("recoverable optional structure defects");

    assert_eq!(layout.text, "FirstSecond");
    assert_eq!(layout.pages[0].orders.tagged_order, ["p0-n1", "p0-n0"]);
    assert_eq!(
        layout.pages[0].semantic_nodes[1].role,
        LayoutNodeRole::Unclassified
    );
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "tagged_structure_cycle")
    );
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "parent_tree_mismatch")
    );
}

#[test]
fn untagged_layout_omits_empty_stage2_fields_from_json() {
    let pdf = text_pdf(b"BT /F1 12 Tf (Plain) Tj ET");
    let document = PdfDocument::parse(&pdf).expect("valid untagged PDF");
    let layout = document
        .extract_layout(LayoutExtractionOptions {
            include_debug_glyphs: true,
            ..LayoutExtractionOptions::default()
        })
        .expect("untagged layout");
    let json = serde_json::to_value(layout).expect("serialize Layout IR");
    let node = json["pages"][0]["semantic_nodes"][0]
        .as_object()
        .expect("node object");
    let span = node["spans"][0].as_object().expect("span object");
    let glyph = json["pages"][0]["debug_glyphs"][0]
        .as_object()
        .expect("glyph object");
    for object in [node, span, glyph] {
        for field in ["tag", "alt_text", "actual_text", "artifact"] {
            assert!(
                !object.contains_key(field),
                "empty Stage 2 field leaked: {field}"
            );
        }
    }
    assert!(!node.contains_key("structure_object"));
}

#[test]
fn structure_and_parent_tree_limits_have_exact_boundaries() {
    let pdf = tagged_pdf(b"<< /Nums [0 [8 0 R 7 0 R]] >>", b"<< /CustomP /P >>");
    let exact = ParseLimits {
        max_structure_elements: 2,
        max_parent_tree_entries: 1,
        ..ParseLimits::default()
    };
    PdfDocument::parse_with_limits(&pdf, exact)
        .expect("exact structure limits parse")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("exact structure limits extract");

    let element_short = ParseLimits {
        max_structure_elements: 1,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&pdf, element_short)
        .expect("document parsing is independent of structure traversal")
        .extract_layout(LayoutExtractionOptions::default())
        .expect_err("structure element limit must be enforced");
    assert_eq!(error.code, ErrorCode::LimitExceeded);

    let parent_short = ParseLimits {
        max_parent_tree_entries: 0,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&pdf, parent_short)
        .expect("document parsing is independent of ParentTree traversal")
        .extract_layout(LayoutExtractionOptions::default())
        .expect_err("ParentTree entry limit must be enforced");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn cyclic_structure_is_warned_and_visible_text_is_preserved() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K 7 0 R >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("syntactically valid cyclic structure")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("cycle is optional metadata failure");

    assert_eq!(layout.text, "Visible");
    assert!(!layout.capabilities.tagged_order);
    assert!(layout.pages[0].orders.tagged_order.is_empty());
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "tagged_structure_cycle")
    );
}

#[test]
fn mcr_dictionary_associates_content_and_parent_tree() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R /ParentTree 8 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K << /Type /MCR /MCID 0 >> >>".to_vec(),
        b"<< /Nums [0 [7 0 R]] >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("valid MCR PDF")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("MCR layout");

    assert_eq!(layout.pages[0].orders.tagged_order, ["p0-n0"]);
    assert_eq!(
        layout.pages[0].semantic_nodes[0].role,
        LayoutNodeRole::Paragraph
    );
    assert!(
        !layout
            .warnings
            .iter()
            .any(|warning| warning.code == "parent_tree_mismatch")
    );
}

#[test]
fn structure_mcid_without_page_content_warns_without_text_loss() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K 9 >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("syntactically valid structure")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("missing MCID is recoverable");

    assert_eq!(layout.text, "Visible");
    assert!(!layout.capabilities.tagged_order);
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "tagged_mcid_missing")
    );
}
#[test]
fn duplicate_structure_mcid_emits_one_ambiguous_warning() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K [7 0 R 7 0 R] >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K 0 >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("valid duplicate association PDF")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("duplicate association is recoverable");

    assert_eq!(layout.pages[0].orders.tagged_order, ["p0-n0"]);
    assert_eq!(
        layout
            .warnings
            .iter()
            .filter(|warning| warning.code == "tagged_mcid_ambiguous")
            .count(),
        1
    );
}

#[test]
fn indirect_structure_depth_is_bounded() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let mut objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R >>".to_vec(),
    ];
    for object_number in 7..=17 {
        let kid = if object_number == 17 {
            "0".to_owned()
        } else {
            format!("{} 0 R", object_number + 1)
        };
        objects.push(format!("<< /Type /StructElem /S /P /Pg 3 0 R /K {kid} >>").into_bytes());
    }
    let pdf = classic_pdf(&objects);
    let limits = ParseLimits {
        max_object_depth: 8,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&pdf, limits)
        .expect("indirect chain parses within ordinary object depth")
        .extract_layout(LayoutExtractionOptions::default())
        .expect_err("structure traversal depth must be bounded");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}
#[test]
fn parent_tree_kids_number_tree_is_traversed() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /StructParents 0 /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R /ParentTree 8 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K 0 >>".to_vec(),
        b"<< /Kids [9 0 R] >>".to_vec(),
        b"<< /Nums [0 [7 0 R]] >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("valid ParentTree Kids PDF")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("ParentTree Kids layout");

    assert_eq!(layout.pages[0].orders.tagged_order, ["p0-n0"]);
    assert!(
        !layout
            .warnings
            .iter()
            .any(|warning| warning.code == "parent_tree_mismatch")
    );
}

#[test]
fn deferred_objr_and_stream_mcr_warn_without_dropping_text() {
    let content = b"BT /F1 12 Tf /P << /MCID 0 >> BDC (Visible) Tj EMC ET";
    let pdf = classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        b"<< /Type /StructTreeRoot /K 7 0 R >>".to_vec(),
        b"<< /Type /StructElem /S /P /Pg 3 0 R /K [<< /Type /OBJR /Obj 10 0 R >> << /Type /MCR /MCID 0 /Stm 4 0 R >>] >>".to_vec(),
    ]);
    let layout = PdfDocument::parse(&pdf)
        .expect("syntactically valid deferred references")
        .extract_layout(LayoutExtractionOptions::default())
        .expect("deferred references are recoverable");

    assert_eq!(layout.text, "Visible");
    assert!(!layout.capabilities.tagged_order);
    assert_eq!(
        layout
            .warnings
            .iter()
            .filter(|warning| warning.code == "tagged_object_reference_unsupported")
            .count(),
        2
    );
}
fn tagged_pdf(parent_tree: &[u8], role_map: &[u8]) -> Vec<u8> {
    let content = b"BT /F1 12 Tf \
        /P << /MCID 0 >> BDC (First) Tj EMC \
        /P << /MCID 1 >> BDC (Second) Tj EMC ET";
    let mut root = b"<< /Type /StructTreeRoot /RoleMap ".to_vec();
    root.extend_from_slice(role_map);
    root.extend_from_slice(b" /K [7 0 R 8 0 R] /ParentTree 9 0 R >>");
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 6 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /StructParents 0 \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
        root,
        b"<< /Type /StructElem /S /CustomP /Pg 3 0 R /Alt (second description) /K 1 >>".to_vec(),
        b"<< /Type /StructElem /S /H1 /Pg 3 0 R /ActualText (metadata only) /K 0 >>".to_vec(),
        parent_tree.to_vec(),
    ])
}
fn text_pdf(content: &[u8]) -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
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
