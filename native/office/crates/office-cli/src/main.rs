use base64::Engine as _;
use clap::{Args as ClapArgs, Parser, Subcommand, ValueEnum};
use office_core::{
    OfficeDocument, OfficeError, OfficeFormat, ParseOptions, Result, capabilities_value,
    detect_format, layout_schema_value, read_input,
};
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Parser)]
#[command(
    name = "rust-office",
    version,
    about = "Extract Office documents using office-layout-v1"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Capabilities,
    Schema,
    Detect {
        path: PathBuf,
    },
    Parse {
        path: PathBuf,
        #[command(flatten)]
        options: Options,
        #[command(flatten)]
        output: Output,
    },
    Batch {
        directory: PathBuf,
        #[arg(long)]
        output_dir: PathBuf,
        #[arg(long)]
        recursive: bool,
        #[command(flatten)]
        options: Options,
        #[arg(long, value_enum, default_value = "jsonl")]
        format: OutputFormat,
        #[arg(long)]
        export_assets: bool,
    },
    Benchmark {
        path: PathBuf,
        #[arg(long, default_value_t = 5)]
        iterations: u32,
        #[command(flatten)]
        options: Options,
    },
}
#[derive(ClapArgs, Default)]
struct Options {
    /// JSON object using the same options and limits as the Python API.
    #[arg(long)]
    options_json: Option<String>,
    #[arg(long)]
    include_hidden: bool,
    #[arg(long)]
    no_images: bool,
    #[arg(long)]
    sheet: Vec<String>,
    #[arg(long)]
    range: Option<String>,
    #[arg(long)]
    batch_rows: Option<usize>,
    #[arg(long)]
    sparse: bool,
    #[arg(long)]
    asset_mode: Option<String>,
    #[arg(long)]
    revision_mode: Option<String>,
    #[arg(long)]
    formula_mode: Option<String>,
    #[arg(long)]
    external_links: Option<String>,
    #[arg(long)]
    max_input_bytes: Option<u64>,
    #[arg(long)]
    max_cells: Option<usize>,
    #[arg(long)]
    max_text_chars: Option<usize>,
}
impl Options {
    fn parse(&self) -> Result<ParseOptions> {
        let mut o = ParseOptions::from_json(self.options_json.as_deref().unwrap_or("{}"))?;
        if self.include_hidden {
            o.include_hidden = true;
        }
        if self.no_images {
            o.extract_images = false;
        }
        if self.sparse {
            o.sparse = true;
        }
        if !self.sheet.is_empty() {
            o.sheets = self.sheet.clone();
        }
        if let Some(v) = &self.range {
            o.cell_range = Some(v.clone());
        }
        if let Some(v) = self.batch_rows {
            o.batch_rows = v;
        }
        if let Some(v) = &self.asset_mode {
            o.asset_mode = v.clone();
        }
        if let Some(v) = &self.revision_mode {
            o.revision_mode = v.clone();
        }
        if let Some(v) = &self.formula_mode {
            o.formula_mode = v.clone();
        }
        if let Some(v) = &self.external_links {
            o.external_links = v.clone();
        }
        if let Some(v) = self.max_input_bytes {
            o.limits.max_input_bytes = v;
        }
        if let Some(v) = self.max_cells {
            o.limits.max_cells = v;
        }
        if let Some(v) = self.max_text_chars {
            o.limits.max_text_chars = v;
        }
        o.validate()?;
        Ok(o)
    }
}
#[derive(Clone, Copy, ValueEnum)]
enum OutputFormat {
    Jsonl,
    Markdown,
    Csv,
}
impl OutputFormat {
    fn extension(self) -> &'static str {
        match self {
            Self::Jsonl => "jsonl",
            Self::Markdown => "md",
            Self::Csv => "csv",
        }
    }
}
#[derive(ClapArgs)]
struct Output {
    #[arg(long, value_enum, default_value = "jsonl")]
    format: OutputFormat,
    #[arg(long)]
    output: Option<PathBuf>,
    #[arg(long)]
    assets_dir: Option<PathBuf>,
}
fn io_error(e: io::Error) -> OfficeError {
    OfficeError::new("OFFICE_IO_FAILED", e.to_string(), true)
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{}", error.to_value());
        std::process::exit(2);
    }
}
fn run() -> Result<()> {
    match Args::parse().command {
        Command::Capabilities => println!(
            "{}",
            serde_json::to_string_pretty(&capabilities_value()).unwrap()
        ),
        Command::Schema => println!(
            "{}",
            serde_json::to_string_pretty(&layout_schema_value()).unwrap()
        ),
        Command::Detect { path } => {
            let bytes = read_input(&path, &Default::default())?;
            println!(
                "{}",
                serde_json::json!({"format":detect_format(&path,&bytes)?.as_str()})
            );
        }
        Command::Parse {
            path,
            options,
            output,
        } => {
            let options = options.parse()?;
            let bytes = read_input(&path, &options.limits)?;
            let doc = parse_document(&path, &bytes, &options)?;
            if let Some(dir) = &output.assets_dir {
                export_assets(&doc, dir)?;
            }
            write_document(doc, output.format, output.output.as_deref())?;
        }
        Command::Batch {
            directory,
            output_dir,
            recursive,
            options,
            format,
            export_assets: assets,
        } => {
            let options = options.parse()?;
            let mut paths = Vec::new();
            collect(&directory, recursive, &mut paths)?;
            paths.sort();
            fs::create_dir_all(&output_dir).map_err(io_error)?;
            let mut failures = Vec::new();
            let mut success = 0;
            for path in paths {
                let relative = path
                    .strip_prefix(&directory)
                    .map_err(|e| OfficeError::new("OFFICE_IO_FAILED", e.to_string(), true))?;
                let target = output_dir.join(relative).with_extension(format!(
                    "{}.{}",
                    path.extension()
                        .and_then(|s| s.to_str())
                        .unwrap_or("office"),
                    format.extension()
                ));
                let result = (|| {
                    let bytes = read_input(&path, &options.limits)?;
                    let doc = parse_document(&path, &bytes, &options)?;
                    if let Some(parent) = target.parent() {
                        fs::create_dir_all(parent).map_err(io_error)?;
                    }
                    if assets {
                        export_assets(&doc, &target.with_extension("assets"))?;
                    }
                    write_document(doc, format, Some(&target))
                })();
                match result {
                    Ok(()) => success += 1,
                    Err(e) => failures.push(serde_json::json!({"path":path,"error":e.to_value()})),
                }
            }
            let report = serde_json::json!({"succeeded":success,"failed":failures.len(),"failures":failures});
            fs::write(
                output_dir.join("batch-report.json"),
                serde_json::to_vec_pretty(&report).unwrap(),
            )
            .map_err(io_error)?;
            println!("{report}");
            if !failures.is_empty() {
                return Err(OfficeError::new(
                    "BATCH_INCOMPLETE",
                    "One or more files failed; see batch-report.json.",
                    true,
                ));
            }
        }
        Command::Benchmark {
            path,
            iterations,
            options,
        } => {
            let options = options.parse()?;
            let bytes = read_input(&path, &options.limits)?;
            let iterations = iterations.max(1);
            let start = Instant::now();
            let mut units = 0;
            for _ in 0..iterations {
                units = parse_document(&path, &bytes, &options)?.units.len();
            }
            println!(
                "{}",
                serde_json::json!({"path":path,"iterations":iterations,"unit_count":units,"input_bytes":bytes.len(),"mean_ms":start.elapsed().as_secs_f64()*1000.0/f64::from(iterations)})
            );
        }
    }
    Ok(())
}
fn parse_document(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument> {
    match detect_format(path, bytes)? {
        OfficeFormat::Docx => office_word::parse_docx(path, bytes, options),
        OfficeFormat::Xls => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xls, options)
        }
        OfficeFormat::Xlsx => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xlsx, options)
        }
        OfficeFormat::Pptx => office_slide::parse_pptx(path, bytes, options),
        _ => unreachable!(),
    }
}
fn collect(dir: &Path, recursive: bool, out: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(dir).map_err(io_error)? {
        let entry = entry.map_err(io_error)?;
        let kind = entry.file_type().map_err(io_error)?;
        if kind.is_symlink() {
            continue;
        }
        if kind.is_dir() && recursive {
            collect(&entry.path(), true, out)?;
        } else if kind.is_file()
            && entry
                .path()
                .extension()
                .and_then(|s| s.to_str())
                .is_some_and(|s| {
                    matches!(
                        s.to_ascii_lowercase().as_str(),
                        "docx" | "xlsx" | "xls" | "pptx" | "doc" | "ppt" | "docm" | "xlsm" | "pptm"
                    )
                })
        {
            out.push(entry.path());
        }
    }
    Ok(())
}
fn write_document(doc: OfficeDocument, format: OutputFormat, path: Option<&Path>) -> Result<()> {
    // Parsing has already succeeded before opening any output file.
    let mut out: Box<dyn Write> = if let Some(path) = path {
        Box::new(fs::File::create(path).map_err(io_error)?)
    } else {
        Box::new(io::stdout().lock())
    };
    match format {
        OutputFormat::Jsonl => {
            for event in doc.into_event_iter() {
                writeln!(out, "{event}").map_err(io_error)?;
            }
        }
        OutputFormat::Markdown => {
            for unit in &doc.units {
                writeln!(out, "# {}\n", md(&unit.label)).map_err(io_error)?;
                for block in &unit.blocks {
                    if block.kind == "table" {
                        if let Some(table) = unit.tables.iter().find(|t| t.block_id == block.id) {
                            if let Some(first) = table.rows.first() {
                                let width = table.rows.iter().map(Vec::len).max().unwrap_or(0);
                                writeln!(
                                    out,
                                    "| {} |",
                                    (0..width)
                                        .map(|i| md(first
                                            .get(i)
                                            .and_then(|s| s.as_deref())
                                            .unwrap_or("")))
                                        .collect::<Vec<_>>()
                                        .join(" | ")
                                )
                                .map_err(io_error)?;
                                writeln!(out, "| {} |", vec!["---"; width].join(" | "))
                                    .map_err(io_error)?;
                                for row in &table.rows[1..] {
                                    writeln!(
                                        out,
                                        "| {} |",
                                        (0..width)
                                            .map(|i| md(row
                                                .get(i)
                                                .and_then(|s| s.as_deref())
                                                .unwrap_or("")))
                                            .collect::<Vec<_>>()
                                            .join(" | ")
                                    )
                                    .map_err(io_error)?;
                                }
                            } else {
                                writeln!(out, "{}", md(&block.text)).map_err(io_error)?;
                            }
                        }
                    } else if block.kind == "heading" {
                        writeln!(out, "## {}", md(&block.text)).map_err(io_error)?;
                    } else {
                        writeln!(out, "{}", md(&block.text)).map_err(io_error)?;
                    }
                    writeln!(out).map_err(io_error)?;
                }
            }
        }
        OutputFormat::Csv => {
            writeln!(out, "unit,table,row,column,value").map_err(io_error)?;
            for unit in &doc.units {
                for block in &unit.blocks {
                    if let Some(records) = block
                        .metadata
                        .get("cell_records")
                        .and_then(|v| v.as_array())
                    {
                        for r in records {
                            writeln!(
                                out,
                                "{},{},{},{},{}",
                                csv(&unit.label),
                                csv(&block.id),
                                r["row"],
                                r["column"],
                                csv(r["value"].as_str().unwrap_or(""))
                            )
                            .map_err(io_error)?;
                        }
                    } else if let Some(table) = unit.tables.iter().find(|t| t.block_id == block.id)
                    {
                        for (r, row) in table.rows.iter().enumerate() {
                            for (c, value) in row.iter().enumerate() {
                                writeln!(
                                    out,
                                    "{},{},{},{},{}",
                                    csv(&unit.label),
                                    csv(&block.id),
                                    r + 1,
                                    c + 1,
                                    csv(value.as_deref().unwrap_or(""))
                                )
                                .map_err(io_error)?;
                            }
                        }
                    }
                }
            }
        }
    }
    out.flush().map_err(io_error)
}
fn md(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('|', "\\|")
        .replace('\n', "<br>")
}
fn csv(text: &str) -> String {
    format!("\"{}\"", text.replace('"', "\"\""))
}
fn export_assets(doc: &OfficeDocument, dir: &Path) -> Result<()> {
    if doc
        .assets
        .iter()
        .any(|a| a.metadata.get("data_omitted").and_then(|v| v.as_bool()) == Some(true))
    {
        return Err(OfficeError::new(
            "INVALID_OPTIONS",
            "Asset export requires asset_mode=inline.",
            true,
        ));
    }
    fs::create_dir_all(dir).map_err(io_error)?;
    let mut manifest = Vec::new();
    for asset in &doc.assets {
        let extension = Path::new(&asset.file_name)
            .extension()
            .and_then(|s| s.to_str())
            .filter(|s| s.bytes().all(|c| c.is_ascii_alphanumeric()))
            .unwrap_or("bin");
        let filename = format!("{}.{}", asset.sha256, extension);
        let data = base64::engine::general_purpose::STANDARD
            .decode(&asset.data_base64)
            .map_err(|e| OfficeError::new("INVALID_ASSET", e.to_string(), false))?;
        fs::write(dir.join(&filename), data).map_err(io_error)?;
        manifest.push(serde_json::json!({"id":asset.id,"file":filename,"source_ref":asset.source_ref,"sha256":asset.sha256,"alt_text":asset.alt_text}));
    }
    fs::write(
        dir.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .map_err(io_error)
}
