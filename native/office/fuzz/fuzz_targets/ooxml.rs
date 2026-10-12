#![no_main]

use libfuzzer_sys::fuzz_target;
use office_core::ParseLimits;
use office_ooxml::OoxmlPackage;

fuzz_target!(|data: &[u8]| {
    let _ = OoxmlPackage::open(data, &ParseLimits::default());
});
