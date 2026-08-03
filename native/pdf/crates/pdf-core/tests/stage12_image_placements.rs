use pdf_core::{ErrorCode, LayoutExtractionOptions, ParseLimits, PdfDocument};

#[test]
fn repeated_image_do_occurrences_preserve_layout_quad_bbox_object_and_ordinals() {
    let layout = PdfDocument::parse(&direct_image_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert!(layout.capabilities.image_placements);
    let page = &layout.pages[0];
    assert_eq!(page.image_placements.len(), 2);
    let first = &page.image_placements[0];
    assert_eq!(first.id, "p0-i0");
    assert_eq!(first.paint_ordinal, 0);
    assert_eq!(first.resource_name, "Im");
    assert_eq!(first.object.unwrap().number, 5);
    assert_eq!(
        (first.bbox.x0, first.bbox.y0, first.bbox.x1, first.bbox.y1),
        (20.0, 120.0, 120.0, 170.0)
    );
    assert_eq!(
        (first.quad.top_left.x, first.quad.top_left.y),
        (20.0, 120.0)
    );
    assert_eq!(
        (first.quad.bottom_right.x, first.quad.bottom_right.y),
        (120.0, 170.0)
    );
    assert!(first.source_node_ids.is_empty());
    assert!(first.structure_object.is_none());
    assert!(first.alt_text.is_none());
    assert_eq!(first.rule_id, "stage5a_image_do_v1");

    let second = &page.image_placements[1];
    assert_eq!(second.paint_ordinal, 1);
    assert_eq!(
        (
            second.bbox.x0,
            second.bbox.y0,
            second.bbox.x1,
            second.bbox.y1
        ),
        (170.0, 130.0, 200.0, 150.0)
    );
    assert_eq!(
        (second.quad.top_left.x, second.quad.top_left.y),
        (170.0, 150.0)
    );
    assert_eq!(
        (second.quad.top_right.x, second.quad.top_right.y),
        (170.0, 130.0)
    );
    assert_eq!(
        (second.quad.bottom_right.x, second.quad.bottom_right.y),
        (200.0, 130.0)
    );
    assert_eq!(
        (second.quad.bottom_left.x, second.quad.bottom_left.y),
        (200.0, 150.0)
    );
    assert_eq!(page.geometry.rotation, 90);
}

#[test]
fn form_matrix_places_nested_image_and_preserves_resource_path() {
    let layout = PdfDocument::parse(&form_image_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let placement = &layout.pages[0].image_placements[0];
    assert_eq!(placement.resource_name, "Fm/Im");
    assert_eq!(placement.object.unwrap().number, 6);
    assert_eq!(
        (
            placement.bbox.x0,
            placement.bbox.y0,
            placement.bbox.x1,
            placement.bbox.y1,
        ),
        (50.0, 110.0, 90.0, 140.0)
    );
}

#[test]
fn crop_box_user_unit_and_page_rotation_follow_layout_space_once() {
    let layout = PdfDocument::parse(&crop_user_unit_image_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    let page = &layout.pages[0];
    let placement = &page.image_placements[0];
    assert_eq!(
        page.geometry.coordinate_space.as_str(),
        "layout_unrotated_top_left"
    );
    assert_eq!(page.geometry.rotation, 270);
    assert_eq!(
        (
            placement.quad.top_left.x,
            placement.quad.top_left.y,
            placement.quad.top_right.x,
            placement.quad.top_right.y,
            placement.quad.bottom_right.x,
            placement.quad.bottom_right.y,
            placement.quad.bottom_left.x,
            placement.quad.bottom_left.y,
        ),
        (10.0, 170.0, 50.0, 170.0, 50.0, 190.0, 10.0, 190.0)
    );
    assert_eq!(
        (
            placement.bbox.x0,
            placement.bbox.y0,
            placement.bbox.x1,
            placement.bbox.y1,
        ),
        (10.0, 170.0, 50.0, 190.0)
    );
}

#[test]
fn malformed_optional_image_placement_warns_and_preserves_text_and_later_occurrence() {
    let layout = PdfDocument::parse(&recoverable_image_pdf())
        .unwrap()
        .extract_layout(LayoutExtractionOptions::default())
        .unwrap();
    assert_eq!(layout.text, "A");
    assert!(
        layout
            .warnings
            .iter()
            .any(|warning| warning.code == "image_placement_invalid")
    );
    let placements = &layout.pages[0].image_placements;
    assert_eq!(placements.len(), 1);
    assert_eq!(placements[0].id, "p0-i1");
    assert_eq!(placements[0].paint_ordinal, 1);
    assert_eq!(
        (
            placements[0].bbox.x0,
            placements[0].bbox.y0,
            placements[0].bbox.x1,
            placements[0].bbox.y1,
        ),
        (50.0, 40.0, 60.0, 50.0)
    );
}

#[test]
fn image_occurrence_limit_has_exact_and_one_short_boundaries() {
    let pdf = direct_image_pdf();
    let exact = ParseLimits {
        max_images: 2,
        ..ParseLimits::default()
    };
    assert_eq!(
        PdfDocument::parse_with_limits(&pdf, exact)
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap()
            .pages[0]
            .image_placements
            .len(),
        2
    );
    let short = ParseLimits {
        max_images: 1,
        ..ParseLimits::default()
    };
    assert_eq!(
        PdfDocument::parse_with_limits(&pdf, short)
            .unwrap()
            .extract_layout(LayoutExtractionOptions::default())
            .unwrap_err()
            .code,
        ErrorCode::LimitExceeded
    );
}

fn direct_image_pdf() -> Vec<u8> {
    let content = b"q 100 0 0 50 20 30 cm /Im Do Q q 0 20 -30 0 200 50 cm /Im Do Q";
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Rotate 90 /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        image_stream(),
    ])
}

fn form_image_pdf() -> Vec<u8> {
    let form_content = b"/Im Do";
    let mut form = format!(
        "<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Matrix [40 0 0 30 50 60] /Resources << /XObject << /Im 6 0 R >> >> /Length {} >>\nstream\n",
        form_content.len()
    )
    .into_bytes();
    form.extend_from_slice(form_content);
    form.extend_from_slice(b"\nendstream");
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /XObject << /Fm 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(b"/Fm Do"),
        form,
        image_stream(),
    ])
}

fn crop_user_unit_image_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /CropBox [10 20 210 120] /UserUnit 2 /Rotate 270 /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(b"q 20 0 0 10 15 25 cm /Im Do Q"),
        image_stream(),
    ])
}

fn recoverable_image_pdf() -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Font << /F1 5 0 R >> /XObject << /Im 6 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(b"q 0 0 0 0 0 0 cm /Im Do Q BT /F1 12 Tf 1 0 0 1 20 20 Tm (A) Tj ET q 10 0 0 10 50 50 cm /Im Do Q"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>".to_vec(),
        image_stream(),
    ])
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
