use std::collections::BTreeMap;
use std::path::Path;

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use thiserror::Error;

pub const OFFICE_LAYOUT_SCHEMA_VERSION: u32 = 1;
pub const OFFICE_LAYOUT_CONTRACT: &str = "office-layout-v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OfficeFormat {
    Docx,
    Xls,
    Xlsx,
    Pptx,
    Doc,
    Ppt,
}

impl OfficeFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Docx => "docx",
            Self::Xls => "xls",
            Self::Xlsx => "xlsx",
            Self::Pptx => "pptx",
            Self::Doc => "doc",
            Self::Ppt => "ppt",
        }
    }

    pub fn supported(self) -> bool {
        matches!(self, Self::Docx | Self::Xls | Self::Xlsx | Self::Pptx)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ParseOptions {
    pub extract_images: bool,
    pub include_hidden: bool,
    pub revision_mode: String,
    pub formula_mode: String,
    pub external_links: String,
    pub asset_mode: String,
    pub sheets: Vec<String>,
    pub cell_range: Option<String>,
    pub batch_rows: usize,
    pub sparse: bool,
    pub limits: ParseLimits,
}

impl Default for ParseOptions {
    fn default() -> Self {
        Self {
            extract_images: true,
            include_hidden: false,
            revision_mode: "final".into(),
            formula_mode: "formula_and_cached_value".into(),
            external_links: "metadata_only".into(),
            asset_mode: "inline".into(),
            sheets: Vec::new(),
            cell_range: None,
            batch_rows: 0,
            sparse: false,
            limits: ParseLimits::default(),
        }
    }
}

impl ParseOptions {
    pub fn from_json(input: &str) -> Result<Self> {
        let value: Value = serde_json::from_str(input)
            .map_err(|e| OfficeError::new("INVALID_OPTIONS", e.to_string(), true))?;
        if !value.is_object() || value.get("limits").is_some_and(|v| !v.is_object()) {
            return Err(OfficeError::new(
                "INVALID_OPTIONS",
                "Options and limits must be JSON objects.",
                true,
            ));
        }
        let options: Self = serde_json::from_value(value)
            .map_err(|e| OfficeError::new("INVALID_OPTIONS", e.to_string(), true))?;
        options.validate()?;
        Ok(options)
    }
    pub fn validate(&self) -> Result<()> {
        for (key, value, allowed) in [
            (
                "revision_mode",
                self.revision_mode.as_str(),
                &["final", "original", "all"][..],
            ),
            (
                "formula_mode",
                self.formula_mode.as_str(),
                &["formula_and_cached_value", "formula", "cached_value"][..],
            ),
            (
                "external_links",
                self.external_links.as_str(),
                &["metadata_only", "ignore"][..],
            ),
            (
                "asset_mode",
                self.asset_mode.as_str(),
                &["inline", "metadata"][..],
            ),
        ] {
            if !allowed.contains(&value) {
                return Err(OfficeError::new(
                    "INVALID_OPTIONS",
                    format!("Invalid {key}: {value}; expected {allowed:?}"),
                    true,
                ));
            }
        }
        if self.batch_rows > 1_048_576 {
            return Err(OfficeError::new(
                "INVALID_OPTIONS",
                "batch_rows must be between 0 and 1048576.",
                true,
            ));
        }
        if let Some(range) = &self.cell_range {
            parse_cell_range(range)?;
        }
        if self.limits.max_input_bytes == 0
            || self.limits.max_parts == 0
            || self.limits.max_part_bytes == 0
            || self.limits.max_uncompressed_bytes == 0
            || self.limits.max_compression_ratio == 0
            || self.limits.max_cells == 0
            || self.limits.max_shapes == 0
            || self.limits.max_text_chars == 0
            || self.limits.max_xml_depth == 0
            || self.limits.max_xml_depth > 256
        {
            return Err(OfficeError::new(
                "INVALID_OPTIONS",
                "Limits must be positive; max_xml_depth must be at most 256.",
                true,
            ));
        }
        Ok(())
    }
}

pub type CellRange = ((u32, u32), (u32, u32));

pub fn parse_cell_range(value: &str) -> Result<CellRange> {
    fn cell(value: &str) -> Option<(u32, u32)> {
        let value = value.to_ascii_uppercase();
        let value = value.strip_prefix('$').unwrap_or(&value);
        let index = value.find(|c: char| c.is_ascii_digit())?;
        let (letters, digits) = value.split_at(index);
        let letters = letters.strip_suffix('$').unwrap_or(letters);
        if letters.is_empty() || !letters.bytes().all(|c| c.is_ascii_uppercase()) {
            return None;
        }
        let column = letters.bytes().try_fold(0_u32, |n, c| {
            n.checked_mul(26)?.checked_add(u32::from(c - b'A' + 1))
        })?;
        let row: u32 = digits.parse().ok()?;
        (row > 0 && row <= 1_048_576 && column > 0 && column <= 16_384)
            .then(|| (row - 1, column - 1))
    }
    let (start, end) = value.split_once(':').unwrap_or((value, value));
    if let (Some(start), Some(end)) = (cell(start), cell(end))
        && start.0 <= end.0
        && start.1 <= end.1
    {
        return Ok((start, end));
    }
    Err(OfficeError::new(
        "INVALID_OPTIONS",
        format!("Invalid A1 cell range: {value}"),
        true,
    ))
}

pub fn read_input(path: &Path, limits: &ParseLimits) -> Result<Vec<u8>> {
    use std::io::Read;
    let file = std::fs::File::open(path)
        .map_err(|e| OfficeError::new("OFFICE_OPEN_FAILED", e.to_string(), true))?;
    let length = file
        .metadata()
        .map_err(|e| OfficeError::new("OFFICE_OPEN_FAILED", e.to_string(), true))?
        .len();
    if length > limits.max_input_bytes {
        return Err(OfficeError::new(
            "INPUT_LIMIT_EXCEEDED",
            "Input exceeds max_input_bytes.",
            false,
        ));
    }
    let mut bytes = Vec::new();
    file.take(limits.max_input_bytes.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|e| OfficeError::new("OFFICE_OPEN_FAILED", e.to_string(), true))?;
    if bytes.len() as u64 > limits.max_input_bytes {
        return Err(OfficeError::new(
            "INPUT_LIMIT_EXCEEDED",
            "Input exceeds max_input_bytes.",
            false,
        ));
    }
    Ok(bytes)
}

#[derive(Default)]
pub struct ParseBudget {
    pub text: usize,
    pub cells: usize,
    pub shapes: usize,
}
impl ParseBudget {
    pub fn add_text(&mut self, value: &str, limits: &ParseLimits) -> Result<()> {
        self.text = self.text.saturating_add(value.chars().count());
        check_limit(self.text, limits.max_text_chars, "text characters")
    }
    pub fn add_cells(&mut self, count: usize, limits: &ParseLimits) -> Result<()> {
        self.cells = self.cells.saturating_add(count);
        check_limit(self.cells, limits.max_cells, "cells")
    }
    pub fn add_shapes(&mut self, count: usize, limits: &ParseLimits) -> Result<()> {
        self.shapes = self.shapes.saturating_add(count);
        check_limit(self.shapes, limits.max_shapes, "shapes")
    }
}
pub fn check_limit(count: usize, limit: usize, label: &str) -> Result<()> {
    if count > limit {
        Err(OfficeError::new(
            "PARSE_LIMIT_EXCEEDED",
            format!("Exceeded limit for {label}."),
            false,
        ))
    } else {
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ParseLimits {
    pub max_input_bytes: u64,
    pub max_parts: usize,
    pub max_part_bytes: u64,
    pub max_uncompressed_bytes: u64,
    pub max_compression_ratio: u64,
    pub max_cells: usize,
    pub max_shapes: usize,
    pub max_text_chars: usize,
    pub max_xml_depth: usize,
}

impl Default for ParseLimits {
    fn default() -> Self {
        Self {
            max_input_bytes: 512 * 1024 * 1024,
            max_parts: 100_000,
            max_part_bytes: 256 * 1024 * 1024,
            max_uncompressed_bytes: 2 * 1024 * 1024 * 1024,
            max_compression_ratio: 100,
            max_cells: 10_000_000,
            max_shapes: 1_000_000,
            max_text_chars: 200_000_000,
            max_xml_depth: 128,
        }
    }
}

#[derive(Debug, Error)]
#[error("{code}: {message}")]
pub struct OfficeError {
    pub code: &'static str,
    pub message: String,
    pub recoverable: bool,
    pub context: BTreeMap<String, Value>,
}

impl OfficeError {
    pub fn new(code: &'static str, message: impl Into<String>, recoverable: bool) -> Self {
        Self {
            code,
            message: message.into(),
            recoverable,
            context: BTreeMap::new(),
        }
    }

    pub fn with_context(mut self, key: impl Into<String>, value: impl Into<Value>) -> Self {
        self.context.insert(key.into(), value.into());
        self
    }

    pub fn to_value(&self) -> Value {
        json!({
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
            "context": self.context,
        })
    }
}

pub type Result<T> = std::result::Result<T, OfficeError>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OfficeDocument {
    pub schema_version: u32,
    pub format: OfficeFormat,
    pub coordinate_space: String,
    pub source_name: String,
    pub source_hash: String,
    pub units: Vec<LogicalUnit>,
    pub assets: Vec<Asset>,
    pub warnings: Vec<Warning>,
    pub metadata: BTreeMap<String, Value>,
}

impl OfficeDocument {
    pub fn new(
        format: OfficeFormat,
        source: &Path,
        source_bytes: &[u8],
        coordinate_space: &str,
    ) -> Self {
        Self {
            schema_version: OFFICE_LAYOUT_SCHEMA_VERSION,
            format,
            coordinate_space: coordinate_space.into(),
            source_name: source.to_string_lossy().into_owned(),
            source_hash: sha256_hex(source_bytes),
            units: Vec::new(),
            assets: Vec::new(),
            warnings: Vec::new(),
            metadata: BTreeMap::new(),
        }
    }

    pub fn into_events(self) -> Vec<Value> {
        self.into_event_iter().collect()
    }
    pub fn into_event_iter(self) -> impl Iterator<Item = Value> {
        let start = json!({
            "event": "document_start",
            "schema_version": self.schema_version,
            "format": self.format,
            "coordinate_space": self.coordinate_space,
            "source_name": self.source_name,
            "source_hash": self.source_hash,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "unit_count": self.units.len(),
        });
        std::iter::once(start)
            .chain(
                self.units
                    .into_iter()
                    .map(|unit| json!({"event": "unit", "unit": unit})),
            )
            .chain(
                self.assets
                    .into_iter()
                    .map(|asset| json!({"event": "asset", "asset": asset})),
            )
            .chain(std::iter::once(
                json!({"event": "document_end", "status": "ok"}),
            ))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogicalUnit {
    pub id: String,
    pub number: u32,
    pub label: String,
    pub kind: String,
    pub width: f64,
    pub height: f64,
    pub hidden: bool,
    pub blocks: Vec<Block>,
    pub tables: Vec<Table>,
    pub metadata: BTreeMap<String, Value>,
}

impl LogicalUnit {
    pub fn new(id: impl Into<String>, number: u32, label: impl Into<String>, kind: &str) -> Self {
        Self {
            id: id.into(),
            number,
            label: label.into(),
            kind: kind.into(),
            width: 0.0,
            height: 0.0,
            hidden: false,
            blocks: Vec::new(),
            tables: Vec::new(),
            metadata: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Block {
    pub id: String,
    pub kind: String,
    pub text: String,
    pub order_index: usize,
    pub bbox: Option<[f64; 4]>,
    pub spans: Vec<Span>,
    pub source_refs: Vec<String>,
    pub confidence: String,
    pub metadata: BTreeMap<String, Value>,
}

impl Block {
    pub fn new(
        id: impl Into<String>,
        kind: &str,
        text: impl Into<String>,
        order_index: usize,
        source_ref: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            kind: kind.into(),
            text: text.into(),
            order_index,
            bbox: None,
            spans: Vec::new(),
            source_refs: vec![source_ref.into()],
            confidence: "high".into(),
            metadata: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Span {
    pub id: String,
    pub text: String,
    pub styles: Vec<String>,
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Table {
    pub id: String,
    pub block_id: String,
    pub rows: Vec<Vec<Option<String>>>,
    pub source_refs: Vec<String>,
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Asset {
    pub id: String,
    pub mime_type: String,
    pub file_name: String,
    pub data_base64: String,
    pub sha256: String,
    pub source_ref: String,
    pub alt_text: Option<String>,
    pub metadata: BTreeMap<String, Value>,
}

impl Asset {
    pub fn from_bytes(
        id: impl Into<String>,
        file_name: impl Into<String>,
        mime_type: impl Into<String>,
        source_ref: impl Into<String>,
        bytes: &[u8],
    ) -> Self {
        Self {
            id: id.into(),
            file_name: file_name.into(),
            mime_type: mime_type.into(),
            data_base64: base64::engine::general_purpose::STANDARD.encode(bytes),
            sha256: sha256_hex(bytes),
            source_ref: source_ref.into(),
            alt_text: None,
            metadata: BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Warning {
    pub code: String,
    pub message: String,
    pub context: BTreeMap<String, Value>,
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn detect_format(path: &Path, bytes: &[u8]) -> Result<OfficeFormat> {
    let suffix = path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let format = match suffix.as_str() {
        "docx" => OfficeFormat::Docx,
        "xlsx" => OfficeFormat::Xlsx,
        "xls" => OfficeFormat::Xls,
        "pptx" => OfficeFormat::Pptx,
        "doc" => OfficeFormat::Doc,
        "ppt" => OfficeFormat::Ppt,
        "docm" | "xlsm" | "pptm" => {
            return Err(OfficeError::new(
                "MACRO_ENABLED_OFFICE_NOT_SUPPORTED",
                "Macro-enabled Office files are not supported.",
                true,
            ));
        }
        _ => {
            return Err(OfficeError::new(
                "UNSUPPORTED_OFFICE_FORMAT",
                format!("Unsupported Office extension: {suffix}"),
                true,
            ));
        }
    };
    if matches!(format, OfficeFormat::Doc | OfficeFormat::Ppt) {
        return Err(OfficeError::new(
            "LEGACY_OFFICE_NOT_SUPPORTED",
            format!(
                "Legacy .{} parsing is reserved for a future release.",
                format.as_str()
            ),
            true,
        ));
    }
    if bytes.len() >= 8 && bytes[..8] == [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] {
        if matches!(
            format,
            OfficeFormat::Docx | OfficeFormat::Xlsx | OfficeFormat::Pptx
        ) {
            return Err(OfficeError::new(
                "ENCRYPTED_OFFICE_NOT_SUPPORTED",
                "The OOXML extension contains an OLE encrypted package or does not match its content.",
                false,
            ));
        }
    } else if format == OfficeFormat::Xls {
        return Err(OfficeError::new(
            "FORMAT_MISMATCH",
            "An .xls file must be an OLE/BIFF workbook.",
            false,
        ));
    }
    if matches!(
        format,
        OfficeFormat::Docx | OfficeFormat::Xlsx | OfficeFormat::Pptx
    ) && !bytes.starts_with(b"PK")
    {
        return Err(OfficeError::new(
            "FORMAT_MISMATCH",
            "An OOXML document must be a ZIP package.",
            false,
        ));
    }
    Ok(format)
}

pub fn capabilities_value() -> Value {
    json!({
        "contract": OFFICE_LAYOUT_CONTRACT,
        "formats": {
            "docx": {"supported": true, "coordinate_space": "logical_flow", "features": ["paragraphs", "runs", "headings", "lists", "tables", "footnotes", "comments", "headers_footers", "hyperlinks", "images"]},
            "xls": {"supported": true, "variant": "BIFF8", "coordinate_space": "cell_grid", "features": ["worksheets", "cells", "formulas", "cached_values", "sheet_visibility"]},
            "xlsx": {"supported": true, "coordinate_space": "cell_grid", "features": ["worksheets", "cells", "formulas", "cached_values", "sheet_visibility", "images_inventory"]},
            "pptx": {"supported": true, "coordinate_space": "slide_points", "features": ["slides", "shapes", "text", "tables", "speaker_notes", "images", "bbox"]},
            "doc": {"supported": false, "error": "LEGACY_OFFICE_NOT_SUPPORTED"},
            "ppt": {"supported": false, "error": "LEGACY_OFFICE_NOT_SUPPORTED"}
        },
        "security": {"network": false, "external_processes": false, "macros_executed": false, "encrypted_documents": false},
        "options": {"revision_mode": ["final", "original", "all"], "formula_mode": ["formula_and_cached_value", "formula", "cached_value"], "external_links": ["metadata_only", "ignore"], "asset_mode": ["inline", "metadata"], "sheets": true, "cell_range": true, "batch_rows": true, "sparse": true, "limits": true},
        "input": ["path", "bytes", "binary_stream"],
        "delivery": "atomic_parse_lazy_event_serialization"
    })
}

pub fn layout_schema_value() -> Value {
    serde_json::from_str(include_str!("schema.json")).expect("embedded IR schema is valid JSON")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_formats_are_explicitly_rejected() {
        let err = detect_format(Path::new("old.doc"), b"anything").unwrap_err();
        assert_eq!(err.code, "LEGACY_OFFICE_NOT_SUPPORTED");
    }

    #[test]
    fn capabilities_are_versioned() {
        assert_eq!(capabilities_value()["contract"], OFFICE_LAYOUT_CONTRACT);
    }
}

#[cfg(test)]
mod option_tests {
    use super::*;
    #[test]
    fn cell_ranges_are_bounded_and_strict() {
        assert_eq!(
            parse_cell_range("$A$1:XFD1048576").unwrap(),
            ((0, 0), (1048575, 16383))
        );
        for value in ["A$$1", "A1$", "A0", "XFE1", "A1:B0", "B2:A1"] {
            assert!(parse_cell_range(value).is_err(), "{value}");
        }
    }
    #[test]
    fn text_budget_counts_characters_not_blocks_or_utf8_bytes() {
        let mut budget = ParseBudget::default();
        let limits = ParseLimits {
            max_text_chars: 3,
            ..ParseLimits::default()
        };
        assert!(budget.add_text("中文A", &limits).is_ok());
        assert!(budget.add_text("B", &limits).is_err());
    }
    #[test]
    fn impossible_batch_size_is_rejected() {
        let options = ParseOptions {
            batch_rows: usize::MAX,
            ..ParseOptions::default()
        };
        assert!(options.validate().is_err());
    }
}
