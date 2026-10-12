use super::{
    LayoutJsonStream, initial_stream_metadata, parse_extraction_mode, serialization_error,
    to_python_error,
};
use pdf_core::{
    DirectOcrOptions, LayoutExtractionOptions, PageSelection, ParseLimits, PdfDocument,
    TextExtractionOptions, TextExtractionOptionsV2,
};
use pyo3::prelude::*;

/// Reusable parser state; clones used by streams share the monotonic decode budget.
#[pyclass(name = "Document", frozen)]
pub(super) struct NativeDocument {
    inner: PdfDocument,
}

#[pymethods]
impl NativeDocument {
    #[new]
    #[pyo3(signature = (data, limits_json=None))]
    fn new(py: Python<'_>, data: &[u8], limits_json: Option<&str>) -> PyResult<Self> {
        py.detach(|| {
            let limits = match limits_json {
                None => ParseLimits::default(),
                Some(value) => {
                    if value.len() > 65_536 {
                        return Err(invalid_option("limits JSON exceeds 65536 bytes"));
                    }
                    serde_json::from_str(value)
                        .map_err(|error| invalid_option(&error.to_string()))?
                }
            };
            PdfDocument::parse_with_limits(data, limits)
                .map(|inner| Self { inner })
                .map_err(to_python_error)
        })
    }

    #[pyo3(signature = (normalize_unicode=false, layout=true, pages=None))]
    fn extract_text(
        &self,
        py: Python<'_>,
        normalize_unicode: bool,
        layout: bool,
        pages: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            self.inner
                .extract_text_pages(
                    TextExtractionOptions {
                        normalize_unicode,
                        layout,
                    },
                    &selection(pages)?,
                )
                .map(|result| result.text)
                .map_err(to_python_error)
        })
    }

    #[pyo3(signature = (normalize_unicode=false, layout=true, pages=None))]
    fn extract_json(
        &self,
        py: Python<'_>,
        normalize_unicode: bool,
        layout: bool,
        pages: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let result = self
                .inner
                .extract_text_pages(
                    TextExtractionOptions {
                        normalize_unicode,
                        layout,
                    },
                    &selection(pages)?,
                )
                .map_err(to_python_error)?;
            serde_json::to_string(&result).map_err(serialization_error)
        })
    }

    #[pyo3(signature = (mode="auto", normalize_unicode=false, quality=true, pages=None))]
    fn extract_v2_json(
        &self,
        py: Python<'_>,
        mode: &str,
        normalize_unicode: bool,
        quality: bool,
        pages: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let options = TextExtractionOptionsV2 {
                mode: parse_extraction_mode(mode).map_err(to_python_error)?,
                normalize_unicode,
                include_quality_metadata: quality,
            };
            let result = self
                .inner
                .extract_text_v2_pages(options, &selection(pages)?)
                .map_err(to_python_error)?;
            serde_json::to_string(&result).map_err(serialization_error)
        })
    }

    #[pyo3(signature = (normalize_unicode=false, quality=true, debug_glyphs=false, timings=false, pages=None))]
    #[allow(clippy::fn_params_excessive_bools)]
    fn extract_layout_json(
        &self,
        py: Python<'_>,
        normalize_unicode: bool,
        quality: bool,
        debug_glyphs: bool,
        timings: bool,
        pages: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let result = self
                .inner
                .extract_layout_pages(
                    layout_options(normalize_unicode, quality, debug_glyphs, timings),
                    &selection(pages)?,
                )
                .map_err(to_python_error)?;
            serde_json::to_string(&result).map_err(serialization_error)
        })
    }

    #[pyo3(signature = (normalize_unicode=false, quality=true, debug_glyphs=false, timings=false, pages=None))]
    #[allow(clippy::fn_params_excessive_bools)]
    fn extract_layout_stream(
        &self,
        py: Python<'_>,
        normalize_unicode: bool,
        quality: bool,
        debug_glyphs: bool,
        timings: bool,
        pages: Option<&str>,
    ) -> PyResult<LayoutJsonStream> {
        py.detach(|| {
            let mut events = self
                .inner
                .extract_layout_events_pages(
                    layout_options(normalize_unicode, quality, debug_glyphs, timings),
                    &selection(pages)?,
                )
                .map_err(to_python_error)?;
            let Some(Ok(pdf_core::LayoutEvent::DocumentStart(start))) = events.next() else {
                return Err(super::stream_protocol_error(
                    "native Layout stream has no document_start",
                ));
            };
            Ok(LayoutJsonStream {
                metadata_json: initial_stream_metadata(&start)?,
                start,
                events: Some(events),
                finalizations: std::collections::VecDeque::new(),
                finalized: false,
            })
        })
    }

    #[pyo3(signature = (pages=None))]
    fn extract_images_json(&self, py: Python<'_>, pages: Option<&str>) -> PyResult<String> {
        py.detach(|| {
            let images = self
                .inner
                .extract_images_pages(&selection(pages)?)
                .map_err(to_python_error)?;
            serde_json::to_string(&images).map_err(serialization_error)
        })
    }

    #[pyo3(signature = (min_page_coverage=0.9, pages=None))]
    fn direct_ocr_pages(
        &self,
        py: Python<'_>,
        min_page_coverage: f64,
        pages: Option<&str>,
    ) -> PyResult<OcrCandidateStream> {
        py.detach(|| {
            let pages = self
                .inner
                .direct_ocr_pages_selected(
                    DirectOcrOptions {
                        min_page_coverage,
                        ..DirectOcrOptions::default()
                    },
                    &selection(pages)?,
                )
                .map_err(to_python_error)?;
            Ok(OcrCandidateStream { pages: Some(pages) })
        })
    }

    fn inspect_json(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            serde_json::to_string(&self.inner.summary().map_err(to_python_error)?)
                .map_err(serialization_error)
        })
    }

    #[pyo3(signature = (min_page_coverage=0.9, pages=None))]
    fn analyze_direct_ocr_json(
        &self,
        py: Python<'_>,
        min_page_coverage: f64,
        pages: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let result = self
                .inner
                .analyze_direct_ocr_selected(
                    DirectOcrOptions {
                        min_page_coverage,
                        ..DirectOcrOptions::default()
                    },
                    &selection(pages)?,
                )
                .map_err(to_python_error)?;
            serde_json::to_string(&result).map_err(serialization_error)
        })
    }

    fn decode_metrics_json(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            serde_json::to_string(&self.inner.decode_metrics()).map_err(serialization_error)
        })
    }

    #[cfg(feature = "ocr-onnx")]
    #[pyo3(signature = (detection_model, recognition_model, dictionary, min_page_coverage=0.9, min_confidence=0.0, pages=None, ocr_limits_json=None))]
    #[allow(clippy::too_many_arguments)]
    fn ocr_direct_json(
        &self,
        py: Python<'_>,
        detection_model: &[u8],
        recognition_model: &[u8],
        dictionary: &[u8],
        min_page_coverage: f64,
        min_confidence: f32,
        pages: Option<&str>,
        ocr_limits_json: Option<&str>,
    ) -> PyResult<String> {
        py.detach(|| {
            let limits = ocr_limits(ocr_limits_json)?;
            let selection = selection(pages)?;
            let engine =
                pdf_ocr::RapidOcrOnnxEngine::new(detection_model, recognition_model, dictionary)
                    .map_err(super::to_python_ocr_error)?;
            let result = pdf_ocr::ocr_document_selected(
                &self.inner,
                &engine,
                ocr_options(min_page_coverage, min_confidence),
                limits,
                &selection,
            )
            .map_err(super::to_python_ocr_error)?;
            serde_json::to_string(&result).map_err(serialization_error)
        })
    }

    #[cfg(feature = "ocr-onnx")]
    #[pyo3(signature = (detection_model, recognition_model, dictionary, min_page_coverage=0.9, min_confidence=0.0, pages=None, ocr_limits_json=None))]
    #[allow(clippy::too_many_arguments)]
    fn ocr_direct_stream(
        &self,
        py: Python<'_>,
        detection_model: &[u8],
        recognition_model: &[u8],
        dictionary: &[u8],
        min_page_coverage: f64,
        min_confidence: f32,
        pages: Option<&str>,
        ocr_limits_json: Option<&str>,
    ) -> PyResult<OcrJsonStream> {
        py.detach(|| {
            let limits = ocr_limits(ocr_limits_json)?;
            let selection = selection(pages)?;
            let engine =
                pdf_ocr::RapidOcrOnnxEngine::new(detection_model, recognition_model, dictionary)
                    .map_err(super::to_python_ocr_error)?;
            let pages = pdf_ocr::ocr_pages_owned(
                &self.inner,
                engine,
                ocr_options(min_page_coverage, min_confidence),
                limits,
                &selection,
            )
            .map_err(super::to_python_ocr_error)?;
            Ok(OcrJsonStream { pages: Some(pages) })
        })
    }
}

#[cfg(feature = "ocr-onnx")]
#[pyclass]
pub(super) struct OcrJsonStream {
    pages: Option<pdf_ocr::OcrPageProducer<'static, pdf_ocr::RapidOcrOnnxEngine>>,
}

#[cfg(feature = "ocr-onnx")]
#[pymethods]
impl OcrJsonStream {
    fn next_page_json(&mut self, py: Python<'_>) -> PyResult<Option<String>> {
        py.detach(|| {
            let value = self.pages.as_mut().and_then(Iterator::next);
            if value.is_none() || value.as_ref().is_some_and(Result::is_err) {
                self.pages = None;
            }
            value
                .map(|page| {
                    serde_json::to_string(&page.map_err(super::to_python_ocr_error)?)
                        .map_err(serialization_error)
                })
                .transpose()
        })
    }
    fn close(&mut self) {
        self.pages = None;
    }
}

#[cfg(feature = "ocr-onnx")]
fn ocr_options(min_page_coverage: f64, min_confidence: f32) -> pdf_ocr::OcrOptions {
    pdf_ocr::OcrOptions {
        direct_image: pdf_core::DirectOcrOptions {
            min_page_coverage,
            ..pdf_core::DirectOcrOptions::default()
        },
        min_confidence,
        ..pdf_ocr::OcrOptions::default()
    }
}

#[cfg(feature = "ocr-onnx")]
fn ocr_limits(value: Option<&str>) -> PyResult<pdf_ocr::OcrDocumentLimits> {
    match value {
        None => Ok(pdf_ocr::OcrDocumentLimits::default()),
        Some(value) if value.len() <= 65_536 => {
            serde_json::from_str(value).map_err(|error| invalid_option(&error.to_string()))
        }
        Some(_) => Err(invalid_option("OCR limits JSON exceeds 65536 bytes")),
    }
}

#[pyclass]
pub(super) struct OcrCandidateStream {
    pages: Option<pdf_core::DirectOcrPageProducer>,
}

#[pymethods]
impl OcrCandidateStream {
    fn next_page_json(&mut self, py: Python<'_>) -> PyResult<Option<String>> {
        py.detach(|| {
            let value = self.pages.as_mut().and_then(Iterator::next);
            if value.is_none() || value.as_ref().is_some_and(Result::is_err) {
                self.pages = None;
            }
            value
                .map(|page| {
                    serde_json::to_string(&page.map_err(to_python_error)?)
                        .map_err(serialization_error)
                })
                .transpose()
        })
    }

    fn close(&mut self) {
        self.pages = None;
    }
}

fn selection(pages: Option<&str>) -> PyResult<PageSelection> {
    pages.map_or_else(
        || Ok(PageSelection::all()),
        |pages| pages.parse().map_err(to_python_error),
    )
}

#[allow(clippy::fn_params_excessive_bools)]
fn layout_options(
    normalize_unicode: bool,
    quality: bool,
    debug_glyphs: bool,
    timings: bool,
) -> LayoutExtractionOptions {
    LayoutExtractionOptions {
        normalize_unicode,
        include_quality_metadata: quality,
        include_debug_glyphs: debug_glyphs,
        include_timings: timings,
    }
}

fn invalid_option(message: &str) -> PyErr {
    to_python_error(pdf_core::PdfError::new(
        pdf_core::ErrorCode::InvalidOption,
        None,
        message,
    ))
}
