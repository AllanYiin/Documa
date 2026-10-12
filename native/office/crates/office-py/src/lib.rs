use office_core::{
    Asset, LogicalUnit, OfficeDocument, OfficeError, OfficeFormat, ParseOptions,
    capabilities_value, detect_format as detect_from_bytes,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Serialize;
use serde_json::{Value, json};
#[derive(Serialize)]
struct UnitEvent {
    event: &'static str,
    unit: LogicalUnit,
}
#[derive(Serialize)]
struct AssetEvent {
    event: &'static str,
    asset: Asset,
}
use std::collections::VecDeque;
use std::path::{Path, PathBuf};

fn py_error(error: OfficeError) -> PyErr {
    PyValueError::new_err(error.to_value().to_string())
}
fn parse(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument, OfficeError> {
    options.validate()?;
    if bytes.len() as u64 > options.limits.max_input_bytes {
        return Err(OfficeError::new(
            "INPUT_LIMIT_EXCEEDED",
            "Office input exceeds max_input_bytes.",
            false,
        ));
    }
    match detect_from_bytes(path, bytes)? {
        OfficeFormat::Docx => office_word::parse_docx(path, bytes, options),
        OfficeFormat::Xls => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xls, options)
        }
        OfficeFormat::Xlsx => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xlsx, options)
        }
        OfficeFormat::Pptx => office_slide::parse_pptx(path, bytes, options),
        _ => unreachable!("legacy formats rejected"),
    }
}
#[pyclass]
struct OfficeEventStream {
    start: Option<Value>,
    units: VecDeque<LogicalUnit>,
    assets: VecDeque<Asset>,
    finished: bool,
    #[pyo3(get)]
    metadata_json: String,
}
impl OfficeEventStream {
    fn new(doc: OfficeDocument) -> Self {
        let start = json!({"event":"document_start","schema_version":doc.schema_version,"format":doc.format,"coordinate_space":doc.coordinate_space,"source_name":doc.source_name,"source_hash":doc.source_hash,"metadata":doc.metadata,"warnings":doc.warnings,"unit_count":doc.units.len()});
        Self {
            metadata_json: start.to_string(),
            start: Some(start),
            units: doc.units.into(),
            assets: doc.assets.into(),
            finished: false,
        }
    }
}
#[pymethods]
impl OfficeEventStream {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }
    fn __next__(&mut self) -> Option<String> {
        if let Some(start) = self.start.take() {
            return Some(start.to_string());
        }
        if let Some(unit) = self.units.pop_front() {
            return Some(
                serde_json::to_string(&UnitEvent {
                    event: "unit",
                    unit,
                })
                .expect("IR unit is serializable"),
            );
        }
        if let Some(asset) = self.assets.pop_front() {
            return Some(
                serde_json::to_string(&AssetEvent {
                    event: "asset",
                    asset,
                })
                .expect("IR asset is serializable"),
            );
        }
        if self.finished {
            return None;
        }
        self.finished = true;
        Some(json!({"event":"document_end","status":"ok"}).to_string())
    }
    fn remaining(&self) -> usize {
        usize::from(self.start.is_some())
            + self.units.len()
            + self.assets.len()
            + usize::from(!self.finished)
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
fn schema_json() -> String {
    office_core::layout_schema_value().to_string()
}
#[pyfunction]
fn detect_format(path: PathBuf) -> PyResult<String> {
    let bytes =
        office_core::read_input(&path, &office_core::ParseLimits::default()).map_err(py_error)?;
    detect_from_bytes(&path, &bytes)
        .map(|f| f.as_str().into())
        .map_err(py_error)
}
fn options(value: &str) -> PyResult<ParseOptions> {
    ParseOptions::from_json(value).map_err(py_error)
}

#[pyfunction]
fn validate_options_json(options_json: &str) -> PyResult<()> {
    options(options_json).map(|_| ())
}
#[pyfunction]
#[pyo3(signature=(path, options_json="{}"))]
fn open_native(py: Python<'_>, path: PathBuf, options_json: &str) -> PyResult<OfficeEventStream> {
    let options = options(options_json)?;
    let doc = py
        .allow_threads(move || {
            let bytes = office_core::read_input(&path, &options.limits)?;
            parse(&path, &bytes, &options)
        })
        .map_err(py_error)?;
    Ok(OfficeEventStream::new(doc))
}
#[pyfunction]
#[pyo3(signature=(data, source_name, options_json="{}"))]
fn open_bytes_native(
    py: Python<'_>,
    data: &[u8],
    source_name: &str,
    options_json: &str,
) -> PyResult<OfficeEventStream> {
    let options = options(options_json)?;
    let doc = py
        .allow_threads(|| parse(Path::new(source_name), data, &options))
        .map_err(py_error)?;
    Ok(OfficeEventStream::new(doc))
}
#[pymodule]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<OfficeEventStream>()?;
    module.add_function(wrap_pyfunction!(version_info, module)?)?;
    module.add_function(wrap_pyfunction!(capabilities_json, module)?)?;
    module.add_function(wrap_pyfunction!(schema_json, module)?)?;
    module.add_function(wrap_pyfunction!(detect_format, module)?)?;
    module.add_function(wrap_pyfunction!(open_native, module)?)?;
    module.add_function(wrap_pyfunction!(validate_options_json, module)?)?;
    module.add_function(wrap_pyfunction!(open_bytes_native, module)?)?;
    Ok(())
}
