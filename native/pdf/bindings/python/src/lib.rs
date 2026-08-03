use std::collections::VecDeque;

use pyo3::prelude::*;

pyo3::create_exception!(_native, PdfParseError, pyo3::exceptions::PyException);

/// Return parser package and stage information.
#[pyfunction]
fn version_info() -> (String, String) {
    let info = pdf_core::version_info();
    (info.version.to_owned(), info.stage.to_owned())
}

/// Extract plain Unicode text from PDF bytes.
#[pyfunction]
#[pyo3(signature = (data, normalize_unicode=false, layout=true))]
fn extract_text(data: &[u8], normalize_unicode: bool, layout: bool) -> PyResult<String> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    document
        .extract_text(pdf_core::TextExtractionOptions {
            normalize_unicode,
            layout,
        })
        .map(|result| result.text)
        .map_err(to_python_error)
}

/// Extract structured pages, spans, warnings, and text as a JSON string.
#[pyfunction]
#[pyo3(signature = (data, normalize_unicode=false, layout=true))]
fn extract_json(data: &[u8], normalize_unicode: bool, layout: bool) -> PyResult<String> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let result = document
        .extract_text(pdf_core::TextExtractionOptions {
            normalize_unicode,
            layout,
        })
        .map_err(to_python_error)?;
    serde_json::to_string(&result).map_err(|error| {
        PdfParseError::new_err(
            serde_json::json!({
                "code": "serialization_error",
                "offset": null,
                "message": error.to_string(),
            })
            .to_string(),
        )
    })
}

/// Extract a V2 structured result as a JSON string.
#[pyfunction]
#[pyo3(signature = (data, mode="auto", normalize_unicode=false, quality=true))]
fn extract_v2_json(
    data: &[u8],
    mode: &str,
    normalize_unicode: bool,
    quality: bool,
) -> PyResult<String> {
    let mode = parse_extraction_mode(mode).map_err(to_python_error)?;
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let result = document
        .extract_text_v2(pdf_core::TextExtractionOptionsV2 {
            normalize_unicode,
            mode,
            include_quality_metadata: quality,
        })
        .map_err(to_python_error)?;
    serde_json::to_string(&result).map_err(serialization_error)
}

/// Extract versioned Layout IR as a JSON string.
#[pyfunction]
#[pyo3(signature = (data, normalize_unicode=false, quality=true, debug_glyphs=false, timings=false))]
#[allow(clippy::fn_params_excessive_bools)] // Mirrors explicit Python keyword options.
fn extract_layout_json(
    data: &[u8],
    normalize_unicode: bool,
    quality: bool,
    debug_glyphs: bool,
    timings: bool,
) -> PyResult<String> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let result = document
        .extract_layout(pdf_core::LayoutExtractionOptions {
            normalize_unicode,
            include_quality_metadata: quality,
            include_debug_glyphs: debug_glyphs,
            include_timings: timings,
        })
        .map_err(to_python_error)?;
    serde_json::to_string(&result).map_err(serialization_error)
}

/// Page-at-a-time Layout IR JSON stream backed by the native lazy event producer.
///
/// Metadata begins with the bounded `DocumentStart` values. After the iterator is
/// exhausted it is replaced with `DocumentFinalize` metadata and page patches.
#[pyclass]
struct LayoutJsonStream {
    metadata_json: String,
    start: pdf_core::LayoutDocumentStart,
    events: pdf_core::LayoutEventProducer,
    finalizations: VecDeque<pdf_core::LayoutPageFinalization>,
    finalized: bool,
}

#[pymethods]
impl LayoutJsonStream {
    /// Return current metadata without the document text or page array.
    #[getter]
    fn metadata_json(&self) -> &str {
        &self.metadata_json
    }

    /// Produce and release the next page as JSON, or `None` after finalization.
    fn next_page_json(&mut self) -> PyResult<Option<String>> {
        match self.events.next() {
            Some(Ok(pdf_core::LayoutEvent::Page(page))) => serde_json::to_string(&page)
                .map(Some)
                .map_err(serialization_error),
            Some(Ok(pdf_core::LayoutEvent::DocumentFinalize(mut finalize))) => {
                self.metadata_json = finalized_stream_metadata(&self.start, &finalize)?;
                self.finalizations = std::mem::take(&mut finalize.page_finalizations).into();
                self.finalized = true;
                Ok(None)
            }
            Some(Ok(pdf_core::LayoutEvent::DocumentStart(_))) => Err(stream_protocol_error(
                "native Layout stream emitted duplicate document_start",
            )),
            Some(Err(error)) => Err(to_python_error(error)),
            None if self.finalized => Ok(None),
            None => Err(stream_protocol_error(
                "native Layout stream ended before document_finalize",
            )),
        }
    }

    /// Return and release the next stable-ID page finalization as JSON.
    fn next_finalization_json(&mut self) -> PyResult<Option<String>> {
        if !self.finalized {
            return Err(stream_protocol_error(
                "page finalizations are unavailable before document_finalize",
            ));
        }
        self.finalizations
            .pop_front()
            .map(|value| serde_json::to_string(&value).map_err(serialization_error))
            .transpose()
    }

    /// Number of native page finalizations not yet serialized.
    #[getter]
    fn remaining_finalizations(&self) -> usize {
        self.finalizations.len()
    }

    /// Number of native page events not yet produced.
    #[getter]
    fn remaining_pages(&self) -> usize {
        self.events.remaining_pages()
    }
}

/// Extract versioned Layout IR from a genuinely incremental native event producer.
#[pyfunction]
#[pyo3(signature = (data, normalize_unicode=false, quality=true, debug_glyphs=false, timings=false))]
#[allow(clippy::fn_params_excessive_bools)] // Mirrors explicit Python keyword options.
fn extract_layout_stream(
    data: &[u8],
    normalize_unicode: bool,
    quality: bool,
    debug_glyphs: bool,
    timings: bool,
) -> PyResult<LayoutJsonStream> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let mut events = document
        .extract_layout_events(pdf_core::LayoutExtractionOptions {
            normalize_unicode,
            include_quality_metadata: quality,
            include_debug_glyphs: debug_glyphs,
            include_timings: timings,
        })
        .map_err(to_python_error)?;
    let start = match events.next() {
        Some(Ok(pdf_core::LayoutEvent::DocumentStart(start))) => start,
        Some(Ok(_)) => {
            return Err(stream_protocol_error(
                "native Layout stream did not begin with document_start",
            ));
        }
        Some(Err(error)) => return Err(to_python_error(error)),
        None => {
            return Err(stream_protocol_error(
                "native Layout stream ended before document_start",
            ));
        }
    };
    let metadata_json = initial_stream_metadata(&start)?;
    Ok(LayoutJsonStream {
        metadata_json,
        start,
        events,
        finalizations: VecDeque::new(),
        finalized: false,
    })
}

fn initial_stream_metadata(start: &pdf_core::LayoutDocumentStart) -> PyResult<String> {
    serde_json::to_string(&serde_json::json!({
        "schema_version": start.schema_version,
        "parser": &start.parser,
        "coordinate_space": start.coordinate_space,
        "options": start.options,
        "options_digest": &start.options_digest,
        "capabilities": start.capabilities,
        "page_count": start.page_count,
        "named_destinations": [],
        "outlines": [],
        "warnings": [],
        "quality": null,
        "timings": null,
        "page_finalizations": [],
        "streaming": {
            "page_transfer": "native_events_v2",
            "metadata_finalized": false,
            "page_finalization": "draining_stable_id_patches_v1",
            "document_text_omitted": true,
        },
    }))
    .map_err(serialization_error)
}

fn finalized_stream_metadata(
    start: &pdf_core::LayoutDocumentStart,
    finalize: &pdf_core::LayoutDocumentFinalize,
) -> PyResult<String> {
    serde_json::to_string(&serde_json::json!({
        "schema_version": start.schema_version,
        "parser": &start.parser,
        "coordinate_space": start.coordinate_space,
        "options": start.options,
        "options_digest": &start.options_digest,
        "capabilities": finalize.capabilities.unwrap_or(start.capabilities),
        "page_count": finalize.page_count,
        "named_destinations": &finalize.named_destinations,
        "outlines": &finalize.outlines,
        "warnings": &finalize.warnings,
        "quality": &finalize.quality,
        "timings": &finalize.timings,

        "streaming": {
            "page_transfer": "native_events_v2",
            "metadata_finalized": true,
            "page_finalization": "draining_stable_id_patches_v1",
            "document_text_omitted": true,
        },
    }))
    .map_err(serialization_error)
}

fn stream_protocol_error(message: &str) -> PyErr {
    to_python_error(pdf_core::PdfError::new(
        pdf_core::ErrorCode::InvalidObject,
        None,
        message,
    ))
}
/// Inspect the PDF header and cross-reference summary as a JSON string.
#[pyfunction]
fn inspect_json(data: &[u8]) -> PyResult<String> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let summary = document.summary().map_err(to_python_error)?;
    serde_json::to_string(&summary).map_err(|error| {
        PdfParseError::new_err(
            serde_json::json!({
                "code": "serialization_error",
                "offset": null,
                "message": error.to_string(),
            })
            .to_string(),
        )
    })
}

/// Extract image `XObjects` and encoded/raw bytes as a JSON string.
#[pyfunction]
fn extract_images_json(data: &[u8]) -> PyResult<String> {
    let document = pdf_core::PdfDocument::parse(data).map_err(to_python_error)?;
    let images = document.extract_images().map_err(to_python_error)?;
    serde_json::to_string(&images).map_err(|error| {
        PdfParseError::new_err(
            serde_json::json!({
                "code": "serialization_error",
                "offset": null,
                "message": error.to_string(),
            })
            .to_string(),
        )
    })
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

#[allow(clippy::needless_pass_by_value)] // map_err supplies owned serializer errors.
fn serialization_error(error: serde_json::Error) -> PyErr {
    PdfParseError::new_err(
        serde_json::json!({
            "code": "serialization_error",
            "offset": null,
            "message": error.to_string(),
        })
        .to_string(),
    )
}

#[allow(clippy::needless_pass_by_value)] // map_err supplies owned parser errors.
fn to_python_error(error: pdf_core::PdfError) -> PyErr {
    PdfParseError::new_err(
        serde_json::json!({
            "code": error.code.as_str(),
            "offset": error.offset,
            "message": error.message,
        })
        .to_string(),
    )
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("PdfParseError", module.py().get_type::<PdfParseError>())?;
    module.add_function(wrap_pyfunction!(version_info, module)?)?;
    module.add_function(wrap_pyfunction!(extract_text, module)?)?;
    module.add_function(wrap_pyfunction!(extract_json, module)?)?;
    module.add_function(wrap_pyfunction!(extract_v2_json, module)?)?;
    module.add_function(wrap_pyfunction!(extract_layout_json, module)?)?;
    module.add_class::<LayoutJsonStream>()?;
    module.add_function(wrap_pyfunction!(extract_layout_stream, module)?)?;
    module.add_function(wrap_pyfunction!(inspect_json, module)?)?;
    module.add_function(wrap_pyfunction!(extract_images_json, module)?)?;
    Ok(())
}
