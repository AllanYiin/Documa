use std::collections::BTreeMap;
use std::io::Cursor;
use std::path::Path;

use office_core::{
    Block, LogicalUnit, OfficeDocument, OfficeError, OfficeFormat, ParseOptions, Result, Table,
    Warning,
};
use office_ooxml::{OoxmlPackage, attr, local_name, xml_error};
use quick_xml::Reader;
use quick_xml::events::Event;
use serde_json::json;

const EMU_PER_POINT: f64 = 12_700.0;

pub fn parse_pptx(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument> {
    let package = OoxmlPackage::open(bytes, &options.limits)?;
    let mut document = OfficeDocument::new(OfficeFormat::Pptx, path, bytes, "slide_points");
    document
        .metadata
        .insert("page_ref_kind".into(), json!("slide"));
    let (slide_width, slide_height) = presentation_size(package.get("ppt/presentation.xml")?)?;

    let mut slide_names: Vec<_> = package
        .names_with_prefix("ppt/slides/slide")
        .filter(|name| name.ends_with(".xml"))
        .collect();
    slide_names.sort_by_key(|name| natural_number(name));
    if slide_names.len() > options.limits.max_shapes {
        return Err(OfficeError::new(
            "PARSE_LIMIT_EXCEEDED",
            "Slide count exceeds configured limits.",
            false,
        ));
    }

    for (index, name) in slide_names.iter().enumerate() {
        let placeholders = slide_placeholders(&package, name)?;
        let mut unit = parse_slide(package.get(name)?, index + 1, name, &placeholders, options)?;
        unit.width = slide_width;
        unit.height = slide_height;
        if let Some(notes) = matching_notes(&package, index + 1) {
            let note_text = all_drawing_text(notes)?;
            if !note_text.trim().is_empty() {
                let order = unit.blocks.len() + 1;
                let mut block = Block::new(
                    format!("pptx_s{}_notes", index + 1),
                    "paragraph",
                    note_text.trim(),
                    order,
                    format!("pptx:slide:{}:speaker_notes", index + 1),
                );
                block
                    .metadata
                    .insert("source_type".into(), json!("speaker_notes"));
                block.metadata.insert("visual".into(), json!(false));
                unit.blocks.push(block);
            }
        }
        document.units.push(unit);
    }

    if options.extract_images {
        document.assets = package.media_assets("ppt/media/");
    }
    let chart_count = package
        .names_with_prefix("ppt/charts/")
        .filter(|name| name.ends_with(".xml"))
        .count();
    document
        .metadata
        .insert("chart_inventory_count".into(), json!(chart_count));
    let smartart_count = package
        .names_with_prefix("ppt/diagrams/")
        .filter(|name| name.ends_with(".xml"))
        .count();
    document
        .metadata
        .insert("smartart_inventory_count".into(), json!(smartart_count));
    if chart_count + smartart_count > 0 {
        document.warnings.push(Warning {
            code: "COMPLEX_GRAPHICS_METADATA_ONLY".into(),
            message:
                "Charts and SmartArt are inventoried but not rendered or semantically evaluated."
                    .into(),
            context: BTreeMap::from([
                ("chart_count".into(), json!(chart_count)),
                ("smartart_count".into(), json!(smartart_count)),
            ]),
        });
    }
    Ok(document)
}

fn presentation_size(bytes: &[u8]) -> Result<(f64, f64)> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Empty(element)) | Ok(Event::Start(element))
                if local_name(element.name().as_ref()) == b"sldSz" =>
            {
                let width = attr(&reader, &element, b"cx")
                    .and_then(|value| value.parse::<f64>().ok())
                    .unwrap_or(0.0)
                    / EMU_PER_POINT;
                let height = attr(&reader, &element, b"cy")
                    .and_then(|value| value.parse::<f64>().ok())
                    .unwrap_or(0.0)
                    / EMU_PER_POINT;
                return Ok((width, height));
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok((0.0, 0.0))
}

struct ShapeState {
    text: String,
    name: Option<String>,
    placeholder_key: Option<String>,
    alt_text: Option<String>,
    title: bool,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    kind: &'static str,
}

impl ShapeState {
    fn new(kind: &'static str) -> Self {
        Self {
            text: String::new(),
            name: None,
            placeholder_key: None,
            alt_text: None,
            title: false,
            x: 0.0,
            y: 0.0,
            width: 0.0,
            height: 0.0,
            kind,
        }
    }
}

fn parse_slide(
    bytes: &[u8],
    slide_index: usize,
    part_name: &str,
    placeholders: &BTreeMap<String, [f64; 4]>,
    options: &ParseOptions,
) -> Result<LogicalUnit> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut unit = LogicalUnit::new(
        format!("slide_{slide_index}"),
        slide_index as u32,
        format!("Slide {slide_index}"),
        "slide",
    );
    unit.metadata.insert("source_part".into(), json!(part_name));
    unit.metadata.insert("page_ref_kind".into(), json!("slide"));

    let mut shape: Option<ShapeState> = None;
    let mut shape_depth = 0_usize;
    let mut table_depth = 0_usize;
    let mut table_rows: Vec<Vec<Option<String>>> = Vec::new();
    let mut table_row: Vec<Option<String>> = Vec::new();
    let mut table_cell = String::new();
    let mut in_table_cell = false;
    let mut shape_count = 0_usize;
    let mut table_count = 0_usize;

    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element)) => {
                let qualified_name = element.name();
                let name = local_name(qualified_name.as_ref());
                if matches!(name, b"sp" | b"pic" | b"graphicFrame" | b"cxnSp") {
                    if shape.is_none() {
                        shape = Some(ShapeState::new(match name {
                            b"pic" => "image",
                            b"graphicFrame" => "graphic_frame",
                            _ => "shape",
                        }));
                        shape_depth = 1;
                    } else {
                        shape_depth += 1;
                    }
                } else if shape.is_some() && matches!(name, b"grpSp" | b"spTree") {
                    shape_depth += 1;
                }
                if name == b"tbl" {
                    table_depth += 1;
                    if table_depth == 1 {
                        table_rows.clear();
                    }
                } else if name == b"tr" && table_depth > 0 {
                    table_row.clear();
                } else if name == b"tc" && table_depth > 0 {
                    in_table_cell = true;
                    table_cell.clear();
                }
            }
            Ok(Event::Empty(element)) => {
                if let Some(current) = shape.as_mut() {
                    match local_name(element.name().as_ref()) {
                        b"cNvPr" => {
                            current.name = attr(&reader, &element, b"name");
                            current.alt_text = attr(&reader, &element, b"descr");
                            if current
                                .name
                                .as_deref()
                                .is_some_and(|name| name.to_ascii_lowercase().starts_with("title"))
                            {
                                current.title = true;
                            }
                        }
                        b"ph" => {
                            let placeholder_type =
                                attr(&reader, &element, b"type").unwrap_or_else(|| "body".into());
                            let placeholder_index = attr(&reader, &element, b"idx");
                            current.placeholder_key = Some(
                                placeholder_index
                                    .map(|index| format!("idx:{index}"))
                                    .unwrap_or_else(|| format!("type:{placeholder_type}")),
                            );
                            if matches!(
                                placeholder_type.as_str(),
                                "title" | "ctrTitle" | "subTitle"
                            ) {
                                current.title = true;
                            }
                        }
                        b"off" => {
                            current.x = attr(&reader, &element, b"x")
                                .and_then(|value| value.parse::<f64>().ok())
                                .unwrap_or(0.0)
                                / EMU_PER_POINT;
                            current.y = attr(&reader, &element, b"y")
                                .and_then(|value| value.parse::<f64>().ok())
                                .unwrap_or(0.0)
                                / EMU_PER_POINT;
                        }
                        b"ext" => {
                            current.width = attr(&reader, &element, b"cx")
                                .and_then(|value| value.parse::<f64>().ok())
                                .unwrap_or(0.0)
                                / EMU_PER_POINT;
                            current.height = attr(&reader, &element, b"cy")
                                .and_then(|value| value.parse::<f64>().ok())
                                .unwrap_or(0.0)
                                / EMU_PER_POINT;
                        }
                        _ => {}
                    }
                }
            }
            Ok(Event::Text(text)) if shape.is_some() => {
                let decoded = text
                    .decode()
                    .map_err(|error| OfficeError::new("INVALID_XML", error.to_string(), false))?;
                if let Some(current) = shape.as_mut() {
                    current.text.push_str(&decoded);
                    if in_table_cell {
                        table_cell.push_str(&decoded);
                    }
                }
            }
            Ok(Event::End(element)) => {
                let qualified_name = element.name();
                let name = local_name(qualified_name.as_ref());
                if name == b"tc" && table_depth > 0 {
                    table_row.push(
                        (!table_cell.trim().is_empty()).then(|| table_cell.trim().to_string()),
                    );
                    in_table_cell = false;
                } else if name == b"tr" && table_depth > 0 {
                    if table_row.iter().any(Option::is_some) {
                        table_rows.push(std::mem::take(&mut table_row));
                    }
                } else if name == b"tbl" && table_depth > 0 {
                    if table_depth == 1 && !table_rows.is_empty() {
                        table_count += 1;
                    }
                    table_depth -= 1;
                }

                if shape.is_some() && matches!(name, b"sp" | b"pic" | b"graphicFrame" | b"cxnSp") {
                    shape_depth = shape_depth.saturating_sub(1);
                    if shape_depth == 0 {
                        let mut current = shape.take().expect("shape state checked");
                        if current.width == 0.0
                            && current.height == 0.0
                            && let Some(bbox) = current
                                .placeholder_key
                                .as_ref()
                                .and_then(|key| placeholders.get(key))
                        {
                            [current.x, current.y, current.width, current.height] =
                                [bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]];
                        }
                        shape_count += 1;
                        if shape_count > options.limits.max_shapes {
                            return Err(OfficeError::new(
                                "PARSE_LIMIT_EXCEEDED",
                                "Shape count exceeds max_shapes.",
                                false,
                            ));
                        }
                        let text = current.text.trim().to_string();
                        if !table_rows.is_empty() {
                            let block_id = format!("pptx_s{slide_index}_table{table_count:04}");
                            let table_text = table_rows
                                .iter()
                                .map(|row| {
                                    row.iter()
                                        .map(|cell| cell.as_deref().unwrap_or(""))
                                        .collect::<Vec<_>>()
                                        .join(" | ")
                                })
                                .collect::<Vec<_>>()
                                .join("\n");
                            let mut block = Block::new(
                                &block_id,
                                "table",
                                table_text,
                                unit.blocks.len() + 1,
                                format!("pptx:slide:{slide_index}:table:{table_count}"),
                            );
                            block.bbox = Some([
                                current.x,
                                current.y,
                                current.x + current.width,
                                current.y + current.height,
                            ]);
                            block
                                .metadata
                                .insert("table_rows".into(), json!(table_rows));
                            unit.blocks.push(block);
                            unit.tables.push(Table {
                                id: format!("table_{block_id}"),
                                block_id,
                                rows: std::mem::take(&mut table_rows),
                                source_refs: vec![format!(
                                    "pptx:slide:{slide_index}:table:{table_count}"
                                )],
                                metadata: BTreeMap::new(),
                            });
                        } else if !text.is_empty() || current.kind == "image" {
                            let kind = if current.title {
                                "heading"
                            } else if current.kind == "image" {
                                "image"
                            } else {
                                "paragraph"
                            };
                            let mut block = Block::new(
                                format!("pptx_s{slide_index}_b{shape_count:04}"),
                                kind,
                                text,
                                unit.blocks.len() + 1,
                                format!("pptx:slide:{slide_index}:shape:{shape_count}"),
                            );
                            block.bbox = Some([
                                current.x,
                                current.y,
                                current.x + current.width,
                                current.y + current.height,
                            ]);
                            block
                                .metadata
                                .insert("shape_kind".into(), json!(current.kind));
                            block
                                .metadata
                                .insert("shape_name".into(), json!(current.name));
                            block
                                .metadata
                                .insert("alt_text".into(), json!(current.alt_text));
                            block.metadata.insert("z_order".into(), json!(shape_count));
                            if current.title {
                                block.metadata.insert("heading_level".into(), json!(1));
                            }
                            unit.blocks.push(block);
                        }
                    }
                }
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }

    unit.blocks.sort_by(|left, right| {
        let left_title = left.kind == "heading";
        let right_title = right.kind == "heading";
        right_title
            .cmp(&left_title)
            .then_with(|| {
                left.bbox
                    .map(|bbox| bbox[1])
                    .unwrap_or(f64::MAX)
                    .total_cmp(&right.bbox.map(|bbox| bbox[1]).unwrap_or(f64::MAX))
            })
            .then_with(|| {
                left.bbox
                    .map(|bbox| bbox[0])
                    .unwrap_or(f64::MAX)
                    .total_cmp(&right.bbox.map(|bbox| bbox[0]).unwrap_or(f64::MAX))
            })
            .then_with(|| left.id.cmp(&right.id))
    });
    for (index, block) in unit.blocks.iter_mut().enumerate() {
        block.order_index = index + 1;
    }
    Ok(unit)
}

fn slide_placeholders(
    package: &OoxmlPackage,
    slide_part: &str,
) -> Result<BTreeMap<String, [f64; 4]>> {
    let Some(layout_relationship) = package
        .relationships(slide_part)?
        .into_iter()
        .find(|relationship| relationship.relationship_type.ends_with("/slideLayout"))
    else {
        return Ok(BTreeMap::new());
    };
    let layout_part = resolve_target(slide_part, &layout_relationship.target);
    let mut geometry = BTreeMap::new();
    if let Some(master_relationship) = package
        .relationships(&layout_part)?
        .into_iter()
        .find(|relationship| relationship.relationship_type.ends_with("/slideMaster"))
    {
        let master_part = resolve_target(&layout_part, &master_relationship.target);
        if let Some(bytes) = package.get_optional(&master_part) {
            geometry.extend(parse_placeholder_geometry(bytes)?);
        }
    }
    if let Some(bytes) = package.get_optional(&layout_part) {
        geometry.extend(parse_placeholder_geometry(bytes)?);
    }
    Ok(geometry)
}

fn parse_placeholder_geometry(bytes: &[u8]) -> Result<BTreeMap<String, [f64; 4]>> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(true);
    let mut buffer = Vec::new();
    let mut geometry = BTreeMap::new();
    let mut in_shape = false;
    let mut key: Option<String> = None;
    let (mut x, mut y, mut width, mut height) = (0.0, 0.0, 0.0, 0.0);
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Start(element)) if local_name(element.name().as_ref()) == b"sp" => {
                in_shape = true;
                key = None;
                (x, y, width, height) = (0.0, 0.0, 0.0, 0.0);
            }
            Ok(Event::Empty(element)) if in_shape => match local_name(element.name().as_ref()) {
                b"ph" => {
                    let placeholder_type =
                        attr(&reader, &element, b"type").unwrap_or_else(|| "body".into());
                    key = Some(
                        attr(&reader, &element, b"idx")
                            .map(|index| format!("idx:{index}"))
                            .unwrap_or_else(|| format!("type:{placeholder_type}")),
                    );
                }
                b"off" => {
                    x = attr(&reader, &element, b"x")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(0.0)
                        / EMU_PER_POINT;
                    y = attr(&reader, &element, b"y")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(0.0)
                        / EMU_PER_POINT;
                }
                b"ext" => {
                    width = attr(&reader, &element, b"cx")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(0.0)
                        / EMU_PER_POINT;
                    height = attr(&reader, &element, b"cy")
                        .and_then(|value| value.parse::<f64>().ok())
                        .unwrap_or(0.0)
                        / EMU_PER_POINT;
                }
                _ => {}
            },
            Ok(Event::End(element)) if local_name(element.name().as_ref()) == b"sp" => {
                if let Some(key) = key.take()
                    && (width > 0.0 || height > 0.0)
                {
                    geometry.insert(key, [x, y, x + width, y + height]);
                }
                in_shape = false;
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok(geometry)
}

fn resolve_target(base_part: &str, target: &str) -> String {
    let mut parts: Vec<String> = base_part.split('/').map(str::to_string).collect();
    parts.pop();
    let normalized_target = target.replace('\\', "/");
    for segment in normalized_target.split('/') {
        match segment {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            value => parts.push(value.to_string()),
        }
    }
    parts.join("/")
}
fn matching_notes(package: &OoxmlPackage, slide_index: usize) -> Option<&[u8]> {
    package.get_optional(&format!("ppt/notesSlides/notesSlide{slide_index}.xml"))
}

fn all_drawing_text(bytes: &[u8]) -> Result<String> {
    let mut reader = Reader::from_reader(Cursor::new(bytes));
    reader.config_mut().trim_text(false);
    let mut buffer = Vec::new();
    let mut text = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(Event::Text(value)) => {
                let decoded = value
                    .decode()
                    .map_err(|error| OfficeError::new("INVALID_XML", error.to_string(), false))?;
                if !decoded.trim().is_empty() {
                    text.push(decoded.into_owned());
                }
            }
            Ok(Event::Eof) => break,
            Err(error) => return Err(xml_error(error)),
            _ => {}
        }
        buffer.clear();
    }
    Ok(text.join("\n"))
}

fn natural_number(name: &str) -> u32 {
    name.rsplit('/')
        .next()
        .unwrap_or(name)
        .chars()
        .filter(char::is_ascii_digit)
        .collect::<String>()
        .parse()
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emu_conversion_is_exact_at_one_point() {
        assert_eq!(12_700.0 / EMU_PER_POINT, 1.0);
    }
}
