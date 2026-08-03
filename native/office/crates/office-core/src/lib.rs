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
pub struct ParseOptions {
    pub extract_images: bool,
    pub include_hidden: bool,
    pub revision_mode: String,
    pub formula_mode: String,
    pub external_links: String,
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
            limits: ParseLimits::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParseLimits {
    pub max_input_bytes: u64,
    pub max_parts: usize,
    pub max_part_bytes: u64,
    pub max_uncompressed_bytes: u64,
    pub max_compression_ratio: u64,
    pub max_cells: usize,
    pub max_shapes: usize,
    pub max_text_chars: usize,
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
        let mut events = Vec::new();
        events.push(json!({
            "event": "document_start",
            "schema_version": self.schema_version,
            "format": self.format,
            "coordinate_space": self.coordinate_space,
            "source_name": self.source_name,
            "source_hash": self.source_hash,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "unit_count": self.units.len(),
        }));
        for unit in self.units {
            events.push(json!({"event": "unit", "unit": unit}));
        }
        for asset in self.assets {
            events.push(json!({"event": "asset", "asset": asset}));
        }
        events.push(json!({"event": "document_end", "status": "ok"}));
        events
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
        "security": {"network": false, "external_processes": false, "macros_executed": false, "encrypted_documents": false}
    })
}

pub fn layout_schema_value() -> Value {
    json!({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:rust-office:office-layout-v1",
        "title": "Office Layout IR event",
        "oneOf": [
            {
                "type": "object",
                "required": ["event", "schema_version", "format", "coordinate_space", "source_hash"],
                "properties": {
                    "event": {"const": "document_start"},
                    "schema_version": {"const": OFFICE_LAYOUT_SCHEMA_VERSION},
                    "format": {"enum": ["docx", "xls", "xlsx", "pptx"]},
                    "coordinate_space": {"enum": ["logical_flow", "cell_grid", "slide_points"]},
                    "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}
                }
            },
            {
                "type": "object",
                "required": ["event", "unit"],
                "properties": {
                    "event": {"const": "unit"},
                    "unit": {"type": "object", "required": ["id", "number", "kind", "blocks", "tables"]}
                }
            },
            {
                "type": "object",
                "required": ["event", "asset"],
                "properties": {
                    "event": {"const": "asset"},
                    "asset": {"type": "object", "required": ["id", "mime_type", "sha256", "source_ref"]}
                }
            },
            {
                "type": "object",
                "required": ["event", "status"],
                "properties": {"event": {"const": "document_end"}, "status": {"const": "ok"}}
            }
        ]
    })
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
