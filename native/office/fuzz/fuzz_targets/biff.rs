#![no_main]

use std::path::Path;

use libfuzzer_sys::fuzz_target;
use office_core::{OfficeFormat, ParseOptions};

fuzz_target!(|data: &[u8]| {
    let _ = office_sheet::parse_spreadsheet(
        Path::new("fuzz.xls"),
        data,
        OfficeFormat::Xls,
        &ParseOptions::default(),
    );
});
