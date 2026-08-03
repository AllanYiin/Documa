use pdf_core::{
    AffineMatrix, BBox, CoordinateSpace, ErrorCode, ExtractionMode, LAYOUT_SPACE, PageGeometry,
    PageGeometryWarning, PdfDocument, Point, Quad, TextExtractionOptionsV2,
};

const TOLERANCE: f64 = 1.0e-6;

#[test]
fn affine_inverse_round_trips_points_and_geometry() {
    let matrix = AffineMatrix::try_new(2.0, 0.5, -0.25, 3.0, 7.0, -9.0).expect("matrix");
    let inverse = matrix.inverse().expect("invertible");
    let point = Point::try_new(-14.25, 81.5).expect("point");
    assert_point(
        inverse.transform_point(matrix.transform_point(point)),
        point,
    );

    let bbox = BBox::try_new(10.0, 20.0, 30.0, 50.0).expect("bbox");
    let transformed = bbox.transformed(matrix).expect("bbox transform");
    assert!(transformed.width() > 0.0);
    assert!(transformed.height() > 0.0);

    let quad = Quad {
        top_left: Point { x: 10.0, y: 20.0 },
        top_right: Point { x: 30.0, y: 20.0 },
        bottom_right: Point { x: 30.0, y: 50.0 },
        bottom_left: Point { x: 10.0, y: 50.0 },
    };
    assert_eq!(quad.bounding_box().expect("quad bbox"), bbox);
    assert!(quad.transformed(matrix).bounding_box().is_ok());
}

#[test]
fn crop_box_user_unit_and_pdf_layout_round_trip_are_explicit() {
    let geometry = PageGeometry::new(
        [-10.0, -20.0, 210.0, 380.0],
        Some([10.0, 20.0, 110.0, 220.0]),
        2.0,
        0,
    )
    .expect("geometry");
    assert_eq!(geometry.coordinate_space, CoordinateSpace::LayoutSpace);
    assert_eq!(geometry.coordinate_space.as_str(), LAYOUT_SPACE);
    let serialized = serde_json::to_value(&geometry).expect("serialize geometry");
    assert_eq!(serialized["coordinate_space"], LAYOUT_SPACE);
    assert_close(geometry.layout_bounds.width(), 200.0);
    assert_close(geometry.layout_bounds.height(), 400.0);
    assert_point(
        geometry.pdf_point_to_layout(Point { x: 10.0, y: 220.0 }),
        Point { x: 0.0, y: 0.0 },
    );
    assert_point(
        geometry.pdf_point_to_layout(Point { x: 110.0, y: 20.0 }),
        Point { x: 200.0, y: 400.0 },
    );
    let source = Point { x: 42.5, y: 97.25 };
    assert_point(
        geometry.layout_point_to_pdf(geometry.pdf_point_to_layout(source)),
        source,
    );
}

#[test]
fn all_quarter_turn_display_transforms_are_reversible() {
    let corners = [
        Point { x: 0.0, y: 0.0 },
        Point { x: 100.0, y: 0.0 },
        Point { x: 100.0, y: 200.0 },
        Point { x: 0.0, y: 200.0 },
    ];
    for rotation in [0, 90, 180, 270, -90, 450] {
        let geometry = PageGeometry::new([0.0, 0.0, 100.0, 200.0], None, 1.0, rotation)
            .expect("quarter-turn geometry");
        for point in corners {
            let display = geometry.layout_point_to_display(point);
            assert_point(geometry.display_point_to_layout(display), point);
            assert!(display.x >= -TOLERANCE);
            assert!(display.y >= -TOLERANCE);
            assert!(display.x <= geometry.display_bounds.x1 + TOLERANCE);
            assert!(display.y <= geometry.display_bounds.y1 + TOLERANCE);
        }
        let layout_box = BBox::try_new(10.0, 20.0, 70.0, 120.0).expect("layout bbox");
        let display_box = layout_box
            .transformed(geometry.layout_to_display)
            .expect("display bbox");
        let round_trip_box = display_box
            .transformed(geometry.display_to_layout)
            .expect("layout bbox");
        assert_bbox(round_trip_box, layout_box);

        let layout_quad = Quad {
            top_left: Point { x: 10.0, y: 20.0 },
            top_right: Point { x: 70.0, y: 20.0 },
            bottom_right: Point { x: 70.0, y: 120.0 },
            bottom_left: Point { x: 10.0, y: 120.0 },
        };
        let round_trip_quad = layout_quad
            .transformed(geometry.layout_to_display)
            .transformed(geometry.display_to_layout);
        assert_quad(round_trip_quad, layout_quad);

        if matches!(geometry.rotation, 90 | 270) {
            assert_close(geometry.display_bounds.width(), 200.0);
            assert_close(geometry.display_bounds.height(), 100.0);
        } else {
            assert_close(geometry.display_bounds.width(), 100.0);
            assert_close(geometry.display_bounds.height(), 200.0);
        }
    }
}

#[test]
fn reordered_boxes_warn_and_invalid_geometry_has_stable_code() {
    let geometry = PageGeometry::new(
        [210.0, 380.0, -10.0, -20.0],
        Some([110.0, 220.0, 10.0, 20.0]),
        1.0,
        0,
    )
    .expect("reordered geometry");
    assert_eq!(
        geometry.warnings,
        vec![
            PageGeometryWarning::MediaBoxReordered,
            PageGeometryWarning::CropBoxReordered,
        ]
    );
    assert!(
        geometry
            .warnings
            .iter()
            .all(|warning| warning.code() == "page_box_reordered")
    );

    for result in [
        PageGeometry::new([0.0, 0.0, 0.0, 10.0], None, 1.0, 0),
        PageGeometry::new([0.0, 0.0, 10.0, 10.0], None, 0.0, 0),
        PageGeometry::new([0.0, 0.0, 10.0, 10.0], None, 1.0, 45),
        PageGeometry::new([0.0, 0.0, f64::INFINITY, 10.0], None, 1.0, 0),
    ] {
        assert_eq!(
            result.expect_err("invalid geometry").code,
            ErrorCode::InvalidPageGeometry
        );
    }
    assert_eq!(
        ErrorCode::InvalidPageGeometry.as_str(),
        "invalid_page_geometry"
    );
}

#[test]
fn page_tree_materializes_inherited_boxes_rotation_and_page_user_unit() {
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 \
          /MediaBox 4 0 R /CropBox 5 0 R /Rotate 6 0 R >>"
            .to_vec(),
        b"<< /Type /Page /Parent 2 0 R /UserUnit 7 0 R /Resources << >> >>".to_vec(),
        b"[210 380 -10 -20]".to_vec(),
        b"[10 20 110 220]".to_vec(),
        b"-90".to_vec(),
        b"2".to_vec(),
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("valid page tree");
    let pages = document.pages().expect("materialized pages");
    assert_eq!(pages.len(), 1);
    let page = &pages[0];
    assert_eq!(page.rotate, 270);
    assert_close(page.user_unit, 2.0);
    assert_eq!(page.media_box, Some([210.0, 380.0, -10.0, -20.0]));
    assert_eq!(page.crop_box, Some([10.0, 20.0, 110.0, 220.0]));
    assert_close(page.geometry.layout_bounds.width(), 200.0);
    assert_close(page.geometry.layout_bounds.height(), 400.0);
    assert_close(page.geometry.display_bounds.width(), 400.0);
    assert_close(page.geometry.display_bounds.height(), 200.0);
}

#[test]
fn missing_media_box_is_rejected_before_layout() {
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R >>".to_vec(),
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("structural PDF");
    let error = document.pages().expect_err("MediaBox is mandatory");
    assert_eq!(error.code, ErrorCode::InvalidPageGeometry);
}

#[test]
fn form_placements_share_one_page_geometry_conversion() {
    let form_content = b"BT /F1 10 Tf 1 0 0 1 10 20 Tm (A) Tj ET".to_vec();
    let form = stream_with_dictionary(
        b"/Type /XObject /Subtype /Form /Matrix [2 0 0 2 5 7] \
          /Resources << /Font << /F1 6 0 R >> >>",
        &form_content,
    );
    let page_content = b"q 1 0 0 1 10 0 cm /Fm1 Do Q q 1 0 0 1 30 0 cm /Fm1 Do Q".to_vec();
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [-10 -20 210 380] \
          /CropBox [10 20 110 220] /UserUnit 2 \
          /Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(&page_content),
        form,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 65 /Widths [600] >>"
            .to_vec(),
    ];
    let document = PdfDocument::parse(&classic_pdf(&objects)).expect("valid Form PDF");
    let result = document
        .extract_text_v2(TextExtractionOptionsV2 {
            mode: ExtractionMode::ContentOrder,
            ..TextExtractionOptionsV2::default()
        })
        .expect("glyph geometry");
    let page = document.pages().expect("page").remove(0);
    assert_eq!(result.glyphs.len(), 2);
    let mapped = result
        .glyphs
        .iter()
        .map(|glyph| {
            page.geometry.pdf_point_to_layout(Point {
                x: glyph.origin[0],
                y: glyph.origin[1],
            })
        })
        .collect::<Vec<_>>();
    assert_point(mapped[0], Point { x: 70.0, y: 346.0 });
    assert_point(mapped[1], Point { x: 150.0, y: 346.0 });
}

fn assert_close(actual: f64, expected: f64) {
    assert!(
        (actual - expected).abs() <= TOLERANCE,
        "{actual} != {expected}"
    );
}

fn assert_point(actual: Point, expected: Point) {
    assert_close(actual.x, expected.x);
    assert_close(actual.y, expected.y);
}

fn assert_bbox(actual: BBox, expected: BBox) {
    assert_close(actual.x0, expected.x0);
    assert_close(actual.y0, expected.y0);
    assert_close(actual.x1, expected.x1);
    assert_close(actual.y1, expected.y1);
}

fn assert_quad(actual: Quad, expected: Quad) {
    assert_point(actual.top_left, expected.top_left);
    assert_point(actual.top_right, expected.top_right);
    assert_point(actual.bottom_right, expected.bottom_right);
    assert_point(actual.bottom_left, expected.bottom_left);
}

fn stream_body(data: &[u8]) -> Vec<u8> {
    stream_with_dictionary(b"", data)
}

fn stream_with_dictionary(dictionary: &[u8], data: &[u8]) -> Vec<u8> {
    let mut body = format!(
        "<< {} /Length {} >>\nstream\n",
        String::from_utf8_lossy(dictionary),
        data.len()
    )
    .into_bytes();
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
