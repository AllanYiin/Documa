use wasm_bindgen::prelude::*;

/// Return parser package and stage information as a JavaScript value.
///
/// # Errors
///
/// Returns a JavaScript error if serialization fails.
#[wasm_bindgen(js_name = versionInfo)]
pub fn version_info() -> Result<JsValue, JsValue> {
    serde_wasm_bindgen::to_value(&pdf_core::version_info()).map_err(serialization_error)
}

/// Inspect PDF header and cross-reference metadata.
///
/// # Errors
///
/// Returns a structured JavaScript error for invalid PDF bytes or serialization failures.
#[wasm_bindgen(js_name = inspect)]
pub fn inspect_pdf(data: &[u8]) -> Result<JsValue, JsValue> {
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    let summary = document.summary().map_err(pdf_error)?;
    serde_wasm_bindgen::to_value(&summary).map_err(serialization_error)
}

/// Extract plain Unicode text without rendering.
///
/// # Errors
///
/// Returns a structured JavaScript error for malformed or unsupported input.
#[wasm_bindgen(js_name = extractText)]
pub fn extract_text(
    data: &[u8],
    normalize_unicode: Option<bool>,
    layout: Option<bool>,
) -> Result<String, JsValue> {
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    document
        .extract_text(pdf_core::TextExtractionOptions {
            normalize_unicode: normalize_unicode.unwrap_or(false),
            layout: layout.unwrap_or(true),
        })
        .map(|result| result.text)
        .map_err(pdf_error)
}

/// Extract structured text, pages, spans, and warnings.
///
/// # Errors
///
/// Returns a structured JavaScript error for malformed input or serialization failures.
#[wasm_bindgen(js_name = extract)]
pub fn extract_structured(
    data: &[u8],
    normalize_unicode: Option<bool>,
    layout: Option<bool>,
) -> Result<JsValue, JsValue> {
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    let result = document
        .extract_text(pdf_core::TextExtractionOptions {
            normalize_unicode: normalize_unicode.unwrap_or(false),
            layout: layout.unwrap_or(true),
        })
        .map_err(pdf_error)?;
    serde_wasm_bindgen::to_value(&result).map_err(serialization_error)
}

#[derive(Debug, serde::Deserialize)]
#[serde(default, rename_all = "camelCase")]
struct ExtractionOptions {
    normalize_unicode: bool,
    mode: String,
    quality: bool,
}

impl Default for ExtractionOptions {
    fn default() -> Self {
        Self {
            normalize_unicode: false,
            mode: "auto".to_owned(),
            quality: true,
        }
    }
}

/// Extract a V2 structured result using a JavaScript options object.
///
/// # Errors
///
/// Returns a structured JavaScript error for invalid options, malformed input, or serialization.
#[wasm_bindgen(js_name = extractWithOptions)]
pub fn extract_with_options(data: &[u8], options: JsValue) -> Result<JsValue, JsValue> {
    let options = if options.is_null() || options.is_undefined() {
        ExtractionOptions::default()
    } else {
        serde_wasm_bindgen::from_value(options).map_err(option_deserialization_error)?
    };
    let mode = parse_extraction_mode(&options.mode).map_err(pdf_error)?;
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    let result = document
        .extract_text_v2(pdf_core::TextExtractionOptionsV2 {
            normalize_unicode: options.normalize_unicode,
            mode,
            include_quality_metadata: options.quality,
        })
        .map_err(pdf_error)?;
    serde_wasm_bindgen::to_value(&result).map_err(serialization_error)
}

#[derive(Debug, serde::Deserialize)]
#[serde(default, rename_all = "camelCase")]
#[allow(clippy::struct_excessive_bools)] // Mirrors JavaScript option fields.
struct LayoutOptions {
    normalize_unicode: bool,
    quality: bool,
    debug_glyphs: bool,
    timings: bool,
}

impl Default for LayoutOptions {
    fn default() -> Self {
        Self {
            normalize_unicode: false,
            quality: true,
            debug_glyphs: false,
            timings: false,
        }
    }
}

/// Extract versioned coordinate-normalized Layout IR.
///
/// # Errors
///
/// Returns a structured JavaScript error for invalid options, malformed input, or serialization.
#[wasm_bindgen(js_name = extractLayout)]
pub fn extract_layout(data: &[u8], options: JsValue) -> Result<JsValue, JsValue> {
    let options = if options.is_null() || options.is_undefined() {
        LayoutOptions::default()
    } else {
        serde_wasm_bindgen::from_value(options).map_err(option_deserialization_error)?
    };
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    let result = document
        .extract_layout(pdf_core::LayoutExtractionOptions {
            normalize_unicode: options.normalize_unicode,
            include_quality_metadata: options.quality,
            include_debug_glyphs: options.debug_glyphs,
            include_timings: options.timings,
        })
        .map_err(pdf_error)?;
    serde_wasm_bindgen::to_value(&result).map_err(serialization_error)
}
fn parse_extraction_mode(value: &str) -> pdf_core::PdfResult<pdf_core::ExtractionMode> {
    match value {
        "content-order" => Ok(pdf_core::ExtractionMode::ContentOrder),
        "layout" => Ok(pdf_core::ExtractionMode::Layout),
        "auto" => Ok(pdf_core::ExtractionMode::Auto),
        _ => Err(pdf_core::PdfError::new(
            pdf_core::ErrorCode::InvalidOption,
            None,
            format!(
                "unsupported extraction mode {value:?}; expected content-order, layout, or auto"
            ),
        )),
    }
}

/// Extract image `XObjects` without rendering pages.
///
/// # Errors
///
/// Returns a structured JavaScript error for malformed input or serialization failures.
#[wasm_bindgen(js_name = extractImages)]
pub fn extract_images(data: &[u8]) -> Result<JsValue, JsValue> {
    let document = pdf_core::PdfDocument::parse(data).map_err(pdf_error)?;
    let images = document.extract_images().map_err(pdf_error)?;
    serde_wasm_bindgen::to_value(&images).map_err(serialization_error)
}

#[allow(clippy::needless_pass_by_value)] // map_err supplies owned option errors.
fn option_deserialization_error(error: serde_wasm_bindgen::Error) -> JsValue {
    pdf_error(pdf_core::PdfError::new(
        pdf_core::ErrorCode::InvalidOption,
        None,
        format!("invalid extraction options: {error}"),
    ))
}

#[allow(clippy::needless_pass_by_value)] // map_err supplies owned parser errors.
fn pdf_error(error: pdf_core::PdfError) -> JsValue {
    serde_wasm_bindgen::to_value(&serde_json::json!({
        "code": error.code.as_str(),
        "offset": error.offset,
        "message": error.message,
    }))
    .unwrap_or_else(|_| JsValue::from_str(error.code.as_str()))
}

#[allow(clippy::needless_pass_by_value)] // map_err supplies owned serializer errors.
fn serialization_error(error: serde_wasm_bindgen::Error) -> JsValue {
    JsValue::from_str(&format!("serialization_error: {error}"))
}
