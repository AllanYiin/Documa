#![no_main]
mod limits;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if let Ok(document) = pdf_core::PdfDocument::parse_with_limits(data, limits::bounded()) {
        let _ = document.extract_images();
        if let Ok(pages) = document.direct_ocr_pages(pdf_core::DirectOcrOptions::default()) {
            for page in pages {
                let Ok(page) = page else { break };
                if let Some(candidate) = page.candidate {
                    let coordinate = data.get(..4).map_or(0.0, |bytes| {
                        f32::from_bits(u32::from_le_bytes(bytes.try_into().unwrap()))
                    });
                    let _ = candidate.map_block(&pdf_core::OcrPixelBlock {
                        text: "fuzz".to_owned(),
                        confidence: 0.5,
                        quad: pdf_core::OcrImageQuad {
                            top_left: [coordinate, 0.0],
                            top_right: [1.0, 0.0],
                            bottom_right: [1.0, 1.0],
                            bottom_left: [0.0, 1.0],
                        },
                    });
                }
            }
        }
    }
});
