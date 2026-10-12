#![no_main]
use libfuzzer_sys::fuzz_target;
use office_core::ParseOptions;
use std::path::Path;
fuzz_target!(|data: &[u8]| {
    let _ = office_word::parse_docx(Path::new("fuzz.docx"), data, &ParseOptions::default());
});
