use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use clap::{Parser, Subcommand};
use office_core::{
    OfficeDocument, OfficeFormat, ParseOptions, capabilities_value, detect_format,
    layout_schema_value,
};

#[derive(Parser)]
#[command(
    name = "rust-office",
    version,
    about = "Inspect Office documents using office-layout-v1"
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
        #[arg(long)]
        include_hidden: bool,
        #[arg(long)]
        no_images: bool,
    },
    Benchmark {
        path: PathBuf,
        #[arg(long, default_value_t = 5)]
        iterations: u32,
    },
}

fn main() {
    if let Err(error) = run() {
        println!("{}", error.to_value());
        std::process::exit(2);
    }
}

fn run() -> office_core::Result<()> {
    match Args::parse().command {
        Command::Capabilities => println!(
            "{}",
            serde_json::to_string_pretty(&capabilities_value()).expect("capabilities serialize")
        ),
        Command::Schema => println!(
            "{}",
            serde_json::to_string_pretty(&layout_schema_value()).expect("schema serialize")
        ),
        Command::Detect { path } => {
            let bytes = read(&path)?;
            println!(
                "{}",
                serde_json::json!({"format": detect_format(&path, &bytes)?.as_str()})
            );
        }
        Command::Parse {
            path,
            include_hidden,
            no_images,
        } => {
            let bytes = read(&path)?;
            let options = ParseOptions {
                include_hidden,
                extract_images: !no_images,
                ..ParseOptions::default()
            };
            let document = parse_document(&path, &bytes, &options)?;
            for event in document.into_events() {
                println!("{}", event);
            }
        }
        Command::Benchmark { path, iterations } => {
            let bytes = read(&path)?;
            let iterations = iterations.max(1);
            let options = ParseOptions::default();
            let start = Instant::now();
            let mut unit_count = 0;
            for _ in 0..iterations {
                unit_count = parse_document(&path, &bytes, &options)?.units.len();
            }
            let elapsed = start.elapsed();
            println!(
                "{}",
                serde_json::json!({
                    "path": path,
                    "iterations": iterations,
                    "input_bytes": bytes.len(),
                    "unit_count": unit_count,
                    "elapsed_ms": elapsed.as_secs_f64() * 1000.0,
                    "mean_ms": elapsed.as_secs_f64() * 1000.0 / f64::from(iterations)
                })
            );
        }
    }
    Ok(())
}

fn parse_document(
    path: &Path,
    bytes: &[u8],
    options: &ParseOptions,
) -> office_core::Result<OfficeDocument> {
    match detect_format(path, bytes)? {
        OfficeFormat::Docx => office_word::parse_docx(path, bytes, options),
        OfficeFormat::Xls => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xls, options)
        }
        OfficeFormat::Xlsx => {
            office_sheet::parse_spreadsheet(path, bytes, OfficeFormat::Xlsx, options)
        }
        OfficeFormat::Pptx => office_slide::parse_pptx(path, bytes, options),
        OfficeFormat::Doc | OfficeFormat::Ppt => {
            unreachable!("legacy formats rejected during detection")
        }
    }
}

fn read(path: &Path) -> office_core::Result<Vec<u8>> {
    fs::read(path).map_err(|error| {
        office_core::OfficeError::new(
            "OFFICE_OPEN_FAILED",
            format!("Unable to read {}: {error}", path.display()),
            true,
        )
    })
}
