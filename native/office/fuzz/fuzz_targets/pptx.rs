#![no_main]

use std::path::Path;

use libfuzzer_sys::fuzz_target;
use office_core::ParseOptions;

fuzz_target!(|data: &[u8]| {
    let _ = office_slide::parse_pptx(Path::new("fuzz.pptx"), data, &ParseOptions::default());
});
