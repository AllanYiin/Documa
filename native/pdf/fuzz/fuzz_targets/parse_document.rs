#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    if let Ok(document) = pdf_core::PdfDocument::parse(data) {
        let _ = document.summary();
        for (&number, entry) in document.xref_entries().iter().take(16) {
            if entry.kind != pdf_core::XrefKind::Free {
                let _ = document.object(pdf_core::ObjectId::new(number, entry.generation));
            }
        }
        let _ = document.extract_text_v2(pdf_core::TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode: pdf_core::ExtractionMode::Auto,
            include_quality_metadata: true,
        });
    }
});
