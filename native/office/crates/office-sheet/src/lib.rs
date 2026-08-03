use std::collections::BTreeMap;
use std::io::{Cursor, Read};
use std::path::Path;

use calamine::{Data, Reader, SheetVisible, open_workbook_auto};
use office_core::{
    Block, LogicalUnit, OfficeDocument, OfficeError, OfficeFormat, ParseOptions, Result, Table,
    Warning,
};
use office_ooxml::{OoxmlPackage, attr, local_name, xml_error};
use quick_xml::Reader as XmlReader;
use quick_xml::events::Event;
use serde_json::{Value, json};

pub fn parse_spreadsheet(
    path: &Path,
    bytes: &[u8],
    format: OfficeFormat,
    options: &ParseOptions,
) -> Result<OfficeDocument> {
    if format == OfficeFormat::Xls {
        require_biff8(path)?;
    }
    let mut workbook = open_workbook_auto(path).map_err(|error| {
        OfficeError::new(
            "SPREADSHEET_OPEN_FAILED",
            format!("Unable to open spreadsheet: {error}"),
            false,
        )
    })?;
    let mut document = OfficeDocument::new(format, path, bytes, "cell_grid");
    document
        .metadata
        .insert("formula_mode".into(), json!(options.formula_mode));
    if format == OfficeFormat::Xls {
        document
            .metadata
            .insert("xls_variant".into(), json!("BIFF8"));
    }

    let metadata = workbook.sheets_metadata().to_vec();
    let xlsx_package = if format == OfficeFormat::Xlsx {
        Some(OoxmlPackage::open(bytes, &options.limits)?)
    } else {
        None
    };
    let mut total_cells = 0_usize;
    for (index, sheet) in metadata.iter().enumerate() {
        let hidden = sheet.visible != SheetVisible::Visible;
        if hidden && !options.include_hidden {
            continue;
        }
        let range = workbook.worksheet_range(&sheet.name).map_err(|error| {
            OfficeError::new(
                "WORKSHEET_PARSE_FAILED",
                format!("Unable to parse worksheet {}: {error:?}", sheet.name),
                false,
            )
        })?;
        let formulas = workbook.worksheet_formula(&sheet.name).ok();
        let start = range.start().unwrap_or((0, 0));
        let (height, width) = range.get_size();
        total_cells = total_cells.saturating_add(height.saturating_mul(width));
        if total_cells > options.limits.max_cells {
            return Err(OfficeError::new(
                "PARSE_LIMIT_EXCEEDED",
                "Spreadsheet cell count exceeds max_cells.",
                false,
            ));
        }

        let mut unit = LogicalUnit::new(
            format!("sheet_{}", index + 1),
            (index + 1) as u32,
            &sheet.name,
            "worksheet",
        );
        unit.width = width as f64;
        unit.height = height as f64;
        unit.hidden = hidden;
        unit.metadata
            .insert("sheet_type".into(), json!(format!("{:?}", sheet.typ)));
        unit.metadata.insert(
            "visibility".into(),
            json!(format!("{:?}", sheet.visible).to_ascii_lowercase()),
        );
        unit.metadata
            .insert("page_ref_kind".into(), json!("worksheet"));

        let mut rows: Vec<Vec<Option<String>>> = Vec::new();
        let mut cells: Vec<Value> = Vec::new();
        for (relative_row, row) in range.rows().enumerate() {
            let mut output_row = Vec::with_capacity(row.len());
            for (relative_col, cell) in row.iter().enumerate() {
                let absolute = (start.0 + relative_row as u32, start.1 + relative_col as u32);
                let value = (!matches!(cell, Data::Empty)).then(|| cell.to_string());
                let formula = formulas
                    .as_ref()
                    .and_then(|items| items.get_value(absolute))
                    .filter(|value| !value.is_empty())
                    .cloned();
                if value.is_some() || formula.is_some() {
                    cells.push(json!({
                        "address": a1(absolute.0, absolute.1),
                        "row": absolute.0 + 1,
                        "column": absolute.1 + 1,
                        "data_type": data_type(cell),
                        "value": value,
                        "formula": formula,
                    }));
                }
                output_row.push(value);
            }
            rows.push(output_row);
        }

        let block_id = format!("sheet{}_region1", index + 1);
        let text = rows
            .iter()
            .map(|row| {
                row.iter()
                    .map(|cell| cell.as_deref().unwrap_or(""))
                    .collect::<Vec<_>>()
                    .join(" | ")
            })
            .collect::<Vec<_>>()
            .join("\n");
        let source_ref = format!(
            "{}:sheet:{}:range:{}",
            format.as_str(),
            sheet.name,
            range_label(start, height, width)
        );
        let mut block = Block::new(&block_id, "table", text, 1, &source_ref);
        block.metadata.insert("worksheet".into(), json!(sheet.name));
        block.metadata.insert("cell_records".into(), json!(cells));
        block
            .metadata
            .insert("page_ref_kind".into(), json!("worksheet"));

        let mut table_metadata = BTreeMap::new();
        table_metadata.insert("worksheet".into(), json!(sheet.name));
        table_metadata.insert("range".into(), json!(range_label(start, height, width)));
        if let Some(package) = xlsx_package.as_ref() {
            let worksheet_part = format!("xl/worksheets/sheet{}.xml", index + 1);
            if let Some(xml) = package.get_optional(&worksheet_part) {
                let supplemental = parse_xlsx_sheet_metadata(xml)?;
                block
                    .metadata
                    .insert("merged_ranges".into(), json!(supplemental.merged_ranges));
                block
                    .metadata
                    .insert("hyperlink_refs".into(), json!(supplemental.hyperlink_refs));
                table_metadata.insert("merged_ranges".into(), json!(supplemental.merged_ranges));
            }
        }
        unit.blocks.push(block);
        unit.tables.push(Table {
            id: format!("table_{block_id}"),
            block_id,
            rows,
            source_refs: vec![source_ref],
            metadata: table_metadata,
        });
        document.units.push(unit);
    }

    if let Some(package) = xlsx_package {
        if options.extract_images {
            document.assets = package.media_assets("xl/media/");
        }
        let chart_count = package
            .names_with_prefix("xl/charts/")
            .filter(|name| name.ends_with(".xml"))
            .count();
        document
            .metadata
            .insert("chart_inventory_count".into(), json!(chart_count));
        if chart_count > 0 {
            document.warnings.push(Warning {
                code: "CHARTS_METADATA_ONLY".into(),
                message:
                    "Spreadsheet charts are inventoried but not rendered or semantically evaluated."
                        .into(),
                context: BTreeMap::from([("count".into(), json!(chart_count))]),
            });
        }
    }
    Ok(document)
}

fn require_biff8(path: &Path) -> Result<()> {
    let mut compound = cfb::open(path).map_err(|error| {
        OfficeError::new(
            "XLS_CFB_OPEN_FAILED",
            format!("Unable to open XLS compound file: {error}"),
            false,
        )
    })?;
    let stream_name = if compound.exists("/Workbook") {
        "/Workbook"
    } else if compound.exists("/Book") {
        "/Book"
    } else {
        return Err(OfficeError::new(
            "XLS_WORKBOOK_STREAM_MISSING",
            "XLS file has no Workbook stream.",
            false,
        ));
    };
    let mut stream = compound.open_stream(stream_name).map_err(|error| {
        OfficeError::new("XLS_WORKBOOK_STREAM_MISSING", error.to_string(), false)
    })?;
    let mut header = [0_u8; 8];
    stream
        .read_exact(&mut header)
        .map_err(|error| OfficeError::new("XLS_BIFF_HEADER_INVALID", error.to_string(), false))?;
    let record_id = u16::from_le_bytes([header[0], header[1]]);
    let version = u16::from_le_bytes([header[4], header[5]]);
    if record_id != 0x0809 || version != 0x0600 {
        return Err(OfficeError::new(
            "XLS_BIFF_VERSION_NOT_SUPPORTED",
            format!("Only BIFF8 is supported; record=0x{record_id:04x}, version=0x{version:04x}."),
            true,
        ));
    }
    Ok(())
}

#[derive(Default)]
struct XlsxSheetMetadata {
    merged_ranges: Vec<String>,
    hyperlink_refs: Vec<String>,
}

fn parse_xlsx_sheet_metadata(bytes: &[u8]) -> Result<XlsxSheetMetadata> {
    let mut reader = XmlReader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    let mut metadata = XlsxSheetMetadata::default();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Empty(element)) | Ok(Event::Start(element)) => {
                match local_name(element.name().as_ref()) {
                    b"mergeCell" => {
                        if let Some(value) = attr(&reader, &element, b"ref") {
                            metadata.merged_ranges.push(value);
                        }
                    }
                    b"hyperlink" => {
                        if let Some(value) = attr(&reader, &element, b"ref") {
                            metadata.hyperlink_refs.push(value);
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok(metadata)
}

fn data_type(value: &Data) -> &'static str {
    match value {
        Data::Int(_) => "integer",
        Data::Float(_) => "float",
        Data::String(_) => "string",
        Data::Bool(_) => "boolean",
        Data::DateTime(_) | Data::DateTimeIso(_) => "datetime",
        Data::DurationIso(_) => "duration",
        Data::Error(_) => "error",
        Data::Empty => "empty",
    }
}

fn range_label(start: (u32, u32), height: usize, width: usize) -> String {
    if height == 0 || width == 0 {
        return "empty".into();
    }
    format!(
        "{}:{}",
        a1(start.0, start.1),
        a1(start.0 + height as u32 - 1, start.1 + width as u32 - 1)
    )
}

fn a1(row: u32, column: u32) -> String {
    let mut n = column + 1;
    let mut letters = String::new();
    while n > 0 {
        let remainder = ((n - 1) % 26) as u8;
        letters.insert(0, (b'A' + remainder) as char);
        n = (n - 1) / 26;
    }
    format!("{letters}{}", row + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cell_addresses_are_stable() {
        assert_eq!(a1(0, 0), "A1");
        assert_eq!(a1(9, 26), "AA10");
    }
}
