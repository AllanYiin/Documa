#![cfg(target_arch = "wasm32")]

use wasm_bindgen_test::wasm_bindgen_test;

#[wasm_bindgen_test]
fn version_reports_stage_11_release_candidate() {
    let value = pdf_wasm::version_info().expect("version info");
    let info: serde_json::Value =
        serde_wasm_bindgen::from_value(value).expect("deserialize version info");
    assert_eq!(info["version"], "0.2.0");
    assert_eq!(info["stage"], "stage-11");
}

#[wasm_bindgen_test]
fn invalid_input_returns_structured_error() {
    assert!(pdf_wasm::extract_text(b"not a PDF", None, None).is_err());
}
