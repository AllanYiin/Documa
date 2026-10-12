use image::DynamicImage;
use pdf_core::{OcrImageQuad, OcrPixelBlock, PdfDocument};
use pdf_ocr::{
    ImageOcrEngine, OcrDocumentLimits, OcrError, OcrErrorCode, OcrOptions,
    ocr_document_with_limits, ocr_pages,
};
use std::cell::Cell;

struct FakeEngine {
    calls: Cell<usize>,
}
impl ImageOcrEngine for FakeEngine {
    fn recognize(&self, _: &DynamicImage, _: usize) -> Result<Vec<OcrPixelBlock>, OcrError> {
        self.calls.set(self.calls.get() + 1);
        Ok(vec![OcrPixelBlock {
            text: "A".to_owned(),
            confidence: 0.9,
            quad: OcrImageQuad {
                top_left: [0.0, 0.0],
                top_right: [1.0, 0.0],
                bottom_right: [1.0, 1.0],
                bottom_left: [0.0, 1.0],
            },
        }])
    }
}

fn document() -> PdfDocument {
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".as_slice(),
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 6 >>\nstream\n/Im Do\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\nx\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1 1] /Resources << /XObject << /Im 5 0 R >> >> /Contents 4 0 R >>",
    ];
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (i, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", i + 1).as_bytes());
        pdf.extend_from_slice(object);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let startxref = pdf.len();
    pdf.extend_from_slice(b"xref\n0 7\n0000000000 65535 f \n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    pdf.extend_from_slice(
        format!("trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n").as_bytes(),
    );
    PdfDocument::parse(&pdf).unwrap()
}

#[test]
fn iteration_is_lazy_and_cancellation_does_not_run_later_inference() {
    let engine = FakeEngine {
        calls: Cell::new(0),
    };
    let document = document();
    let mut pages = ocr_pages(
        &document,
        &engine,
        OcrOptions::default(),
        OcrDocumentLimits::default(),
    )
    .unwrap();
    assert_eq!(engine.calls.get(), 0);
    assert_eq!(pages.next().unwrap().unwrap().text, "A");
    assert_eq!(engine.calls.get(), 1);
    drop(pages);
    assert_eq!(engine.calls.get(), 1);
}

#[test]
fn document_text_blocks_payload_and_image_limits_have_exact_boundaries() {
    let engine = FakeEngine {
        calls: Cell::new(0),
    };
    let payload = 9 + 2
        * (std::mem::size_of::<pdf_ocr::OcrPageResult>()
            + std::mem::size_of::<pdf_core::OcrLayoutBlock>());
    let limits = OcrDocumentLimits {
        max_total_text_bytes: 3,
        max_total_blocks: 2,
        max_total_result_bytes: payload,
        max_decoded_image_bytes: 1,
    };
    let result =
        ocr_document_with_limits(&document(), &engine, OcrOptions::default(), limits).unwrap();
    assert_eq!(result.text, "A\nA");
    for limits in [
        OcrDocumentLimits {
            max_total_text_bytes: 2,
            ..limits
        },
        OcrDocumentLimits {
            max_total_blocks: 1,
            ..limits
        },
        OcrDocumentLimits {
            max_total_result_bytes: payload - 1,
            ..limits
        },
    ] {
        let mut pages = ocr_pages(&document(), &engine, OcrOptions::default(), limits).unwrap();
        assert!(pages.next().unwrap().is_ok());
        assert_eq!(
            pages.next().unwrap().unwrap_err().code,
            OcrErrorCode::LimitExceeded
        );
        assert!(pages.next().is_none());
    }
}

#[test]
fn parser_resource_limits_remain_ocr_limit_errors() {
    let original = document();
    let limited = PdfDocument::parse_with_limits(
        original.bytes(),
        pdf_core::ParseLimits {
            max_pages: 1,
            ..pdf_core::ParseLimits::default()
        },
    )
    .unwrap();
    let engine = FakeEngine {
        calls: Cell::new(0),
    };
    let error = ocr_pages(
        &limited,
        &engine,
        OcrOptions::default(),
        OcrDocumentLimits::default(),
    )
    .err()
    .expect("page limit");
    assert_eq!(error.code, OcrErrorCode::LimitExceeded);
    assert_eq!(engine.calls.get(), 0);
}
