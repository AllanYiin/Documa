use super::*;
use office_ooxml::{Node, OoxmlPackage, asset_id, attr, local_name, parse_xml, xml_error};
use quick_xml::{Reader as XmlReader, events::Event};

pub(super) fn parse(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument> {
    let package = OoxmlPackage::open(bytes, &options.limits)?;
    let workbook = parse_xml(package.get("xl/workbook.xml")?, &options.limits)?;
    if workbook.name != "workbook" || workbook.child("sheets").is_none() {
        return Err(invalid("Expected workbook with sheets."));
    }
    let date1904 = workbook
        .child("workbookPr")
        .is_some_and(|n| matches!(n.attr("date1904"), Some("1" | "true")));
    let mut document = OfficeDocument::new(OfficeFormat::Xlsx, path, bytes, "cell_grid");
    document
        .metadata
        .insert("formula_mode".into(), json!(options.formula_mode));
    document.metadata.insert("defined_names".into(),json!(workbook.descendants("definedName").iter().map(|n|json!({"name":n.attr("name"),"formula":n.content(),"local_sheet_id":n.attr("localSheetId")})).collect::<Vec<_>>()));
    let sheets = workbook.descendants("sheet");
    validate_sheets(options, sheets.iter().filter_map(|n| n.attr("name")))?;
    let shared = package
        .get_optional("xl/sharedStrings.xml")
        .map(|b| parse_xml(b, &options.limits))
        .transpose()?
        .map(|n| {
            n.children
                .iter()
                .filter(|n| n.name == "si")
                .map(|n| {
                    n.descendants("t")
                        .iter()
                        .map(|n| n.content())
                        .collect::<String>()
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let styles = package
        .get_optional("xl/styles.xml")
        .map(|b| parse_xml(b, &options.limits))
        .transpose()?;
    let custom: BTreeMap<_, _> = styles
        .as_ref()
        .map(|s| {
            s.descendants("numFmt")
                .iter()
                .filter_map(|n| {
                    Some((
                        n.attr("numFmtId")?.parse::<u32>().ok()?,
                        n.attr("formatCode")?.to_owned(),
                    ))
                })
                .collect()
        })
        .unwrap_or_default();
    let formats: Vec<_> = styles
        .as_ref()
        .and_then(|s| s.child("cellXfs"))
        .map(|s| {
            s.children
                .iter()
                .map(|n| {
                    let id = n
                        .attr("numFmtId")
                        .and_then(|s| s.parse::<u32>().ok())
                        .unwrap_or(0);
                    custom
                        .get(&id)
                        .cloned()
                        .unwrap_or_else(|| formatting::builtin(id))
                })
                .collect()
        })
        .unwrap_or_else(|| vec!["General".into()]);
    let mut budget = ParseBudget::default();
    for (index, sheet) in sheets.iter().enumerate() {
        let name = sheet
            .attr("name")
            .ok_or_else(|| invalid("Worksheet has no name."))?;
        let hidden = matches!(sheet.attr("state"), Some("hidden" | "veryHidden"));
        if (!options.include_hidden && hidden)
            || (!options.sheets.is_empty() && !options.sheets.iter().any(|s| s == name))
        {
            continue;
        }
        let id = sheet
            .attr("id")
            .ok_or_else(|| invalid("Worksheet has no relationship ID."))?;
        let part = package
            .related_part("xl/workbook.xml", id)?
            .ok_or_else(|| invalid("Worksheet relationship is missing."))?;
        let xml = package.get(&part)?;
        let mut reader = XmlReader::from_reader(xml);
        let mut meta = SheetMeta::default();
        let mut cells = BTreeMap::new();
        let mut cell_start = None;
        let mut depth = 0usize;
        let mut roots = 0usize;
        let mut current_row = 0;
        let mut next_col = 0;
        let mut shared_formulas: BTreeMap<String, ((u32, u32), String)> = BTreeMap::new();
        let mut pending = Vec::new();
        loop {
            let before = reader.buffer_position() as usize;
            let event = reader.read_event().map_err(xml_error)?;
            let is_empty = matches!(&event, Event::Empty(_));
            match event {
                Event::Start(e) | Event::Empty(e) => {
                    if depth == 0 {
                        roots += 1;
                        if roots != 1 || local_name(e.name().as_ref()) != b"worksheet" {
                            return Err(invalid("Expected one worksheet root."));
                        }
                    }
                    if !is_empty {
                        depth += 1;
                        office_core::check_limit(depth, options.limits.max_xml_depth, "XML depth")?;
                    }
                    match local_name(e.name().as_ref()) {
                        b"row" => {
                            current_row = attr(&reader, &e, b"r")
                                .and_then(|s| s.parse::<u32>().ok())
                                .unwrap_or(current_row + 1);
                            next_col = 0;
                            if matches!(attr(&reader, &e, b"hidden").as_deref(), Some("1" | "true"))
                            {
                                meta.hidden_rows.insert(current_row.saturating_sub(1));
                            }
                        }
                        b"col" => {
                            if matches!(attr(&reader, &e, b"hidden").as_deref(), Some("1" | "true"))
                            {
                                let a = attr(&reader, &e, b"min")
                                    .and_then(|s| s.parse::<u32>().ok())
                                    .unwrap_or(1);
                                let b = attr(&reader, &e, b"max")
                                    .and_then(|s| s.parse::<u32>().ok())
                                    .unwrap_or(a);
                                meta.hidden_columns
                                    .push((a.saturating_sub(1), b.saturating_sub(1)));
                            }
                        }
                        b"mergeCell" => {
                            if let Some(r) = attr(&reader, &e, b"ref") {
                                meta.merged.push(r);
                            }
                        }
                        b"hyperlink" => {
                            let id = attr(&reader, &e, b"id");
                            let relationships = package.relationships(&part)?;
                            let rel = id
                                .as_ref()
                                .and_then(|id| relationships.iter().find(|r| &r.id == id));
                            if options.external_links != "ignore"
                                || !rel.is_some_and(|r| r.external())
                            {
                                meta.links.push(json!({"ref":attr(&reader,&e,b"ref"),"target":rel.map(|r|r.target.clone()).or_else(||attr(&reader,&e,b"location")),"external":rel.is_some_and(|r|r.external())}));
                            }
                        }
                        b"c" => {
                            if is_empty {
                                budget.add_cells(1, &options.limits)?;
                                next_col = attr(&reader, &e, b"r")
                                    .map(|r| parse_cell_range(&r))
                                    .transpose()?
                                    .map(|r| r.0.1 + 1)
                                    .unwrap_or(next_col + 1);
                            } else {
                                cell_start = Some(before);
                            }
                        }
                        _ => {}
                    }
                }
                Event::End(e) => {
                    depth = depth.saturating_sub(1);
                    if local_name(e.name().as_ref()) == b"c"
                        && let Some(start) = cell_start.take()
                    {
                        budget.add_cells(1, &options.limits)?;
                        let cell = parse_xml(
                            &xml[start..reader.buffer_position() as usize],
                            &options.limits,
                        )?;
                        let pos = cell
                            .attr("r")
                            .map(parse_cell_range)
                            .transpose()?
                            .map(|r| r.0)
                            .unwrap_or((current_row.saturating_sub(1), next_col));
                        next_col = pos.1 + 1;
                        let formula_node = cell.child("f");
                        let mut formula = formula_node.map(Node::content);
                        if let Some(f) = formula_node
                            && f.attr("t") == Some("shared")
                        {
                            let si = f.attr("si").unwrap_or("").to_owned();
                            if f.text.is_empty() {
                                pending.push((pos, si));
                                formula = None;
                            } else {
                                shared_formulas.insert(si, (pos, f.content()));
                            }
                        }
                        let value = cell.child("v").map(Node::content);
                        let cached = value.as_ref().is_some_and(|v| !v.is_empty());
                        let (raw, kind) = match cell.attr("t").unwrap_or("n") {
                            "s" => {
                                let index = value
                                    .as_deref()
                                    .and_then(|s| s.parse::<usize>().ok())
                                    .ok_or_else(|| invalid("Invalid shared string index."))?;
                                (
                                    json!(
                                        shared
                                            .get(index)
                                            .ok_or_else(|| invalid("Missing shared string."))?
                                    ),
                                    "string",
                                )
                            }
                            "inlineStr" => (
                                json!(
                                    cell.child("is")
                                        .map(|n| n
                                            .descendants("t")
                                            .iter()
                                            .map(|n| n.content())
                                            .collect::<String>())
                                        .unwrap_or_default()
                                ),
                                "string",
                            ),
                            "str" => (json!(value.clone().unwrap_or_default()), "string"),
                            "b" => (json!(value.as_deref() == Some("1")), "boolean"),
                            "e" => (json!(value), "error"),
                            "d" => (json!(value), "datetime"),
                            _ => {
                                if let Some(v) = value.as_ref().filter(|v| !v.is_empty()) {
                                    let n = v
                                        .parse::<f64>()
                                        .map_err(|_| invalid("Invalid numeric cell."))?;
                                    if !n.is_finite() {
                                        return Err(invalid("Non-finite numeric cell."));
                                    }
                                    (json!(n), "float")
                                } else {
                                    (Value::Null, "empty")
                                }
                            }
                        };
                        let style = cell
                            .attr("s")
                            .and_then(|s| s.parse::<usize>().ok())
                            .unwrap_or(0);
                        let format = formats
                            .get(style)
                            .map(String::as_str)
                            .ok_or_else(|| invalid("Invalid cell style index."))?;
                        let kind = if raw.is_number() && formatting::is_date(format) {
                            "datetime"
                        } else {
                            kind
                        };
                        let record = cell_record(
                            pos,
                            raw,
                            kind,
                            formula,
                            cached,
                            (format, date1904),
                            options,
                        );
                        budget.add_text(
                            record["display_text"].as_str().unwrap_or(""),
                            &options.limits,
                        )?;
                        budget
                            .add_text(record["formula"].as_str().unwrap_or(""), &options.limits)?;
                        cells.insert(pos, record);
                    }
                }
                Event::DocType(_) => return Err(invalid("DTD declarations are not supported.")),
                Event::Eof => {
                    if depth != 0 || roots != 1 {
                        return Err(invalid("Unclosed worksheet XML."));
                    }
                    break;
                }
                _ => {}
            }
        }
        for (pos, si) in pending {
            let (origin, base) = shared_formulas
                .get(&si)
                .ok_or_else(|| invalid("Shared formula has no master."))?;
            let formula = shift_formula(
                base,
                i64::from(pos.0) - i64::from(origin.0),
                i64::from(pos.1) - i64::from(origin.1),
            );
            if let Some(record) = cells.get_mut(&pos) {
                record["cache_status"] = json!(if record["raw_value"].is_null() {
                    "missing"
                } else {
                    "present_unverified"
                });
                if options.formula_mode != "cached_value" {
                    record["formula"] = json!(formula);
                }
                if options.formula_mode == "formula" {
                    record["value"] = json!(formula);
                }
            }
        }
        cells.retain(|pos, _| selected(*pos, &meta, options).unwrap_or(false));
        for table_part in package.related_by_type(&part, "table")? {
            let table = parse_xml(package.get(&table_part)?, &options.limits)?;
            meta.tables.push(json!({"name":table.attr("name"),"display_name":table.attr("displayName"),"range":table.attr("ref"),"source_part":table_part,"columns":table.descendants("tableColumn").iter().map(|c|json!({"id":c.attr("id"),"name":c.attr("name")})).collect::<Vec<_>>()}));
        }
        for drawing_part in package.related_by_type(&part, "drawing")? {
            let graphics = package.graphics(&drawing_part, options)?;
            for graphic in &graphics {
                budget.add_text(&graphic.to_string(), &options.limits)?;
            }
            meta.graphics.extend(graphics);
            let drawing = parse_xml(package.get(&drawing_part)?, &options.limits)?;
            for anchor in &drawing.children {
                for blip in anchor.descendants("blip") {
                    if let Some(id) = blip.attr("embed")
                        && let Some(target) = package.related_part(&drawing_part, id)?
                    {
                        let marker = |name| {
                            anchor.child(name).map(|n|json!({"row":n.child("row").map(Node::content),"column":n.child("col").map(Node::content),"row_offset_emu":n.child("rowOff").map(Node::content),"column_offset_emu":n.child("colOff").map(Node::content)}))
                        };
                        meta.images.push(json!({"asset_id":asset_id(&target),"anchor_type":anchor.name,"from":marker("from"),"to":marker("to"),"alt_text":anchor.descendants("cNvPr").first().and_then(|n|n.attr("descr"))}));
                    }
                }
            }
        }
        let unit = build_unit(
            (index, name),
            hidden,
            cells,
            &meta,
            options,
            "xlsx",
            &mut document.warnings,
        )?;
        document.units.push(unit);
    }
    document.assets = package.assets("xl/media/", options);
    for unit in &document.units {
        if let Some(images) = unit.metadata.get("images").and_then(Value::as_array) {
            for image in images {
                if let Some(asset) = document
                    .assets
                    .iter_mut()
                    .find(|a| Some(a.id.as_str()) == image["asset_id"].as_str())
                {
                    if asset.alt_text.is_none() {
                        asset.alt_text = image["alt_text"].as_str().map(str::to_owned);
                    }
                    asset
                        .metadata
                        .entry("unit_ids".into())
                        .or_insert(json!([]))
                        .as_array_mut()
                        .unwrap()
                        .push(json!(unit.id));
                }
            }
        }
    }

    document.metadata.insert(
        "chart_inventory_count".into(),
        json!(
            package
                .names_with_prefix("xl/charts/")
                .filter(|n| n.ends_with(".xml"))
                .count()
        ),
    );
    Ok(document)
}
fn invalid(message: &str) -> OfficeError {
    OfficeError::new("OOXML_WORKSHEET_INVALID", message, false)
}
// Translate relative A1 references; quoted strings and quoted sheet names are left intact.
fn shift_formula(formula: &str, rows: i64, cols: i64) -> String {
    let bytes = formula.as_bytes();
    let mut out = String::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'[' {
            let start = i;
            let mut depth = 0usize;
            while i < bytes.len() {
                if bytes[i] == b'[' {
                    depth += 1;
                }
                if bytes[i] == b']' {
                    depth = depth.saturating_sub(1);
                    i += 1;
                    if depth == 0 {
                        break;
                    }
                    continue;
                }
                i += 1;
            }
            out.push_str(&formula[start..i]);
            continue;
        }
        if matches!(bytes[i], b'\"' | b'\'') {
            let quote = bytes[i];
            let start = i;
            i += 1;
            while i < bytes.len() {
                if bytes[i] == quote {
                    if i + 1 < bytes.len() && bytes[i + 1] == quote {
                        i += 2;
                        continue;
                    }
                    i += 1;
                    break;
                }
                i += 1;
            }
            out.push_str(&formula[start..i]);
            continue;
        }
        let start = i;
        let abs_col = bytes[i] == b'$';
        if abs_col {
            i += 1;
        }
        let letters = i;
        while i < bytes.len() && bytes[i].is_ascii_alphabetic() {
            i += 1;
        }
        let end_letters = i;
        let abs_row = i < bytes.len() && bytes[i] == b'$';
        if abs_row {
            i += 1;
        }
        let digits = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        let boundary =
            start == 0 || !matches!(bytes[start-1],b'A'..=b'Z'|b'a'..=b'z'|b'0'..=b'9'|b'_'|b'.');
        if boundary
            && letters < end_letters
            && digits < i
            && (i == bytes.len() || !matches!(bytes[i],b'('|b'!'|b'_'|b'.'|b'A'..=b'Z'|b'a'..=b'z'))
            && let Ok((pos, _)) = parse_cell_range(&formula[start..i])
        {
            let r = i64::from(pos.0) + if abs_row { 0 } else { rows };
            let c = i64::from(pos.1) + if abs_col { 0 } else { cols };
            if !(0..1_048_576).contains(&r) || !(0..16_384).contains(&c) {
                out.push_str("#REF!");
            } else {
                let address = a1(r as u32, c as u32);
                let split = address.find(|ch: char| ch.is_ascii_digit()).unwrap();
                out.push_str(&format!(
                    "{}{}{}{}",
                    if abs_col { "$" } else { "" },
                    &address[..split],
                    if abs_row { "$" } else { "" },
                    &address[split..]
                ));
            }
            continue;
        }
        i = start;
        let ch = formula[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}
