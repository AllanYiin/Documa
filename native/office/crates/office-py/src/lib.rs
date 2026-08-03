use std::collections::VecDeque;
use std::fs;
use std::path::{Path, PathBuf};

use office_core::{
    OfficeDocument, OfficeError, OfficeFormat, ParseOptions, capabilities_value,
    detect_format as detect_from_bytes,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn py_error(error: OfficeError) -> PyErr {
    let payload = serde_json::json!({
        "code": error.code,
        "message": error.message,
        "recoverable": error.recoverable,
        "context": error.context,
    });
    PyValueError::new_err(payload.to_string())
}

fn parse(path: &Path, options: &ParseOptions) -> Result<OfficeDocument, OfficeError> {
    let bytes = fs::read(path).map_err(|error| {
        OfficeError::new(
            "OFFICE_OPEN_FAILED",
            format!("Unable to read {}: {error}", path.display()),
            true,
        )
    })?;
    if bytes.len() as u64 > options.limits.max_input_bytes {
        return Err(OfficeError::new(
            "INPUT_LIMIT_EXCEEDED",
            "Office input exceeds max_input_bytes.",
            false,
        ));
    }
    match detect_from_bytes(path, &bytes)? {
        OfficeFormat::Docx => office_word::parse_docx(path, &bytes, options),
        OfficeFormat::Xls => {
            office_sheet::parse_spreadsheet(path, &bytes, OfficeFormat::Xls, options)
        }
        OfficeFormat::Xlsx => {
            office_sheet::parse_spreadsheet(path, &bytes, OfficeFormat::Xlsx, options)
        }
        OfficeFormat::Pptx => office_slide::parse_pptx(path, &bytes, options),
        OfficeFormat::Doc | OfficeFormat::Ppt => {
            unreachable!("legacy formats are rejected by detect_format")
        }
    }
}

#[pyclass]
struct OfficeEventStream {
    events: VecDeque<String>,
    #[pyo3(get)]
    metadata_json: String,
}

#[pymethods]
impl OfficeEventStream {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self) -> Option<String> {
        self.events.pop_front()
    }

    fn remaining(&self) -> usize {
        self.events.len()
    }
}

#[pyfunction]
fn version_info() -> (&'static str, &'static str) {
    (
        env!("CARGO_PKG_VERSION"),
        office_core::OFFICE_LAYOUT_CONTRACT,
    )
}

#[pyfunction]
fn capabilities_json() -> String {
    capabilities_value().to_string()
}

#[pyfunction]
fn detect_format(path: PathBuf) -> PyResult<String> {
    let bytes = fs::read(&path).map_err(|error| PyValueError::new_err(error.to_string()))?;
    detect_from_bytes(&path, &bytes)
        .map(|format| format.as_str().to_string())
        .map_err(py_error)
}

#[pyfunction]
#[pyo3(signature = (
    path,
    extract_images=true,
    include_hidden=false,
    revision_mode="final",
    formula_mode="formula_and_cached_value",
    external_links="metadata_only"
))]
fn open_native(
    path: PathBuf,
    extract_images: bool,
    include_hidden: bool,
    revision_mode: &str,
    formula_mode: &str,
    external_links: &str,
) -> PyResult<OfficeEventStream> {
    let options = ParseOptions {
        extract_images,
        include_hidden,
        revision_mode: revision_mode.to_string(),
        formula_mode: formula_mode.to_string(),
        external_links: external_links.to_string(),
        ..ParseOptions::default()
    };
    let events = parse(&path, &options).map_err(py_error)?.into_events();
    let metadata_json = events
        .first()
        .map(ToString::to_string)
        .unwrap_or_else(|| "{}".into());
    Ok(OfficeEventStream {
        events: events.into_iter().map(|value| value.to_string()).collect(),
        metadata_json,
    })
}

#[pymodule]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OfficeEventStream>()?;
    module.add_function(wrap_pyfunction!(version_info, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities_json, module)?)?;
    module.add_function(wrap_pyfunction!(detect_format, module)?)?;
    module.add_function(wrap_pyfunction!(open_native, module)?)?;
    Ok(())
}
