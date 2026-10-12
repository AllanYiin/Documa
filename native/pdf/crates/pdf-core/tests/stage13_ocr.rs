use pdf_core::{DirectOcrOptions, DirectOcrReason, OcrImageQuad, OcrPixelBlock, PdfDocument};

#[test]
fn selects_one_full_page_raw_image_without_rasterization() {
    let document = PdfDocument::parse(&image_pdf(b"q 100 0 0 100 0 0 cm /Im Do Q")).expect("parse");
    let analysis = document
        .analyze_direct_ocr(DirectOcrOptions::default())
        .expect("analysis");
    let page = &analysis.pages[0];
    assert_eq!(page.reason, DirectOcrReason::Eligible);
    let candidate = page.candidate.as_ref().expect("candidate");
    assert_eq!(candidate.image.data, vec![127]);
    assert!((candidate.page_coverage - 1.0).abs() < f64::EPSILON);

    let mapped = candidate
        .map_block(&OcrPixelBlock {
            text: "hello".to_owned(),
            confidence: 0.8,
            quad: OcrImageQuad {
                top_left: [0.0, 0.0],
                top_right: [1.0, 0.0],
                bottom_right: [1.0, 1.0],
                bottom_left: [0.0, 1.0],
            },
        })
        .expect("map");
    assert_eq!(mapped.bbox, candidate.image_quad.bounding_box().unwrap());
}

#[test]
fn rejects_partial_and_multiple_painted_images() {
    let partial = PdfDocument::parse(&image_pdf(b"q 80 0 0 80 0 0 cm /Im Do Q"))
        .unwrap()
        .analyze_direct_ocr(DirectOcrOptions::default())
        .unwrap();
    assert_eq!(
        partial.pages[0].reason,
        DirectOcrReason::ImageDoesNotCoverPage
    );

    let multiple = PdfDocument::parse(&image_pdf(
        b"q 100 0 0 100 0 0 cm /Im Do Q q 100 0 0 100 0 0 cm /Im Do Q",
    ))
    .unwrap()
    .analyze_direct_ocr(DirectOcrOptions::default())
    .unwrap();
    assert_eq!(
        multiple.pages[0].reason,
        DirectOcrReason::MultiplePaintedImages
    );
}

#[test]
fn coverage_counts_only_the_image_area_inside_the_page() {
    for content in [
        b"q 100 0 0 100 200 0 cm /Im Do Q".as_slice(),
        b"q 100 0 0 100 50 0 cm /Im Do Q",
        // A diamond with a full-page bounding box, but only half the page area.
        b"q 50 50 -50 50 50 0 cm /Im Do Q",
        // Large skewed image: area exceeds the page but intersection does not.
        b"q 200 200 -200 200 50 -350 cm /Im Do Q",
    ] {
        let analysis = PdfDocument::parse(&image_pdf(content))
            .unwrap()
            .analyze_direct_ocr(DirectOcrOptions::default())
            .unwrap();
        assert_eq!(
            analysis.pages[0].reason,
            DirectOcrReason::ImageDoesNotCoverPage
        );
    }

    let analysis = PdfDocument::parse(&image_pdf(b"q 100 0 0 100 10 0 cm /Im Do Q"))
        .unwrap()
        .analyze_direct_ocr(DirectOcrOptions::default())
        .unwrap();
    let candidate = analysis.pages[0].candidate.as_ref().unwrap();
    assert!((candidate.page_coverage - 0.9).abs() < 1e-12);
}

fn image_pdf(content: &[u8]) -> Vec<u8> {
    classic_pdf(&[
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>".to_vec(),
        stream_body(content),
        image_stream(),
    ])
}

fn image_stream() -> Vec<u8> {
    let mut body = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n".to_vec();
    body.push(127);
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
