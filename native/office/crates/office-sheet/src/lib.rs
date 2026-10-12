use calamine::{Data, Reader, SheetVisible, Xls};
use office_core::{
    Block, LogicalUnit, OfficeDocument, OfficeError, OfficeFormat, ParseBudget, ParseOptions,
    Result, Table, Warning, parse_cell_range,
};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Cursor, Read};
use std::path::Path;
mod formatting;
mod xlsx;

pub fn parse_spreadsheet(
    path: &Path,
    bytes: &[u8],
    format: OfficeFormat,
    options: &ParseOptions,
) -> Result<OfficeDocument> {
    options.validate()?;
    if bytes.len() as u64 > options.limits.max_input_bytes {
        return Err(OfficeError::new(
            "INPUT_LIMIT_EXCEEDED",
            "Spreadsheet exceeds max_input_bytes.",
            false,
        ));
    }
    if format == OfficeFormat::Xlsx {
        return xlsx::parse(path, bytes, options);
    }
    if format != OfficeFormat::Xls {
        return Err(OfficeError::new(
            "UNSUPPORTED_OFFICE_FORMAT",
            "Expected XLS or XLSX.",
            true,
        ));
    }
    let biff = read_biff(bytes, options)?;
    let mut workbook = Xls::new(Cursor::new(bytes))
        .map_err(|e| OfficeError::new("SPREADSHEET_OPEN_FAILED", e.to_string(), false))?;
    let mut document = OfficeDocument::new(format, path, bytes, "cell_grid");
    document
        .metadata
        .insert("xls_variant".into(), json!("BIFF8"));
    document
        .metadata
        .insert("formula_mode".into(), json!(options.formula_mode));
    document
        .metadata
        .insert("defined_names".into(), json!(workbook.defined_names()));
    let metadata = workbook.sheets_metadata().to_vec();
    validate_sheets(options, metadata.iter().map(|s| s.name.as_str()))?;
    let mut budget = ParseBudget::default();
    for (index, sheet) in metadata.iter().enumerate() {
        let hidden = sheet.visible != SheetVisible::Visible;
        if (!options.include_hidden && hidden)
            || (!options.sheets.is_empty() && !options.sheets.contains(&sheet.name))
        {
            continue;
        }
        let range = workbook
            .worksheet_range(&sheet.name)
            .map_err(|e| OfficeError::new("WORKSHEET_PARSE_FAILED", e.to_string(), false))?;
        let formulas = workbook
            .worksheet_formula(&sheet.name)
            .map_err(|e| OfficeError::new("WORKSHEET_PARSE_FAILED", e.to_string(), false))?;
        let mut cells = BTreeMap::new();
        let meta = biff.sheets.get(index).cloned().unwrap_or_default();
        for (r, c, value) in range.used_cells() {
            let start = range.start().unwrap_or((0, 0));
            let pos = (start.0 + r as u32, start.1 + c as u32);
            budget.add_cells(1, &options.limits)?;
            if !selected(pos, &meta, options)? {
                continue;
            }
            let (raw, kind) = match value {
                Data::Int(v) => (json!(v), "integer"),
                Data::Float(v) => (json!(v), "float"),
                Data::Bool(v) => (json!(v), "boolean"),
                Data::DateTime(v) => (json!(v.as_f64()), "datetime"),
                Data::Error(v) => (json!(v.to_string()), "error"),
                Data::Empty => (Value::Null, "empty"),
                _ => (json!(value.to_string()), "string"),
            };
            let formula = formulas.get_value(pos).filter(|f| !f.is_empty()).cloned();
            let format = meta
                .formats
                .get(&pos)
                .map(String::as_str)
                .unwrap_or("General");
            let mut record = cell_record(
                pos,
                raw,
                kind,
                formula,
                !matches!(value, Data::Empty),
                (format, biff.date1904),
                options,
            );
            if let Data::DateTime(v) = value {
                let (y, m, d, h, mi, s, ms) = v.to_ymd_hms_milli();
                record["date_iso"] = json!(format!(
                    "{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{ms:03}"
                ));
            }
            budget.add_text(record["value"].as_str().unwrap_or(""), &options.limits)?;
            cells.insert(pos, record);
        }
        for (r, c, formula) in formulas.used_cells() {
            if formula.is_empty() {
                continue;
            }
            let start = formulas.start().unwrap_or((0, 0));
            let pos = (start.0 + r as u32, start.1 + c as u32);
            if !cells.contains_key(&pos) && selected(pos, &meta, options)? {
                budget.add_cells(1, &options.limits)?;
                cells.insert(
                    pos,
                    cell_record(
                        pos,
                        Value::Null,
                        "empty",
                        Some(formula.clone()),
                        false,
                        ("General", biff.date1904),
                        options,
                    ),
                );
            }
        }
        let mut meta = meta;
        meta.merged = workbook
            .merge_cells_by_sheet_name(&sheet.name)
            .unwrap_or_default()
            .iter()
            .map(|d| format!("{}:{}", a1(d.start.0, d.start.1), a1(d.end.0, d.end.1)))
            .collect();
        let unit = build_unit(
            (index, &sheet.name),
            hidden,
            cells,
            &meta,
            options,
            "xls",
            &mut document.warnings,
        )?;
        document.units.push(unit);
    }
    Ok(document)
}

#[derive(Default, Clone)]
struct SheetMeta {
    merged: Vec<String>,
    hidden_rows: BTreeSet<u32>,
    hidden_columns: Vec<(u32, u32)>,
    links: Vec<Value>,
    tables: Vec<Value>,
    graphics: Vec<Value>,
    images: Vec<Value>,
    formats: BTreeMap<(u32, u32), String>,
}
fn validate_sheets<'a>(options: &ParseOptions, names: impl Iterator<Item = &'a str>) -> Result<()> {
    let names: Vec<_> = names.collect();
    for name in &options.sheets {
        if !names.contains(&name.as_str()) {
            return Err(OfficeError::new(
                "SHEET_NOT_FOUND",
                format!("Worksheet not found: {name}"),
                true,
            ));
        }
    }
    Ok(())
}
fn selected(pos: (u32, u32), meta: &SheetMeta, options: &ParseOptions) -> Result<bool> {
    if !options.include_hidden
        && (meta.hidden_rows.contains(&pos.0)
            || meta
                .hidden_columns
                .iter()
                .any(|&(a, b)| pos.1 >= a && pos.1 <= b))
    {
        return Ok(false);
    }
    if let Some(range) = &options.cell_range {
        let (a, b) = parse_cell_range(range)?;
        return Ok(pos.0 >= a.0 && pos.0 <= b.0 && pos.1 >= a.1 && pos.1 <= b.1);
    }
    Ok(true)
}
fn cell_record(
    pos: (u32, u32),
    raw: Value,
    kind: &str,
    formula: Option<String>,
    cached: bool,
    display_context: (&str, bool),
    options: &ParseOptions,
) -> Value {
    let (format, date1904) = display_context;
    let (display, exact, iso) = formatting::display(&raw, kind, format, date1904);
    let cache_status = if formula.is_none() {
        "not_applicable"
    } else if cached {
        "present_unverified"
    } else {
        "missing"
    };
    let value = if formula.is_some() && options.formula_mode == "formula" {
        formula.clone()
    } else if raw.is_null() {
        None
    } else {
        Some(display.clone())
    };
    let formula_out = if options.formula_mode == "cached_value" {
        None
    } else {
        formula
    };
    json!({"address":a1(pos.0,pos.1), "row":pos.0+1,"column":pos.1+1,"data_type":kind,"value":value,"raw_value":raw,"display_text":display,"display_exact":exact,"number_format":format,"date_system":if date1904 {"1904"} else {"1900"},"date_iso":iso,"formula":formula_out,"cache_status":cache_status})
}
fn build_unit(
    identity: (usize, &str),
    hidden: bool,
    cells: BTreeMap<(u32, u32), Value>,
    meta: &SheetMeta,
    options: &ParseOptions,
    format: &str,
    warnings: &mut Vec<Warning>,
) -> Result<LogicalUnit> {
    let (index, name) = identity;
    let mut unit = LogicalUnit::new(
        format!("sheet_{}", index + 1),
        (index + 1) as u32,
        name,
        "worksheet",
    );
    unit.hidden = hidden;
    unit.metadata
        .insert("page_ref_kind".into(), json!("worksheet"));
    unit.metadata.insert(
        "visibility".into(),
        json!(if hidden { "hidden" } else { "visible" }),
    );
    unit.metadata.insert(
        "hidden_rows".into(),
        json!(meta.hidden_rows.iter().map(|n| n + 1).collect::<Vec<_>>()),
    );
    unit.metadata.insert(
        "hidden_columns".into(),
        json!(
            meta.hidden_columns
                .iter()
                .map(|(a, b)| [a + 1, b + 1])
                .collect::<Vec<_>>()
        ),
    );
    unit.metadata.insert("tables".into(), json!(meta.tables));
    unit.metadata
        .insert("graphics".into(), json!(meta.graphics));
    unit.metadata.insert("images".into(), json!(meta.images));
    let minr = cells.keys().map(|p| p.0).min().unwrap_or(0);
    let maxr = cells.keys().map(|p| p.0).max().unwrap_or(0);
    let minc = cells.keys().map(|p| p.1).min().unwrap_or(0);
    let maxc = cells.keys().map(|p| p.1).max().unwrap_or(0);
    unit.width = if cells.is_empty() {
        0.0
    } else {
        f64::from(maxc - minc + 1)
    };
    unit.height = if cells.is_empty() {
        0.0
    } else {
        f64::from(maxr - minr + 1)
    };
    if !options.sparse && !cells.is_empty() {
        office_core::check_limit(
            ((maxr - minr + 1) as usize).saturating_mul((maxc - minc + 1) as usize),
            options.limits.max_cells,
            "dense grid; use sparse=true or cell_range",
        )?;
    }
    let size = if options.batch_rows == 0 {
        (maxr - minr + 1) as usize
    } else {
        options.batch_rows
    };
    let mut groups: BTreeMap<u32, Vec<Value>> = BTreeMap::new();
    for ((r, c), mut cell) in cells {
        cell["source_ref"] = json!(format!("{format}:sheet:{name}:cell:{}", a1(r, c)));
        groups
            .entry(((r - minr) as usize / size) as u32)
            .or_default()
            .push(cell);
    }
    if groups.is_empty() {
        groups.insert(0, Vec::new());
    }
    for (group, records) in groups {
        let start = minr + group * (size as u32);
        let end = maxr.min(start.saturating_add(size as u32 - 1));
        let label = if records.is_empty() {
            "empty".into()
        } else {
            format!("{}:{}", a1(start, minc), a1(end, maxc))
        };
        let source = format!("{format}:sheet:{name}:range:{label}");
        let block_id = format!("sheet{}_region{}", index + 1, group + 1);
        let mut rows = Vec::new();
        if !options.sparse && !records.is_empty() {
            let lookup: BTreeMap<_, _> = records
                .iter()
                .map(|c| {
                    (
                        (
                            c["row"].as_u64().unwrap() as u32 - 1,
                            c["column"].as_u64().unwrap() as u32 - 1,
                        ),
                        c["value"].as_str().map(str::to_owned),
                    )
                })
                .collect();
            for r in start..=end {
                if !options.include_hidden && meta.hidden_rows.contains(&r) {
                    continue;
                }
                let mut row = Vec::new();
                for c in minc..=maxc {
                    if !options.include_hidden
                        && meta.hidden_columns.iter().any(|&(a, b)| c >= a && c <= b)
                    {
                        continue;
                    }
                    row.push(lookup.get(&(r, c)).cloned().flatten());
                }
                rows.push(row);
            }
        }
        let text = if options.sparse {
            records
                .iter()
                .map(|r| {
                    format!(
                        "{}: {}",
                        r["address"].as_str().unwrap(),
                        r["value"].as_str().unwrap_or("")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            rows.iter()
                .map(|r| {
                    r.iter()
                        .map(|c| c.as_deref().unwrap_or(""))
                        .collect::<Vec<_>>()
                        .join(" | ")
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        let mut block = Block::new(&block_id, "table", text, unit.blocks.len() + 1, &source);
        block.metadata = BTreeMap::from([
            ("worksheet".into(), json!(name)),
            ("range".into(), json!(label)),
            ("page_ref_kind".into(), json!("worksheet")),
            (
                "representation".into(),
                json!(if options.sparse { "sparse" } else { "dense" }),
            ),
            ("cell_records".into(), json!(records)),
            ("merged_ranges".into(), json!(meta.merged)),
            ("hyperlinks".into(), json!(meta.links)),
        ]);
        for (code, message, field, needle) in [
            (
                "FORMULA_CACHE_MISSING",
                "Formula has no stored cached result.",
                "cache_status",
                json!("missing"),
            ),
            (
                "DISPLAY_FORMAT_APPROXIMATED",
                "Unsupported display format; raw value retained.",
                "display_exact",
                json!(false),
            ),
        ] {
            let addresses: Vec<_> = records
                .iter()
                .filter(|c| c[field] == needle)
                .map(|c| c["address"].clone())
                .collect();
            if !addresses.is_empty() {
                warnings.push(Warning {
                    code: code.into(),
                    message: message.into(),
                    context: BTreeMap::from([
                        ("worksheet".into(), json!(name)),
                        ("addresses".into(), json!(addresses)),
                    ]),
                });
            }
        }
        unit.blocks.push(block);
        unit.tables.push(Table {
            id: format!("table_{block_id}"),
            block_id,
            rows,
            source_refs: vec![source],
            metadata: BTreeMap::from([
                ("worksheet".into(), json!(name)),
                ("range".into(), json!(label)),
                ("merged_ranges".into(), json!(meta.merged)),
                (
                    "representation".into(),
                    json!(if options.sparse { "sparse" } else { "dense" }),
                ),
            ]),
        });
    }
    Ok(unit)
}
fn a1(row: u32, column: u32) -> String {
    let mut n = column + 1;
    let mut letters = String::new();
    while n > 0 {
        letters.insert(0, (b'A' + ((n - 1) % 26) as u8) as char);
        n = (n - 1) / 26;
    }
    format!("{letters}{}", row + 1)
}

#[derive(Default)]
struct BiffMeta {
    sheets: Vec<SheetMeta>,
    date1904: bool,
}
fn read_biff(bytes: &[u8], options: &ParseOptions) -> Result<BiffMeta> {
    let mut compound = cfb::CompoundFile::open(Cursor::new(bytes))
        .map_err(|e| OfficeError::new("XLS_CFB_OPEN_FAILED", e.to_string(), false))?;
    let name = if compound.exists("/Workbook") {
        "/Workbook"
    } else {
        "/Book"
    };
    let mut stream = compound
        .open_stream(name)
        .map_err(|e| OfficeError::new("XLS_WORKBOOK_STREAM_MISSING", e.to_string(), false))?;
    let mut data = Vec::new();
    stream
        .by_ref()
        .take(options.limits.max_part_bytes.saturating_add(1))
        .read_to_end(&mut data)
        .map_err(|e| OfficeError::new("XLS_BIFF_HEADER_INVALID", e.to_string(), false))?;
    if data.len() as u64 > options.limits.max_part_bytes {
        return Err(OfficeError::new(
            "PARSE_LIMIT_EXCEEDED",
            "BIFF stream too large.",
            false,
        ));
    }
    if data.len() < 8 || data[..2] != [9, 8] || data[4..6] != [0, 6] {
        return Err(OfficeError::new(
            "XLS_BIFF_VERSION_NOT_SUPPORTED",
            "Only BIFF8 is supported.",
            true,
        ));
    }
    let mut meta = BiffMeta::default();
    let mut offset = 0;
    let mut sheet = None;
    let mut cells = 0_usize;
    let mut xfs = Vec::new();
    let mut custom_formats = BTreeMap::new();
    let mut format_cells: Vec<(usize, (u32, u32), usize)> = Vec::new();
    while offset + 4 <= data.len() {
        let id = u16::from_le_bytes([data[offset], data[offset + 1]]);
        let len = u16::from_le_bytes([data[offset + 2], data[offset + 3]]) as usize;
        offset += 4;
        let r = data.get(offset..offset + len).ok_or_else(|| {
            OfficeError::new("XLS_BIFF_HEADER_INVALID", "Truncated BIFF record.", false)
        })?;
        offset += len;
        let u16at = |n: usize| u16::from_le_bytes([r[n], r[n + 1]]);
        if id == 0x00e0 && len >= 4 {
            xfs.push(u32::from(u16at(2)));
        }
        if id == 0x041e && len >= 5 {
            let count = usize::from(u16at(2));
            let flags = r[4];
            let start = 5 + if flags & 8 != 0 { 2 } else { 0 } + if flags & 4 != 0 { 4 } else { 0 };
            let width = if flags & 1 != 0 { 2 } else { 1 };
            if let Some(chars) = r.get(start..start + count * width) {
                let value = if width == 2 {
                    String::from_utf16_lossy(
                        &chars
                            .chunks_exact(2)
                            .map(|b| u16::from_le_bytes([b[0], b[1]]))
                            .collect::<Vec<_>>(),
                    )
                } else {
                    chars.iter().map(|b| char::from(*b)).collect()
                };
                custom_formats.insert(u32::from(u16at(0)), value);
            }
        }
        if id == 0x002f {
            return Err(OfficeError::new(
                "ENCRYPTED_OFFICE_NOT_SUPPORTED",
                "Encrypted BIFF workbook.",
                false,
            ));
        }
        if id == 0x0022 && len >= 2 {
            meta.date1904 = u16at(0) != 0;
        }
        if id == 0x0809 && len >= 4 && u16at(2) == 0x0010 {
            meta.sheets.push(SheetMeta::default());
            sheet = Some(meta.sheets.len() - 1);
        }
        if let Some(i) = sheet {
            if matches!(
                id,
                0x0203 | 0x027e | 0x00fd | 0x0204 | 0x0006 | 0x0205 | 0x0201
            ) && len >= 6
            {
                format_cells.push((
                    i,
                    (u32::from(u16at(0)), u32::from(u16at(2))),
                    usize::from(u16at(4)),
                ));
            }
            if matches!(id, 0x00bd | 0x00be) && len >= 6 {
                let stride = if id == 0x00bd { 6 } else { 2 };
                for (column, chunk) in r[4..len - 2].chunks_exact(stride).enumerate() {
                    format_cells.push((
                        i,
                        (u32::from(u16at(0)), u32::from(u16at(2)) + column as u32),
                        usize::from(u16::from_le_bytes([chunk[0], chunk[1]])),
                    ));
                }
            }
            if id == 0x0208 && len >= 16 && u16at(12) & 0x20 != 0 {
                meta.sheets[i].hidden_rows.insert(u32::from(u16at(0)));
            }
            if id == 0x007d && len >= 10 && u16at(8) & 1 != 0 {
                meta.sheets[i]
                    .hidden_columns
                    .push((u32::from(u16at(0)), u32::from(u16at(2))));
            }
            if matches!(
                id,
                0x0203 | 0x027e | 0x00fd | 0x0204 | 0x0006 | 0x0205 | 0x00bd | 0x00be
            ) {
                cells = cells.saturating_add(if matches!(id, 0x00bd | 0x00be) && len >= 6 {
                    usize::from(u16at(len - 2).saturating_sub(u16at(2))) + 1
                } else {
                    1
                });
                office_core::check_limit(cells, options.limits.max_cells, "BIFF cells")?;
            }
            if id == 0x0200 && len >= 12 {
                let first = u32::from_le_bytes(r[..4].try_into().unwrap());
                let last = u32::from_le_bytes(r[4..8].try_into().unwrap());
                let area = (last.saturating_sub(first) as usize)
                    .saturating_mul(usize::from(u16at(10).saturating_sub(u16at(8))));
                office_core::check_limit(area, options.limits.max_cells, "BIFF dense allocation")?;
            }
        }
    }
    // Derive allocation bounds from actual records as well as DIMENSIONS;
    // a malformed file must not bypass preflight with a dishonest DIMENSIONS record.
    let mut bounds: BTreeMap<usize, (u32, u32, u32, u32)> = BTreeMap::new();
    for &(sheet, (r, c), _) in &format_cells {
        if c > 255 {
            return Err(OfficeError::new(
                "XLS_CELL_ADDRESS_INVALID",
                "BIFF8 column exceeds 255.",
                false,
            ));
        }
        bounds
            .entry(sheet)
            .and_modify(|b| {
                b.0 = b.0.min(r);
                b.1 = b.1.min(c);
                b.2 = b.2.max(r);
                b.3 = b.3.max(c);
            })
            .or_insert((r, c, r, c));
    }
    let total_area = bounds.values().fold(0_usize, |sum, &(r0, c0, r1, c1)| {
        sum.saturating_add(((r1 - r0 + 1) as usize).saturating_mul((c1 - c0 + 1) as usize))
    });
    office_core::check_limit(
        total_area,
        options.limits.max_cells,
        "BIFF workbook allocations",
    )?;
    for (sheet, pos, xf) in format_cells {
        if let Some(&format) = xfs.get(xf) {
            meta.sheets[sheet].formats.insert(
                pos,
                custom_formats
                    .get(&format)
                    .cloned()
                    .unwrap_or_else(|| formatting::builtin(format)),
            );
        }
    }
    Ok(meta)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn biff_preflight_does_not_trust_dimensions() {
        use std::io::Write;
        let mut data = Vec::new();
        let mut record = |id: u16, payload: &[u8]| {
            data.extend(id.to_le_bytes());
            data.extend((payload.len() as u16).to_le_bytes());
            data.extend(payload);
        };
        record(0x0809, &[0, 6, 5, 0]);
        record(0x0809, &[0, 6, 16, 0]);
        record(0x0200, &[0; 14]);
        for (row, col) in [(0_u16, 0_u16), (65535, 255)] {
            let mut payload = Vec::new();
            payload.extend(row.to_le_bytes());
            payload.extend(col.to_le_bytes());
            payload.extend(0_u16.to_le_bytes());
            payload.extend(1_f64.to_le_bytes());
            record(0x0203, &payload);
        }
        let mut compound = cfb::CompoundFile::create(Cursor::new(Vec::new())).unwrap();
        compound
            .create_stream("/Workbook")
            .unwrap()
            .write_all(&data)
            .unwrap();
        let bytes = compound.into_inner().into_inner();
        let options = ParseOptions::default();
        assert_eq!(
            read_biff(&bytes, &options).err().unwrap().code,
            "PARSE_LIMIT_EXCEEDED"
        );
    }
    #[test]
    fn cell_addresses_are_stable() {
        assert_eq!(a1(0, 0), "A1");
        assert_eq!(a1(9, 26), "AA10");
        assert_eq!(a1(1048575, 16383), "XFD1048576");
    }
}
