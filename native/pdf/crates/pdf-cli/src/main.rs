use std::{path::PathBuf, process::ExitCode};

use clap::{Parser, Subcommand, ValueEnum};
use pdf_core::{
    ErrorCode, ExtractionMode, LayoutExtractionOptions, ObjectId, ParseLimits, PdfDocument,
    PdfError, PdfName, PdfObject, PdfResult, TextExtractionOptions, TextExtractionOptionsV2,
    XrefKind,
};
use serde_json::{Value, json};

#[derive(Debug, Parser)]
#[command(name = "rust-pdf", version, about = "From-scratch Rust PDF parser")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Print workspace version and implementation stage.
    Version {
        /// Emit JSON instead of plain text.
        #[arg(long)]
        json: bool,
    },
    /// Inspect the header, trailer, and cross-reference summary.
    Inspect {
        /// PDF file to inspect.
        file: PathBuf,
        /// Emit JSON instead of plain text.
        #[arg(long)]
        json: bool,
    },
    /// Print canonical page boxes and reversible coordinate transforms.
    Geometry {
        /// PDF file whose page geometry should be inspected.
        file: PathBuf,
        /// Emit JSON instead of plain text.
        #[arg(long)]
        json: bool,
    },
    /// Build versioned coordinate-normalized Layout IR.
    Layout {
        /// PDF file to extract into Layout IR.
        file: PathBuf,
        /// Emit the full JSON schema instead of a summary.
        #[arg(long)]
        json: bool,
        /// Apply Unicode NFC normalization after font/CMap decoding.
        #[arg(long)]
        normalize_unicode: bool,
        /// Include debug-only projected glyphs.
        #[arg(long)]
        debug_glyphs: bool,
        /// Include non-deterministic layout timing data.
        #[arg(long)]
        timings: bool,
    },
    /// Resolve and print one indirect object.
    Object {
        /// PDF file to inspect.
        file: PathBuf,
        /// Object number.
        number: u32,
        /// Generation number.
        #[arg(long, default_value_t = 0)]
        generation: u16,
        /// Emit JSON instead of debug text.
        #[arg(long)]
        json: bool,
    },
    /// Extract Unicode text without rendering pages.
    Extract {
        /// PDF file to extract.
        file: PathBuf,
        /// Emit structured spans, pages, and warnings as JSON.
        #[arg(long)]
        json: bool,
        /// Apply Unicode NFC normalization after font/CMap decoding.
        #[arg(long)]
        normalize_unicode: bool,
        /// Select V2 extraction order and separator behavior.
        #[arg(long, value_enum, conflicts_with = "no_layout")]
        mode: Option<CliExtractionMode>,
        /// Preserve content-stream order instead of approximate visual line order.
        #[arg(long)]
        no_layout: bool,
    },
    /// Parse every in-use xref object and validate the catalog.
    Validate {
        /// PDF file to validate.
        file: PathBuf,
        /// Emit JSON instead of plain text.
        #[arg(long)]
        json: bool,
        /// Emit document decode/cache metrics (stderr in plain mode).
        #[arg(long)]
        diagnostics: bool,
    },
}

#[derive(Debug, Clone, Copy, ValueEnum)]
enum CliExtractionMode {
    ContentOrder,
    Layout,
    Auto,
}

impl From<CliExtractionMode> for ExtractionMode {
    fn from(mode: CliExtractionMode) -> Self {
        match mode {
            CliExtractionMode::ContentOrder => Self::ContentOrder,
            CliExtractionMode::Layout => Self::Layout,
            CliExtractionMode::Auto => Self::Auto,
        }
    }
}

fn main() -> ExitCode {
    match run(Cli::parse()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!(
                "{}",
                json!({
                    "ok": false,
                    "error": {
                        "code": error.code.as_str(),
                        "offset": error.offset,
                        "message": error.message,
                    }
                })
            );
            ExitCode::from(exit_code(error.code))
        }
    }
}

fn run(cli: Cli) -> PdfResult<()> {
    match cli.command.unwrap_or(Command::Version { json: false }) {
        Command::Version { json: use_json } => {
            print_version(use_json);
            Ok(())
        }
        Command::Inspect { file, json } => print_inspection(&file, json),
        Command::Geometry {
            file,
            json: use_json,
        } => print_geometry(&file, use_json),
        Command::Layout {
            file,
            json: use_json,
            normalize_unicode,
            debug_glyphs,
            timings,
        } => print_layout(
            &file,
            use_json,
            LayoutExtractionOptions {
                normalize_unicode,
                include_quality_metadata: true,
                include_debug_glyphs: debug_glyphs,
                include_timings: timings,
            },
        ),
        Command::Object {
            file,
            number,
            generation,
            json: use_json,
        } => {
            let document = open_document(&file)?;
            let object = document.object(ObjectId::new(number, generation))?;
            if use_json {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&object_to_json(&object.value))
                        .map_err(|error| serialization_error(&error))?
                );
            } else {
                println!("{:#?}", object.value);
            }
            Ok(())
        }
        Command::Extract {
            file,
            json: use_json,
            normalize_unicode,
            mode,
            no_layout,
        } => {
            let document = open_document(&file)?;
            if let Some(mode) = mode {
                let extracted = document.extract_text_v2(TextExtractionOptionsV2 {
                    normalize_unicode,
                    mode: mode.into(),
                    include_quality_metadata: true,
                })?;
                if use_json {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&extracted)
                            .map_err(|error| serialization_error(&error))?
                    );
                } else {
                    println!("{}", extracted.text);
                    print_text_warnings(extracted.warnings);
                }
            } else {
                let extracted = document.extract_text(TextExtractionOptions {
                    normalize_unicode,
                    layout: !no_layout,
                })?;
                if use_json {
                    println!(
                        "{}",
                        serde_json::to_string_pretty(&extracted)
                            .map_err(|error| serialization_error(&error))?
                    );
                } else {
                    println!("{}", extracted.text);
                    print_text_warnings(extracted.warnings);
                }
            }
            Ok(())
        }
        Command::Validate {
            file,
            json: use_json,
            diagnostics,
        } => validate_document(&file, use_json, diagnostics),
    }
}

fn print_inspection(file: &PathBuf, use_json: bool) -> PdfResult<()> {
    let document = open_document(file)?;
    let summary = document.summary()?;
    if use_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&summary).map_err(|error| serialization_error(&error))?
        );
    } else {
        println!(
            "PDF {}.{}: {} bytes, {} in-use objects, {} revision(s), root {} {} R",
            summary.version.major,
            summary.version.minor,
            summary.file_bytes,
            summary.in_use_objects,
            summary.revisions,
            summary.root.number,
            summary.root.generation
        );
    }
    Ok(())
}
fn print_geometry(file: &PathBuf, use_json: bool) -> PdfResult<()> {
    let document = open_document(file)?;
    let pages = document.pages()?;
    if use_json {
        let page_values = pages
            .iter()
            .map(|page| {
                json!({
                    "page_index": page.index,
                    "page_number": page.index + 1,
                    "object": {
                        "number": page.id.number,
                        "generation": page.id.generation,
                    },
                    "geometry": &page.geometry,
                })
            })
            .collect::<Vec<_>>();
        println!(
            "{}",
            serde_json::to_string_pretty(&json!({
                "coordinate_space": pdf_core::LAYOUT_SPACE,
                "pages": page_values,
            }))
            .map_err(|error| serialization_error(&error))?
        );
    } else {
        for page in pages {
            println!(
                "page {}: layout {:.3} x {:.3} pt, display {:.3} x {:.3} pt, rotate {}",
                page.index + 1,
                page.geometry.layout_bounds.width(),
                page.geometry.layout_bounds.height(),
                page.geometry.display_bounds.width(),
                page.geometry.display_bounds.height(),
                page.geometry.rotation,
            );
        }
    }
    Ok(())
}

fn print_layout(file: &PathBuf, use_json: bool, options: LayoutExtractionOptions) -> PdfResult<()> {
    let document = open_document(file)?;
    let layout = document.extract_layout(options)?;
    if use_json {
        println!(
            "{}",
            serde_json::to_string_pretty(&layout).map_err(|error| serialization_error(&error))?
        );
    } else {
        let node_count = layout
            .pages
            .iter()
            .map(|page| page.semantic_nodes.len())
            .sum::<usize>();
        let span_count = layout
            .pages
            .iter()
            .flat_map(|page| &page.semantic_nodes)
            .map(|node| node.spans.len())
            .sum::<usize>();
        println!(
            "Layout IR v{}: {} page(s), {node_count} node(s), {span_count} span(s), {}",
            layout.schema_version,
            layout.pages.len(),
            pdf_core::layout_coordinate_space(),
        );
    }
    Ok(())
}

fn print_text_warnings(warnings: Vec<pdf_core::TextWarning>) {
    for warning in warnings {
        eprintln!(
            "warning[{}] page {}: {}",
            warning.code,
            warning.page_index + 1,
            warning.message
        );
    }
}

fn validate_document(file: &PathBuf, use_json: bool, diagnostics: bool) -> PdfResult<()> {
    let document = open_document(file)?;
    document.catalog()?;
    let mut validated = 0_usize;
    for (&number, entry) in document.xref_entries() {
        if entry.kind != XrefKind::Free {
            document
                .object(ObjectId::new(number, entry.generation))
                .map_err(|mut error| {
                    error.message = format!(
                        "object {number} {} R ({:?}, stream {:?}, index {:?}): {}",
                        entry.generation,
                        entry.kind,
                        entry.object_stream,
                        entry.object_index,
                        error.message
                    );
                    error
                })?;
            validated += 1;
        }
    }
    let metrics = document.decode_metrics();
    if use_json {
        let mut result = json!({"ok": true, "validated_objects": validated});
        if diagnostics {
            result["decode_metrics"] =
                serde_json::to_value(metrics).map_err(|error| serialization_error(&error))?;
        }
        println!("{result}");
    } else {
        println!("valid: parsed {validated} in-use object(s)");
        if diagnostics {
            eprintln!(
                "diagnostics: {}",
                serde_json::to_string(&metrics).map_err(|error| serialization_error(&error))?
            );
        }
    }
    Ok(())
}

fn print_version(use_json: bool) {
    let info = pdf_core::version_info();
    if use_json {
        println!(
            "{}",
            json!({
                "version": info.version,
                "stage": info.stage,
            })
        );
    } else {
        println!("rust-pdf {} ({})", info.version, info.stage);
    }
}

fn open_document(path: &PathBuf) -> PdfResult<PdfDocument> {
    let limits = ParseLimits::default();
    let metadata = std::fs::metadata(path).map_err(|error| {
        PdfError::new(
            ErrorCode::Io,
            None,
            format!("cannot inspect {}: {error}", path.display()),
        )
    })?;
    if metadata.len() > limits.max_file_bytes as u64 {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "input byte limit exceeded before file allocation",
        ));
    }
    let bytes = std::fs::read(path).map_err(|error| {
        PdfError::new(
            ErrorCode::Io,
            None,
            format!("cannot read {}: {error}", path.display()),
        )
    })?;
    PdfDocument::parse_with_limits(&bytes, limits)
}

fn object_to_json(object: &PdfObject) -> Value {
    match object {
        PdfObject::Null => json!({"type": "null"}),
        PdfObject::Boolean(value) => json!({"type": "boolean", "value": value}),
        PdfObject::Integer(value) => json!({"type": "integer", "value": value}),
        PdfObject::Real(value) => json!({"type": "real", "value": value}),
        PdfObject::Name(name) => bytes_json("name", name.as_bytes()),
        PdfObject::String(value) => bytes_json("string", &value.0),
        PdfObject::Array(values) => json!({
            "type": "array",
            "value": values.iter().map(object_to_json).collect::<Vec<_>>(),
        }),
        PdfObject::Dictionary(dictionary) => dictionary_json(dictionary),
        PdfObject::Stream(stream) => json!({
            "type": "stream",
            "dictionary": dictionary_entries_json(&stream.dictionary),
            "data_bytes": stream.data.len(),
            "data_hex": hex(&stream.data),
        }),
        PdfObject::Reference(id) => json!({
            "type": "reference",
            "object": id.number,
            "generation": id.generation,
        }),
    }
}

fn dictionary_json(dictionary: &pdf_core::PdfDictionary) -> Value {
    json!({
        "type": "dictionary",
        "entries": dictionary_entries_json(dictionary),
    })
}

fn dictionary_entries_json(dictionary: &pdf_core::PdfDictionary) -> Vec<Value> {
    dictionary
        .iter()
        .map(|(name, value)| {
            json!({
                "key": name_json(name),
                "value": object_to_json(value),
            })
        })
        .collect()
}

fn name_json(name: &PdfName) -> Value {
    bytes_json("name", name.as_bytes())
}

fn bytes_json(kind: &str, bytes: &[u8]) -> Value {
    json!({
        "type": kind,
        "bytes_hex": hex(bytes),
        "text_lossy": String::from_utf8_lossy(bytes),
    })
}

fn hex(bytes: &[u8]) -> String {
    use std::fmt::Write as _;

    let mut output = String::with_capacity(bytes.len().saturating_mul(2));
    for byte in bytes {
        let _ = write!(output, "{byte:02x}");
    }
    output
}

const fn exit_code(code: ErrorCode) -> u8 {
    match code {
        ErrorCode::Io => 3,
        ErrorCode::LimitExceeded => 4,
        ErrorCode::UnsupportedFeature => 5,
        _ => 2,
    }
}

fn serialization_error(error: &serde_json::Error) -> PdfError {
    PdfError::new(
        ErrorCode::InvalidObject,
        None,
        format!("JSON serialization failed: {error}"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_representation_preserves_non_utf8_bytes() {
        let value = object_to_json(&PdfObject::String(pdf_core::PdfString(vec![0xff, 0x00])));
        assert_eq!(value["bytes_hex"], "ff00");
    }

    #[test]
    fn stable_exit_codes_distinguish_io_limits_and_features() {
        assert_eq!(exit_code(ErrorCode::Io), 3);
        assert_eq!(exit_code(ErrorCode::LimitExceeded), 4);
        assert_eq!(exit_code(ErrorCode::UnsupportedFeature), 5);
    }
}
