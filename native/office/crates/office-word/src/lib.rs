use office_core::{
    Block, LogicalUnit, OfficeDocument, OfficeFormat, ParseBudget, ParseOptions, Result, Span,
    Table,
};
use office_ooxml::{Node, OoxmlPackage, asset_id, parse_xml};
use serde_json::{Value, json};
use std::collections::BTreeMap;
use std::path::Path;

pub fn parse_docx(path: &Path, bytes: &[u8], options: &ParseOptions) -> Result<OfficeDocument> {
    options.validate()?;
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
    let styles = package
        .get_optional("word/styles.xml")
        .map(|b| parse_xml(b, &options.limits))
        .transpose()?;
    let numbering = package
        .get_optional("word/numbering.xml")
        .map(|b| parse_xml(b, &options.limits))
        .transpose()?;
    let mut context = Context {
        package: &package,
        options,
        budget: ParseBudget::default(),
        styles,
        numbering,
        table_index: 0,
        paragraph_index: 0,
        list_counters: BTreeMap::new(),
    };
    let mut unit = LogicalUnit::new("word_flow_1", 1, "Document", "logical_flow");
    let main = parse_xml(package.get("word/document.xml")?, &options.limits)?;
    if main.name != "document" || main.child("body").is_none() {
        return Err(office_core::OfficeError::new(
            "OOXML_DOCUMENT_INVALID",
            "Expected a Word document with a body.",
            false,
        ));
    }
    context.container(&main, "word/document.xml", None, None, &mut unit)?;
    for tag in [
        "altChunk",
        "object",
        "pict",
        "control",
        "oMath",
        "oMathPara",
    ] {
        let count = main.descendants(tag).len();
        if count > 0 {
            document.warnings.push(office_core::Warning {
                code: "UNSUPPORTED_WORD_STRUCTURE".into(),
                message: format!("{tag} is not fully interpreted."),
                context: BTreeMap::from([
                    ("source_part".into(), json!("word/document.xml")),
                    ("element".into(), json!(tag)),
                    ("count".into(), json!(count)),
                ]),
            });
        }
    }
    for (rel_type, forced_kind) in [
        ("header", "page_header"),
        ("footer", "page_footer"),
        ("footnotes", "footnote"),
        ("endnotes", "endnote"),
        ("comments", "comment"),
    ] {
        for part in package.related_by_type("word/document.xml", rel_type)? {
            let root = parse_xml(package.get(&part)?, &options.limits)?;
            if matches!(rel_type, "footnotes" | "endnotes" | "comments") {
                for annotation in &root.children {
                    let id = annotation.attr("id").unwrap_or("");
                    if id.parse::<i64>().is_ok_and(|n| n < 0)
                        || matches!(
                            annotation.attr("type"),
                            Some("separator" | "continuationSeparator")
                        )
                    {
                        continue;
                    }
                    let start = unit.blocks.len();
                    context.container(annotation, &part, Some(forced_kind), None, &mut unit)?;
                    for block in &mut unit.blocks[start..] {
                        block.metadata.insert("annotation_id".into(), json!(id));
                        block.metadata.insert(
                            "annotation_key".into(),
                            json!(format!("{forced_kind}:{id}")),
                        );
                        block
                            .metadata
                            .insert("author".into(), json!(annotation.attr("author")));
                        block
                            .metadata
                            .insert("date".into(), json!(annotation.attr("date")));
                    }
                }
            } else {
                context.container(&root, &part, Some(forced_kind), None, &mut unit)?;
            }
        }
    }
    for (i, block) in unit.blocks.iter_mut().enumerate() {
        block.order_index = i + 1;
    }
    unit.height = unit.blocks.len().max(1) as f64;
    document.assets = package.assets("word/media/", options);
    for block in &unit.blocks {
        if let Some(images) = block.metadata.get("images").and_then(Value::as_array) {
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
                        .entry("block_ids".into())
                        .or_insert(json!([]))
                        .as_array_mut()
                        .unwrap()
                        .push(json!(block.id));
                }
            }
        }
    }
    document.units.push(unit);
    Ok(document)
}

struct Context<'a> {
    package: &'a OoxmlPackage,
    options: &'a ParseOptions,
    budget: ParseBudget,
    styles: Option<Node>,
    numbering: Option<Node>,
    table_index: usize,
    paragraph_index: usize,
    list_counters: BTreeMap<(String, usize), usize>,
}

impl Context<'_> {
    fn excluded(&self, n: &Node) -> bool {
        (matches!(n.name.as_str(), "del" | "moveFrom") && self.options.revision_mode == "final")
            || (matches!(n.name.as_str(), "ins" | "moveTo")
                && self.options.revision_mode == "original")
    }
    fn container(
        &mut self,
        node: &Node,
        part: &str,
        kind: Option<&str>,
        parent: Option<&str>,
        unit: &mut LogicalUnit,
    ) -> Result<()> {
        if self.excluded(node) {
            return Ok(());
        }
        match node.name.as_str() {
            "AlternateContent" => {
                if let Some(branch) = node.child("Choice").or_else(|| node.child("Fallback")) {
                    self.container(branch, part, kind, parent, unit)?;
                }
            }
            "p" => {
                if let Some(block) = self.paragraph(node, part, kind)? {
                    unit.blocks.push(block);
                }
            }
            "tbl" => {
                self.table(node, part, parent, unit)?;
            }
            _ => {
                for child in &node.children {
                    self.container(child, part, kind, parent, unit)?;
                }
            }
        }
        Ok(())
    }
    fn run_text(&self, node: &Node) -> String {
        if self.excluded(node)
            || (node.name == "r"
                && !self.options.include_hidden
                && node
                    .child("rPr")
                    .is_some_and(|p| p.child("vanish").is_some_and(on)))
        {
            return String::new();
        }
        match node.name.as_str() {
            "AlternateContent" => node
                .child("Choice")
                .or_else(|| node.child("Fallback"))
                .map(|n| self.run_text(n))
                .unwrap_or_default(),
            "t" | "delText" => node.content(),
            "tab" => "\t".into(),
            "br" | "cr" => "\n".into(),
            "instrText" | "rPr" | "pPr" | "drawing" | "pict" => String::new(),
            _ => node.children.iter().map(|c| self.run_text(c)).collect(),
        }
    }
    fn visible_nodes<'a>(&self, node: &'a Node, name: &str, out: &mut Vec<&'a Node>) {
        if self.excluded(node) {
            return;
        }
        if node.name == name {
            out.push(node);
        }
        for child in &node.children {
            self.visible_nodes(child, name, out);
        }
    }
    fn paragraph(
        &mut self,
        node: &Node,
        part: &str,
        forced_kind: Option<&str>,
    ) -> Result<Option<Block>> {
        self.paragraph_index += 1;
        let text = self.run_text(node);
        self.budget.add_text(&text, &self.options.limits)?;
        let props = node.child("pPr");
        let style = props
            .and_then(|n| n.child("pStyle"))
            .and_then(|n| n.attr("val"));
        let heading = self.heading_level(style);
        let mut block = Block::new(
            format!("word_b{:06}", self.paragraph_index),
            forced_kind.unwrap_or(if heading.is_some() {
                "heading"
            } else {
                "paragraph"
            }),
            text.trim(),
            self.paragraph_index,
            format!("docx:part:{part}:paragraph:{}", self.paragraph_index),
        );
        block.metadata.insert("source_part".into(), json!(part));
        if let Some(style) = style {
            block.metadata.insert("style_name".into(), json!(style));
        }
        if let Some(level) = heading {
            block.metadata.insert("heading_level".into(), json!(level));
        }
        let mut runs = Vec::new();
        self.visible_nodes(node, "r", &mut runs);
        for run in runs {
            let text = self.run_text(run);
            if text.is_empty() {
                continue;
            }
            let styles = run
                .child("rPr")
                .map(|p| {
                    p.children
                        .iter()
                        .filter(|n| {
                            matches!(n.name.as_str(), "b" | "i" | "u" | "strike" | "vertAlign")
                                && on(n)
                        })
                        .map(|n| n.name.clone())
                        .collect()
                })
                .unwrap_or_default();
            block.spans.push(Span {
                id: format!("{}_run{}", block.id, block.spans.len() + 1),
                text,
                styles,
                metadata: BTreeMap::new(),
            });
        }
        if let Some(num) = props.and_then(|n| n.child("numPr")) {
            let level = num
                .child("ilvl")
                .and_then(|n| n.attr("val"))
                .and_then(|s| s.parse::<usize>().ok())
                .unwrap_or(0);
            if let Some(id) = num.child("numId").and_then(|n| n.attr("val")) {
                block.metadata.insert("list_level".into(), json!(level));
                block.metadata.insert("numbering_id".into(), json!(id));
                if let Some(label) = self.list_label(id, level) {
                    block.metadata.insert("list_label".into(), json!(label));
                }
            }
        }
        let relationships = self.package.relationships(part)?;
        let mut links = Vec::new();
        let mut hyperlinks = Vec::new();
        self.visible_nodes(node, "hyperlink", &mut hyperlinks);
        for link in hyperlinks {
            let relationship = link
                .attr("id")
                .and_then(|id| relationships.iter().find(|r| r.id == id));
            if relationship.is_some_and(|r| r.external()) && self.options.external_links == "ignore"
            {
                continue;
            }
            links.push(json!({"relationship_id": link.attr("id"), "target": relationship.map(|r| r.target.as_str()).or_else(|| link.attr("anchor")), "external": relationship.is_some_and(|r| r.external()), "text": self.run_text(link)}));
        }
        block.metadata.insert("hyperlinks".into(), json!(links));
        let mut references = Vec::new();
        for (tag, kind) in [
            ("footnoteReference", "footnote"),
            ("endnoteReference", "endnote"),
            ("commentReference", "comment"),
            ("commentRangeStart", "comment"),
        ] {
            let mut nodes = Vec::new();
            self.visible_nodes(node, tag, &mut nodes);
            for n in nodes {
                if let Some(id) = n.attr("id") {
                    references.push(format!("{kind}:{id}"));
                }
            }
        }
        references.sort();
        references.dedup();
        block
            .metadata
            .insert("annotation_refs".into(), json!(references));
        let mut drawings = Vec::new();
        self.visible_nodes(node, "drawing", &mut drawings);
        let mut images = Vec::new();
        for drawing in drawings {
            self.budget.add_shapes(1, &self.options.limits)?;
            let info = drawing.descendants("docPr");
            for blip in drawing.descendants("blip") {
                if let Some(id) = blip.attr("embed")
                    && let Some(target) = self.package.related_part(part, id)?
                {
                    images.push(json!({"asset_id": asset_id(&target), "source_part": target, "alt_text": info.first().and_then(|n| n.attr("descr")), "placement": if drawing.child("anchor").is_some() { "floating" } else { "inline" }}));
                }
            }
        }
        block.metadata.insert(
            "asset_ids".into(),
            json!(images.iter().map(|i| &i["asset_id"]).collect::<Vec<_>>()),
        );
        block.metadata.insert("images".into(), json!(images));
        if block.text.is_empty() && images.is_empty() {
            return Ok(None);
        }
        if block.text.is_empty() {
            block.kind = "image".into();
        }
        Ok(Some(block))
    }
    fn heading_level(&self, style: Option<&str>) -> Option<u32> {
        let mut style = style?;
        let mut visited = Vec::new();
        for _ in 0..32 {
            if visited.contains(&style) {
                break;
            }
            visited.push(style);
            if let Some(level) = heading_level(Some(style)) {
                return Some(level);
            }
            let n = self
                .styles
                .as_ref()?
                .children
                .iter()
                .find(|n| n.attr("styleId") == Some(style))?;
            if let Some(level) = n
                .child("pPr")
                .and_then(|n| n.child("outlineLvl"))
                .and_then(|n| n.attr("val"))
                .and_then(|s| s.parse::<u32>().ok())
                .filter(|n| *n < 9)
            {
                return Some(level + 1);
            }
            style = n.child("basedOn")?.attr("val")?;
        }
        None
    }
    fn list_label(&mut self, id: &str, level: usize) -> Option<String> {
        if level > 8 {
            return None;
        }
        let numbering = self.numbering.as_ref()?;
        let num = numbering
            .children
            .iter()
            .find(|n| n.name == "num" && n.attr("numId") == Some(id))?;
        let abstract_id = num.child("abstractNumId")?.attr("val")?;
        let abstract_num = numbering
            .children
            .iter()
            .find(|n| n.name == "abstractNum" && n.attr("abstractNumId") == Some(abstract_id))?;
        let definition = abstract_num.children.iter().find(|n| {
            n.name == "lvl" && n.attr("ilvl").and_then(|s| s.parse::<usize>().ok()) == Some(level)
        })?;
        if !matches!(
            definition.child("numFmt").and_then(|n| n.attr("val")),
            Some("decimal" | "bullet")
        ) {
            return None;
        }
        let start = definition
            .child("start")
            .and_then(|n| n.attr("val"))
            .and_then(|s| s.parse::<usize>().ok())
            .unwrap_or(1);
        self.list_counters
            .retain(|(num, l), _| num != id || *l <= level);
        let count = self
            .list_counters
            .entry((id.into(), level))
            .or_insert(start.saturating_sub(1));
        *count += 1;
        let mut label = definition.child("lvlText")?.attr("val")?.to_owned();
        for l in 0..=level {
            let value = self
                .list_counters
                .get(&(id.into(), l))
                .copied()
                .unwrap_or(1);
            label = label.replace(&format!("%{}", l + 1), &value.to_string());
        }
        Some(label)
    }
    fn table(
        &mut self,
        node: &Node,
        part: &str,
        parent: Option<&str>,
        unit: &mut LogicalUnit,
    ) -> Result<String> {
        self.table_index += 1;
        let index = self.table_index;
        let block_position = unit.blocks.len();
        let table_position = unit.tables.len();
        let id = format!("word_table{index:06}");
        let mut rows = Vec::new();
        let mut cells: Vec<Value> = Vec::new();
        let mut vertical: BTreeMap<usize, usize> = BTreeMap::new();
        for (r, row) in node.children.iter().filter(|n| n.name == "tr").enumerate() {
            let before = row
                .child("trPr")
                .and_then(|n| n.child("gridBefore"))
                .and_then(|n| n.attr("val"))
                .and_then(|s| s.parse::<usize>().ok())
                .unwrap_or(0);
            self.budget.add_cells(before, &self.options.limits)?;
            let mut values: Vec<Option<String>> = vec![None; before];
            let mut next_vertical = BTreeMap::new();
            let mut extended = std::collections::BTreeSet::new();
            for cell in row.children.iter().filter(|n| n.name == "tc") {
                let c = values.len();
                let props = cell.child("tcPr");
                let colspan = props
                    .and_then(|n| n.child("gridSpan"))
                    .and_then(|n| n.attr("val"))
                    .and_then(|s| s.parse::<usize>().ok())
                    .unwrap_or(1)
                    .max(1);
                self.budget.add_cells(colspan, &self.options.limits)?;
                let merge = props.and_then(|n| n.child("vMerge"));
                let continuation = merge.is_some_and(|n| n.attr("val") != Some("restart"));
                let mut text = Vec::new();
                let mut paragraphs = Vec::new();
                let mut nested = Vec::new();
                for child in &cell.children {
                    if child.name == "p" {
                        if let Some(block) = self.paragraph(child, part, None)? {
                            text.push(block.text.clone());
                            paragraphs.push(block);
                        }
                    } else if child.name == "tbl" {
                        nested.push(self.table(child, part, Some(&format!("table_{id}")), unit)?);
                    }
                }
                let text = text.join("\n");
                if continuation && let Some(&anchor) = vertical.get(&c) {
                    if extended.insert(anchor) {
                        let span = cells[anchor]["rowspan"].as_u64().unwrap_or(1) + 1;
                        cells[anchor]["rowspan"] = json!(span);
                    }
                    for col in c..c + colspan {
                        next_vertical.insert(col, anchor);
                    }
                    values.extend(std::iter::repeat_n(None, colspan));
                } else {
                    let anchor = cells.len();
                    cells.push(json!({"row": r + 1, "column": c + 1, "rowspan": 1, "colspan": colspan, "text": text, "source_ref": format!("docx:part:{part}:table:{index}:cell:{},{}", r + 1, c + 1), "paragraphs": paragraphs, "nested_table_ids": nested}));
                    if merge.is_some() {
                        for col in c..c + colspan {
                            next_vertical.insert(col, anchor);
                        }
                    }
                    values.push((!text.is_empty()).then_some(text));
                    values.extend(std::iter::repeat_n(None, colspan - 1));
                }
            }
            vertical = next_vertical;
            rows.push(values);
        }
        let text = rows
            .iter()
            .map(|r| {
                r.iter()
                    .map(|c| c.as_deref().unwrap_or(""))
                    .collect::<Vec<_>>()
                    .join(" | ")
            })
            .collect::<Vec<_>>()
            .join("\n");
        let source = format!("docx:part:{part}:table:{index}");
        let mut block = Block::new(&id, "table", text, unit.blocks.len() + 1, &source);
        block.metadata.insert("table_rows".into(), json!(rows));
        block.metadata.insert("cells".into(), json!(cells));
        let images: Vec<Value> = cells
            .iter()
            .filter_map(|c| c["paragraphs"].as_array())
            .flatten()
            .filter_map(|p| p["metadata"]["images"].as_array())
            .flatten()
            .cloned()
            .collect();
        block.metadata.insert(
            "asset_ids".into(),
            json!(
                images
                    .iter()
                    .filter_map(|i| i["asset_id"].as_str())
                    .collect::<std::collections::BTreeSet<_>>()
            ),
        );
        block.metadata.insert("images".into(), json!(images));
        block
            .metadata
            .insert("parent_table_id".into(), json!(parent));
        unit.blocks.insert(block_position, block);
        unit.tables.insert(
            table_position,
            Table {
                id: format!("table_{id}"),
                block_id: id.clone(),
                rows,
                source_refs: vec![source],
                metadata: BTreeMap::from([
                    ("cells".into(), json!(cells)),
                    ("parent_table_id".into(), json!(parent)),
                    ("source_part".into(), json!(part)),
                ]),
            },
        );
        Ok(format!("table_{id}"))
    }
}
fn on(node: &Node) -> bool {
    !matches!(node.attr("val"), Some("0" | "false" | "off" | "none"))
}
fn heading_level(style: Option<&str>) -> Option<u32> {
    let style = style?;
    if style.eq_ignore_ascii_case("title") {
        return Some(1);
    }
    style
        .replace(' ', "")
        .to_ascii_lowercase()
        .strip_prefix("heading")
        .and_then(|s| s.parse().ok())
        .filter(|l| (1..=9).contains(l))
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
