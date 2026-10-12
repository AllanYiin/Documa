#![no_main]
use libfuzzer_sys::fuzz_target;
use office_core::{OfficeFormat, ParseOptions};
use std::path::Path;
fuzz_target!(|data: &[u8]| {
    let _ = office_sheet::parse_spreadsheet(
        Path::new("fuzz.xlsx"),
        data,
        OfficeFormat::Xlsx,
        &ParseOptions {
            sparse: true,
            ..ParseOptions::default()
        },
    );
});
