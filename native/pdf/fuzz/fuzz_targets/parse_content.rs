#![no_main]

use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = pdf_core::parse_content(data, &pdf_core::ParseLimits::default());
});
