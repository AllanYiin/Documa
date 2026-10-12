use super::{
    ExtractionOptions, LayoutOptions, option_deserialization_error, parse_extraction_mode,
    pdf_error, serialization_error,
};
use wasm_bindgen::prelude::*;

/// Reusable native PDF handle. Call the generated `free()` method to release it.
#[wasm_bindgen(js_name = PdfDocument)]
pub struct WasmDocument {
    inner: pdf_core::PdfDocument,
}

#[wasm_bindgen(js_class = PdfDocument)]
impl WasmDocument {
    /// Parse with an optional partial `ParseLimits` object (`snake_case` fields).
    ///
    /// # Errors
    /// Returns a structured parser or invalid-option error.
    #[wasm_bindgen(constructor)]
    pub fn new(data: &[u8], limits: JsValue) -> Result<Self, JsValue> {
        let limits = parse_options::<pdf_core::ParseLimits>(limits)?;
        pdf_core::PdfDocument::parse_with_limits(data, limits)
            .map(|inner| Self { inner })
            .map_err(pdf_error)
    }

    /// Extract V2 text from optional selected pages. Limits belong on the constructor.
    ///
    /// # Errors
    /// Returns a structured option, parser or serialization error.
    #[wasm_bindgen(js_name = extractWithOptions)]
    pub fn extract_with_options(&self, options: JsValue) -> Result<JsValue, JsValue> {
        let options = parse_options::<ExtractionOptions>(options)?;
        reject_limits(options.limits.is_some())?;
        let result = self
            .inner
            .extract_text_v2_pages(
                pdf_core::TextExtractionOptionsV2 {
                    normalize_unicode: options.normalize_unicode,
                    mode: parse_extraction_mode(&options.mode).map_err(pdf_error)?,
                    include_quality_metadata: options.quality,
                },
                &selection(options.pages.as_deref())?,
            )
            .map_err(pdf_error)?;
        serde_wasm_bindgen::to_value(&result).map_err(serialization_error)
    }

    /// Extract Layout IR for optional source page ranges.
    ///
    /// # Errors
    /// Returns a structured option, parser or serialization error.
    #[wasm_bindgen(js_name = extractLayout)]
    pub fn extract_layout(&self, options: JsValue) -> Result<JsValue, JsValue> {
        let options = parse_options::<LayoutOptions>(options)?;
        reject_limits(options.limits.is_some())?;
        let result = self
            .inner
            .extract_layout_pages(
                layout_options(&options),
                &selection(options.pages.as_deref())?,
            )
            .map_err(pdf_error)?;
        serde_wasm_bindgen::to_value(&result).map_err(serialization_error)
    }

    /// Create an event iterator. `next()` returns `{done, value}`; `close()` cancels.
    ///
    /// # Errors
    /// Returns index/option errors now; page errors are thrown by `next()`.
    #[wasm_bindgen(js_name = extractLayoutEvents)]
    pub fn extract_layout_events(&self, options: JsValue) -> Result<LayoutEventIterator, JsValue> {
        let options = parse_options::<LayoutOptions>(options)?;
        reject_limits(options.limits.is_some())?;
        layout_events(&self.inner, &options)
    }

    /// Return lazy direct-image OCR candidates for browser-side inference.
    ///
    /// # Errors
    /// Returns structured options or parser errors; page errors are thrown by `next()`.
    #[wasm_bindgen(js_name = directOcrPages)]
    pub fn direct_ocr_pages(&self, options: JsValue) -> Result<OcrCandidateIterator, JsValue> {
        let options = parse_options::<OcrOptions>(options)?;
        let pages = self
            .inner
            .direct_ocr_pages_selected(
                pdf_core::DirectOcrOptions {
                    min_page_coverage: options.min_page_coverage,
                    ..pdf_core::DirectOcrOptions::default()
                },
                &selection(options.pages.as_deref())?,
            )
            .map_err(pdf_error)?;
        Ok(OcrCandidateIterator { pages: Some(pages) })
    }

    /// Inspect header/xref metadata without reparsing.
    ///
    /// # Errors
    /// Returns a structured parser or serialization error.
    pub fn inspect(&self) -> Result<JsValue, JsValue> {
        serde_wasm_bindgen::to_value(&self.inner.summary().map_err(pdf_error)?)
            .map_err(serialization_error)
    }

    /// Read the handle's shared, monotonic decode/cache counters.
    ///
    /// # Errors
    /// Returns a serialization error.
    #[wasm_bindgen(js_name = decodeMetrics)]
    pub fn decode_metrics(&self) -> Result<JsValue, JsValue> {
        serde_wasm_bindgen::to_value(&self.inner.decode_metrics()).map_err(serialization_error)
    }
}

/// Create selected-page native events directly from bytes and options.
///
/// # Errors
/// Returns a structured parser, option or limit error.
#[wasm_bindgen(js_name = extractLayoutEvents)]
pub fn extract_layout_events(
    data: &[u8],
    options: JsValue,
) -> Result<LayoutEventIterator, JsValue> {
    let mut options = parse_options::<LayoutOptions>(options)?;
    let document =
        pdf_core::PdfDocument::parse_with_limits(data, options.limits.take().unwrap_or_default())
            .map_err(pdf_error)?;
    layout_events(&document, &options)
}

/// Fallible native Layout event iterator; no complete document queue is retained.
#[wasm_bindgen]
pub struct LayoutEventIterator {
    events: Option<pdf_core::LayoutEventProducer>,
}

#[wasm_bindgen]
impl LayoutEventIterator {
    /// Produce the next event, or `{done: true}`. An error ends the iterator.
    ///
    /// # Errors
    /// Returns a structured page parser, limit or serialization error.
    #[allow(clippy::should_implement_trait)] // JavaScript iterator protocol.
    pub fn next(&mut self) -> Result<JsValue, JsValue> {
        let value = self.events.as_mut().and_then(Iterator::next);
        if value.is_none() || value.as_ref().is_some_and(Result::is_err) {
            self.events = None;
        }
        iteration_result(value.transpose().map_err(pdf_error)?)
    }

    /// Cancel remaining page work and release native state.
    pub fn close(&mut self) {
        self.events = None;
    }
}

/// Fallible OCR candidate iterator, independent of an ONNX runtime.
#[wasm_bindgen]
pub struct OcrCandidateIterator {
    pages: Option<pdf_core::DirectOcrPageProducer>,
}

#[wasm_bindgen]
impl OcrCandidateIterator {
    /// Produce one candidate/rejection or `{done: true}`.
    ///
    /// # Errors
    /// Returns a structured page, image, limit or serialization error.
    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> Result<JsValue, JsValue> {
        let value = self.pages.as_mut().and_then(Iterator::next);
        if value.is_none() || value.as_ref().is_some_and(Result::is_err) {
            self.pages = None;
        }
        iteration_result(value.transpose().map_err(pdf_error)?)
    }

    /// Cancel remaining candidate analysis and release native state.
    pub fn close(&mut self) {
        self.pages = None;
    }
}

#[derive(serde::Serialize)]
struct IterationResult<T> {
    done: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    value: Option<T>,
}

fn iteration_result<T: serde::Serialize>(value: Option<T>) -> Result<JsValue, JsValue> {
    serde_wasm_bindgen::to_value(&IterationResult {
        done: value.is_none(),
        value,
    })
    .map_err(serialization_error)
}

fn layout_events(
    document: &pdf_core::PdfDocument,
    options: &LayoutOptions,
) -> Result<LayoutEventIterator, JsValue> {
    let events = document
        .extract_layout_events_pages(
            layout_options(options),
            &selection(options.pages.as_deref())?,
        )
        .map_err(pdf_error)?;
    Ok(LayoutEventIterator {
        events: Some(events),
    })
}

fn layout_options(options: &LayoutOptions) -> pdf_core::LayoutExtractionOptions {
    pdf_core::LayoutExtractionOptions {
        normalize_unicode: options.normalize_unicode,
        include_quality_metadata: options.quality,
        include_debug_glyphs: options.debug_glyphs,
        include_timings: options.timings,
    }
}

pub(super) fn selection(pages: Option<&str>) -> Result<pdf_core::PageSelection, JsValue> {
    pages.map_or_else(
        || Ok(pdf_core::PageSelection::all()),
        |value| value.parse().map_err(pdf_error),
    )
}

fn parse_options<T: serde::de::DeserializeOwned + Default>(value: JsValue) -> Result<T, JsValue> {
    if value.is_null() || value.is_undefined() {
        Ok(T::default())
    } else {
        serde_wasm_bindgen::from_value(value).map_err(option_deserialization_error)
    }
}

fn reject_limits(present: bool) -> Result<(), JsValue> {
    if present {
        Err(pdf_error(pdf_core::PdfError::new(
            pdf_core::ErrorCode::InvalidOption,
            None,
            "set parser limits on the PdfDocument constructor",
        )))
    } else {
        Ok(())
    }
}

#[derive(serde::Deserialize)]
#[serde(default, deny_unknown_fields, rename_all = "camelCase")]
struct OcrOptions {
    min_page_coverage: f64,
    pages: Option<String>,
}
impl Default for OcrOptions {
    fn default() -> Self {
        Self {
            min_page_coverage: 0.9,
            pages: None,
        }
    }
}
