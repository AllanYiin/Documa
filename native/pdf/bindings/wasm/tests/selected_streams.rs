#![cfg(target_arch = "wasm32")]
use serde::Serialize;
use wasm_bindgen::JsValue;
use wasm_bindgen_test::wasm_bindgen_test;

const SELECTED: &[u8] = include_bytes!("../../../tests/fixtures/priority/selected-pages.pdf");
const VERTICAL: &[u8] = include_bytes!("../../../tests/fixtures/priority/vertical-w2.pdf");
const FILTERS: &[u8] = include_bytes!("../../../tests/fixtures/priority/filters.pdf");
const OCR: &[u8] = include_bytes!("../../../tests/fixtures/priority/ocr-pages.pdf");

fn js(value: &serde_json::Value) -> JsValue {
    value
        .serialize(&serde_wasm_bindgen::Serializer::new().serialize_maps_as_objects(true))
        .unwrap()
}
fn json(value: JsValue) -> serde_json::Value {
    serde_wasm_bindgen::from_value(value).unwrap()
}

#[wasm_bindgen_test]
fn selected_pages_and_reusable_handles_preserve_source_identity() {
    let document = pdf_wasm::WasmDocument::new(SELECTED, JsValue::UNDEFINED).unwrap();
    let options = js(&serde_json::json!({"pages":"1,3"}));
    let value = json(document.extract_with_options(options).unwrap());
    assert_eq!(value["pages"][1]["page_index"], 2);
    assert_eq!(
        json(
            document
                .extract_layout(js(&serde_json::json!({"pages":"3"})))
                .unwrap()
        )["pages"][0]["page_number"],
        3
    );
    assert!(json(document.decode_metrics().unwrap())["decoded_bytes"].is_number());
    assert!(
        document
            .extract_with_options(js(&serde_json::json!({"limits":{}})))
            .is_err()
    );
}

#[wasm_bindgen_test]
fn lazy_events_finalize_cancel_and_surface_later_errors() {
    let document = pdf_wasm::WasmDocument::new(SELECTED, JsValue::UNDEFINED).unwrap();
    let mut events = document
        .extract_layout_events(js(&serde_json::json!({"pages":"1,3"})))
        .unwrap();
    drop(document);
    assert_eq!(
        json(events.next().unwrap())["value"]["event"],
        "document_start"
    );
    assert_eq!(
        json(events.next().unwrap())["value"]["payload"]["page_number"],
        1
    );
    assert_eq!(
        json(events.next().unwrap())["value"]["payload"]["page_number"],
        3
    );
    assert_eq!(
        json(events.next().unwrap())["value"]["event"],
        "document_finalize"
    );
    assert_eq!(json(events.next().unwrap())["done"], true);
    let mut cancelled =
        pdf_wasm::extract_layout_events(SELECTED, js(&serde_json::json!({"pages":"1-2"}))).unwrap();
    cancelled.next().unwrap();
    cancelled.next().unwrap();
    cancelled.close();
    assert_eq!(json(cancelled.next().unwrap())["done"], true);
    let mut failed =
        pdf_wasm::extract_layout_events(SELECTED, js(&serde_json::json!({"pages":"1-2"}))).unwrap();
    failed.next().unwrap();
    failed.next().unwrap();
    assert!(json(failed.next().unwrap_err())["code"].is_string());
    assert_eq!(json(failed.next().unwrap())["done"], true);
}

#[wasm_bindgen_test]
fn limits_filters_and_vertical_metrics_reach_wasm() {
    let error = pdf_wasm::extract_with_options(
        FILTERS,
        js(&serde_json::json!({"limits":{"max_file_bytes":1}})),
    )
    .unwrap_err();
    assert_eq!(json(error)["code"], "limit_exceeded");
    let value = json(pdf_wasm::extract_with_options(FILTERS, JsValue::UNDEFINED).unwrap());
    assert_eq!(value["text"], "Filtered text");
    let value = json(
        pdf_wasm::extract_with_options(VERTICAL, js(&serde_json::json!({"mode":"content-order"})))
            .unwrap(),
    );
    assert_eq!(value["text"], "一二");
    assert_eq!(value["glyphs"][0]["origin"][0].as_f64(), Some(47.0));
    assert_eq!(value["glyphs"][0]["origin"][1].as_f64(), Some(91.0));
    assert_eq!(value["glyphs"][0]["advance"][0].as_f64(), Some(0.0));
    assert_eq!(value["glyphs"][0]["advance"][1].as_f64(), Some(-12.0));
}

#[wasm_bindgen_test]
fn browser_ocr_candidates_are_lazy_and_cancellable() {
    let document = pdf_wasm::WasmDocument::new(OCR, JsValue::UNDEFINED).unwrap();
    let mut pages = document
        .direct_ocr_pages(js(&serde_json::json!({"pages":"2"})))
        .unwrap();
    let value = json(pages.next().unwrap());
    assert_eq!(value["value"]["page_index"], 1);
    assert_eq!(value["value"]["reason"], "eligible");
    pages.close();
    assert_eq!(json(pages.next().unwrap())["done"], true);
}
