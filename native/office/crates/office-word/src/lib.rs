use std::collections::BTreeMap;
use std::io::Cursor;
use std::path::Path;

use office_core::{
    Block, LogicalUnit, OfficeDocument, OfficeFormat, ParseOptions, Result, Span, Table, Warning,
};
use office_ooxml::{OoxmlPackage, attr, local_name, xml_error};
use quick_xml::Reader;
use quick_xml::events::Event;
use serde_json::json;

pub fn parse_docx(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument> {
    let package = OoxmlPackage::open(bytes, &options.limits)?;
    let mut document = OfficeDocument::new(OfficeFormat::Docx, path, bytes, "logical_flow");
    document
        .metadata
        .insert("page_model".into(), json!("logical_flow"));
    document
        .metadata
        .insert("revision_mode".into(), json!(options.revision_mode));
    document
        .metadata
        .insert("external_links".into(), json!(options.external_links));

    let mut unit = LogicalUnit::new("word_flow_1", 1, "Document", "logical_flow");
    unit.width = 12_240.0;
    let mut order = 0_usize;
    parse_word_part(
        package.get("word/document.xml")?,
        "word/document.xml",
        None,
        options,
        &mut unit,
        &mut order,
    )?;

    for name in sorted_parts(&package, "word/header") {
        if name.ends_with(".xml") {
            parse_word_part(
                package.get(name)?,
                name,
                Some("page_header"),
                options,
                &mut unit,
                &mut order,
            )?;
        }
    }
    for name in sorted_parts(&package, "word/footer") {
        if name.ends_with(".xml") {
            parse_word_part(
                package.get(name)?,
                name,
                Some("page_footer"),
                options,
                &mut unit,
                &mut order,
            )?;
        }
    }
    if let Some(footnotes) = package.get_optional("word/footnotes.xml") {
        parse_word_part(
            footnotes,
            "word/footnotes.xml",
            Some("footnote"),
            options,
            &mut unit,
            &mut order,
        )?;
    }
    if let Some(endnotes) = package.get_optional("word/endnotes.xml") {
        parse_word_part(
            endnotes,
            "word/endnotes.xml",
            Some("footnote"),
            options,
            &mut unit,
            &mut order,
        )?;
    }
    if let Some(comments) = package.get_optional("word/comments.xml") {
        parse_word_part(
            comments,
            "word/comments.xml",
            Some("comment"),
            options,
            &mut unit,
            &mut order,
        )?;
    }

    unit.height = order.max(1) as f64;
    document.units.push(unit);
    if options.extract_images {
        document.assets = package.media_assets("word/media/");
    }
    let relationships = package.relationships("word/document.xml")?;
    let external_count = relationships
        .iter()
        .filter(|relationship| relationship.external())
        .count();
    document
        .metadata
        .insert("external_relationship_count".into(), json!(external_count));
    if external_count > 0 {
        document.warnings.push(Warning {
            code: "EXTERNAL_RELATIONSHIPS_NOT_FETCHED".into(),
            message: "External relationships were retained as metadata and were not fetched."
                .into(),
            context: BTreeMap::from([("count".into(), json!(external_count))]),
        });
    }
    Ok(document)
}

fn sorted_parts<'a>(package: &'a OoxmlPackage, prefix: &'a str) -> Vec<&'a str> {
    let mut names: Vec<_> = package.names_with_prefix(prefix).collect();
    names.sort_by_key(|name| natural_number(name));
    names
}

fn natural_number(name: &str) -> u32 {
    name.chars()
        .filter(char::is_ascii_digit)
        .collect::<String>()
        .parse()
        .unwrap_or(0)
}

struct ParagraphState {
    text: String,
    runs: Vec<Span>,
    style_name: Option<String>,
    list_level: Option<u32>,
    num_id: Option<u32>,
    hyperlink_ids: Vec<String>,
    comment_ids: Vec<String>,
}

impl ParagraphState {
    fn new() -> Self {
        Self {
            text: String::new(),
            runs: Vec::new(),
            style_name: None,
            list_level: None,
            num_id: None,
            hyperlink_ids: Vec::new(),
            comment_ids: Vec::new(),
        }
    }
}

fn parse_word_part(
    bytes: &[u8],
    part_name: &str,
    forced_kind: Option<&str>,
    options: &ParseOptions,
    unit: &mut LogicalUnit,
    order: &mut usize,
) -> Result<()> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut paragraph: Option<ParagraphState> = None;
    let mut run_text = String::new();
    let mut run_styles: Vec<String> = Vec::new();
    let mut in_run = false;
    let mut deleted_depth = 0_usize;
    let mut table_depth = 0_usize;
    let mut table_rows: Vec<Vec<Option<String>>> = Vec::new();
    let mut row: Vec<Option<String>> = Vec::new();
    let mut cell_text = String::new();
    let mut cell_grid_span = 1_usize;
    let mut in_cell = false;
    let mut table_index = unit.tables.len();

    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element)) => match local_name(element.name().as_ref()) {
                b"p" => paragraph = Some(ParagraphState::new()),
                b"r" => {
                    in_run = true;
                    run_text.clear();
                    run_styles.clear();
                }
                b"del" => deleted_depth += 1,
                b"tbl" => {
                    table_depth += 1;
                    if table_depth == 1 {
                        table_rows.clear();
                    }
                }
                b"tr" if table_depth > 0 => row.clear(),
                b"tc" if table_depth > 0 => {
                    in_cell = true;
                    cell_text.clear();
                    cell_grid_span = 1;
                }
                b"hyperlink" => {
                    if let Some(state) = paragraph.as_mut()
                        && let Some(id) = attr(&reader, &element, b"id")
                    {
                        state.hyperlink_ids.push(id);
                    }
                }
                b"commentRangeStart" | b"commentReference" => {
                    if let Some(state) = paragraph.as_mut()
                        && let Some(id) = attr(&reader, &element, b"id")
                    {
                        state.comment_ids.push(id);
                    }
                }
                _ => {}
            },
            Ok(Event::Empty(element)) => {
                let qualified_name = element.name();
                let name = local_name(qualified_name.as_ref());
                if in_run && matches!(name, b"b" | b"i" | b"u" | b"strike" | b"vertAlign") {
                    run_styles.push(String::from_utf8_lossy(name).into_owned());
                }
                if in_cell && name == b"gridSpan" {
                    let span = attr(&reader, &element, b"val")
                        .and_then(|value| value.parse::<usize>().ok())
                        .unwrap_or(1);
                    if span == 0 || span > options.limits.max_cells {
                        return Err(office_core::OfficeError::new(
                            "PARSE_LIMIT_EXCEEDED",
                            "Word table grid span exceeds max_cells.",
                            false,
                        ));
                    }
                    cell_grid_span = span;
                }
                if let Some(state) = paragraph.as_mut() {
                    match name {
                        b"pStyle" => state.style_name = attr(&reader, &element, b"val"),
                        b"ilvl" => {
                            state.list_level =
                                attr(&reader, &element, b"val").and_then(|value| value.parse().ok())
                        }
                        b"numId" => {
                            state.num_id =
                                attr(&reader, &element, b"val").and_then(|value| value.parse().ok())
                        }
                        b"tab" if deleted_depth == 0 => {
                            state.text.push('\t');
                            run_text.push('\t');
                        }
                        b"br" if deleted_depth == 0 => {
                            state.text.push('\n');
                            run_text.push('\n');
                        }
                        b"commentRangeStart" | b"commentReference" => {
                            if let Some(id) = attr(&reader, &element, b"id") {
                                state.comment_ids.push(id);
                            }
                        }
                        _ => {}
                    }
                }
            }
            Ok(Event::Text(text)) if paragraph.is_some() && deleted_depth == 0 => {
                let decoded = text.decode().map_err(|error| {
                    office_core::OfficeError::new("INVALID_XML", error.to_string(), false)
                })?;
                if let Some(state) = paragraph.as_mut() {
                    state.text.push_str(&decoded);
                }
                if in_run {
                    run_text.push_str(&decoded);
                }
            }
            Ok(Event::End(element)) => match local_name(element.name().as_ref()) {
                b"del" => deleted_depth = deleted_depth.saturating_sub(1),
                b"r" => {
                    if let Some(state) = paragraph.as_mut()
                        && !run_text.is_empty()
                    {
                        state.runs.push(Span {
                            id: format!("word_span_{:06}_{}", *order + 1, state.runs.len() + 1),
                            text: run_text.clone(),
                            styles: run_styles.clone(),
                            metadata: BTreeMap::new(),
                        });
                    }
                    in_run = false;
                }
                b"p" => {
                    if let Some(state) = paragraph.take() {
                        let text = state.text.trim().to_string();
                        if !text.is_empty() {
                            if in_cell {
                                if !cell_text.is_empty() {
                                    cell_text.push('\n');
                                }
                                cell_text.push_str(&text);
                            } else {
                                *order += 1;
                                let heading_level = heading_level(state.style_name.as_deref());
                                let kind = forced_kind.unwrap_or(if heading_level.is_some() {
                                    "heading"
                                } else {
                                    "paragraph"
                                });
                                let mut block = Block::new(
                                    format!("word_b{:06}", *order),
                                    kind,
                                    text,
                                    *order,
                                    format!("docx:part:{part_name}:block:{}", *order),
                                );
                                block.spans = state.runs;
                                block
                                    .metadata
                                    .insert("source_part".into(), json!(part_name));
                                if let Some(style) = state.style_name {
                                    block.metadata.insert("style_name".into(), json!(style));
                                }
                                if let Some(level) = heading_level {
                                    block.metadata.insert("heading_level".into(), json!(level));
                                }
                                if let Some(level) = state.list_level {
                                    block.metadata.insert("list_level".into(), json!(level));
                                }
                                if let Some(num_id) = state.num_id {
                                    block.metadata.insert("numbering_id".into(), json!(num_id));
                                }
                                if !state.hyperlink_ids.is_empty() {
                                    block.metadata.insert(
                                        "hyperlink_relationship_ids".into(),
                                        json!(state.hyperlink_ids),
                                    );
                                }
                                if !state.comment_ids.is_empty() {
                                    block
                                        .metadata
                                        .insert("comment_ids".into(), json!(state.comment_ids));
                                }
                                unit.blocks.push(block);
                            }
                        }
                    }
                }
                b"tc" if table_depth > 0 => {
                    let value = (!cell_text.is_empty()).then(|| cell_text.trim().to_string());
                    for _ in 0..cell_grid_span {
                        row.push(value.clone());
                    }
                    in_cell = false;
                }
                b"tr" if table_depth > 0 => {
                    if row.iter().any(Option::is_some) {
                        table_rows.push(std::mem::take(&mut row));
                    }
                }
                b"tbl" => {
                    if table_depth == 1 && !table_rows.is_empty() {
                        table_index += 1;
                        *order += 1;
                        let block_id = format!("word_table{:06}", table_index);
                        let text = table_rows
                            .iter()
                            .map(|cells| {
                                cells
                                    .iter()
                                    .map(|cell| cell.as_deref().unwrap_or(""))
                                    .collect::<Vec<_>>()
                                    .join(" | ")
                            })
                            .collect::<Vec<_>>()
                            .join("\n");
                        let mut block = Block::new(
                            &block_id,
                            "table",
                            text,
                            *order,
                            format!("docx:part:{part_name}:table:{table_index}"),
                        );
                        block
                            .metadata
                            .insert("table_rows".into(), json!(table_rows));
                        unit.blocks.push(block);
                        unit.tables.push(Table {
                            id: format!("table_{block_id}"),
                            block_id,
                            rows: std::mem::take(&mut table_rows),
                            source_refs: vec![format!("docx:part:{part_name}:table:{table_index}")],
                            metadata: BTreeMap::from([("source_part".into(), json!(part_name))]),
                        });
                    }
                    table_depth = table_depth.saturating_sub(1);
                }
                _ => {}
            },
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        if unit.blocks.len() > options.limits.max_text_chars {
            return Err(office_core::OfficeError::new(
                "PARSE_LIMIT_EXCEEDED",
                "Word block count exceeds configured limits.",
                false,
            ));
        }
        buffer.clear();
    }
    Ok(())
}

fn heading_level(style: Option<&str>) -> Option<u32> {
    let style = style?;
    if style.eq_ignore_ascii_case("title") {
        return Some(1);
    }
    let normalized = style.replace(' ', "").to_ascii_lowercase();
    normalized
        .strip_prefix("heading")
        .and_then(|level| level.parse::<u32>().ok())
        .filter(|level| (1..=9).contains(level))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heading_styles_are_understood() {
        assert_eq!(heading_level(Some("Heading 2")), Some(2));
        assert_eq!(heading_level(Some("Body Text")), None);
    }
}
