use super::*;

pub(super) fn enrich_slide(
    package: &OoxmlPackage,
    part: &str,
    slide: &Node,
    unit: &mut LogicalUnit,
    warnings: &mut Vec<Warning>,
    options: &ParseOptions,
    budget: &mut office_core::ParseBudget,
) -> Result<()> {
    fn shapes<'a>(n: &'a Node, out: &mut Vec<&'a Node>) {
        for child in &n.children {
            if matches!(child.name.as_str(), "sp" | "pic" | "graphicFrame" | "cxnSp") {
                out.push(child);
            } else {
                shapes(child, out);
            }
        }
    }
    let mut nodes = Vec::new();
    shapes(slide, &mut nodes);
    let relationships = package.relationships(part)?;
    for (index, node) in nodes.iter().enumerate() {
        let source = format!("pptx:slide:{}:shape:{}", unit.number, index + 1);
        if let Some(block) = unit
            .blocks
            .iter_mut()
            .find(|b| b.source_refs.contains(&source))
        {
            if block.kind != "image" {
                block.text = node.drawing_text();
            }
            if let Some(properties) = node.descendants("cNvPr").first() {
                block
                    .metadata
                    .insert("shape_name".into(), json!(properties.attr("name")));
                block
                    .metadata
                    .insert("alt_text".into(), json!(properties.attr("descr")));
            }
            let mut assets = Vec::new();
            for blip in node.descendants("blip") {
                if let Some(id) = blip.attr("embed")
                    && let Some(target) = package.related_part(part, id)?
                {
                    assets.push(asset_id(&target));
                }
            }
            block.metadata.insert("asset_ids".into(), json!(assets));
            if options.external_links != "ignore" {
                let links: Vec<_> = node.descendants("hlinkClick").iter().filter_map(|link| relationships.iter().find(|r| Some(r.id.as_str()) == link.attr("id"))).map(|r| json!({"relationship_id": r.id, "target": r.target, "external": r.external()})).collect();
                block.metadata.insert("hyperlinks".into(), json!(links));
            }
            if node.descendants("xfrm").is_empty() && block.bbox.is_some() {
                block.confidence = "inferred".into();
                warnings.push(Warning {
                    code: "INFERRED_GEOMETRY".into(),
                    message: "Shape geometry was inherited from its layout/master or unavailable."
                        .into(),
                    context: BTreeMap::from([("source_ref".into(), json!(source))]),
                });
            }
        }
    }
    for (index, table_node) in slide.descendants("tbl").iter().enumerate() {
        let Some(table) = unit.tables.get_mut(index) else {
            continue;
        };
        let mut cells = Vec::new();
        let mut rows = Vec::new();
        for (r, row) in table_node
            .children
            .iter()
            .filter(|n| n.name == "tr")
            .enumerate()
        {
            let mut values = Vec::new();
            for (c, cell) in row.children.iter().filter(|n| n.name == "tc").enumerate() {
                budget.add_cells(1, &options.limits)?;
                let covered = matches!(cell.attr("hMerge"), Some("1" | "true"))
                    || matches!(cell.attr("vMerge"), Some("1" | "true"));
                let text = cell.drawing_text();
                values.push((!covered && !text.is_empty()).then_some(text.clone()));
                if !covered {
                    let span = |name| {
                        cell.attr(name)
                            .and_then(|v| v.parse::<usize>().ok())
                            .unwrap_or(1)
                    };
                    let (rowspan, colspan) = (span("rowSpan"), span("gridSpan"));
                    if rowspan == 0 || colspan == 0 {
                        return Err(OfficeError::new(
                            "INVALID_TABLE",
                            "Table spans must be positive.",
                            false,
                        ));
                    }
                    office_core::check_limit(
                        rowspan.saturating_mul(colspan),
                        options.limits.max_cells,
                        "table span",
                    )?;
                    cells.push(json!({"row": r + 1, "column": c + 1, "rowspan": rowspan, "colspan": colspan, "text": text, "source_ref": format!("pptx:part:{part}:table:{}:cell:{},{}", index + 1, r + 1, c + 1)}));
                }
            }
            rows.push(values);
        }
        table.rows = rows;
        table.metadata.insert("cells".into(), json!(cells));
        if let Some(block) = unit.blocks.iter_mut().find(|b| b.id == table.block_id) {
            block
                .metadata
                .insert("table_rows".into(), json!(table.rows));
            block.metadata.insert("cells".into(), json!(cells));
            block.text = table
                .rows
                .iter()
                .map(|row| {
                    row.iter()
                        .map(|v| v.as_deref().unwrap_or(""))
                        .collect::<Vec<_>>()
                        .join(" | ")
                })
                .collect::<Vec<_>>()
                .join("\n");
        }
    }
    let graphics = package.graphics(part, options)?;
    for (index, graphic) in graphics.iter().enumerate() {
        let text = graphic
            .get("text")
            .and_then(|v| v.as_str())
            .map(str::to_owned)
            .unwrap_or_else(|| graphic.to_string());
        budget.add_text(&text, &options.limits)?;
        let mut block = Block::new(
            format!("pptx_s{}_graphic{}", unit.number, index + 1),
            graphic["kind"].as_str().unwrap_or("graphic"),
            text,
            unit.blocks.len() + 1,
            format!(
                "ooxml:part:{}",
                graphic["source_part"].as_str().unwrap_or(part)
            ),
        );
        block.metadata.insert("graphic".into(), graphic.clone());
        unit.blocks.push(block);
    }
    unit.metadata.insert("graphics".into(), json!(graphics));
    Ok(())
}
