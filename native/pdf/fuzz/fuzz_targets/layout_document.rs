#![no_main]
mod limits;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let limits = limits::bounded();
    if let Ok(document) = pdf_core::PdfDocument::parse_with_limits(data, limits.clone()) {
        let options = pdf_core::LayoutExtractionOptions::default();
        if let Ok(events) = document.extract_layout_events(options) {
            let _ = pdf_core::collect_layout_events(events, &limits);
        }
        if let Ok(mut events) = document.extract_layout_events(options) {
            let _ = events.next();
            let _ = events.next();
            // Exercise cancellation without finalizing a partially consumed producer.
            drop(events);
        }
        let _ = document.extract_layout_pages(options, &"1".parse().unwrap());
    }
});
