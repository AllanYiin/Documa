use std::collections::{BTreeMap, BTreeSet};

use crate::{
    BBox, ErrorCode, LayoutLinkAnnotation, LayoutNamedDestination, LayoutNavigationTarget,
    LayoutNavigationTargetKind, LayoutOutlineItem, LayoutWarning, ObjectId, PdfDictionary,
    PdfDocument, PdfError, PdfName, PdfObject, PdfPage, PdfResult, Point, Quad,
    marked_content::resolve_text,
};

#[derive(Debug, Default)]
pub(crate) struct NavigationIndex {
    pub page_links: Vec<Vec<LayoutLinkAnnotation>>,
    pub named_destinations: Vec<LayoutNamedDestination>,
    pub outlines: Vec<LayoutOutlineItem>,
    pub warnings: Vec<LayoutWarning>,
}

pub(crate) fn extract_navigation(
    document: &PdfDocument,
    pages: &[PdfPage],
) -> PdfResult<NavigationIndex> {
    let page_indices = pages
        .iter()
        .map(|page| (page.id, page.index))
        .collect::<BTreeMap<_, _>>();
    let catalog = document.catalog()?;
    let catalog = catalog
        .value
        .as_dictionary()
        .expect("catalog validated by PdfDocument::catalog");
    let mut warnings = Vec::new();
    let named_destinations =
        collect_named_destinations(document, catalog, pages, &page_indices, &mut warnings)?;
    let named_targets = named_destinations
        .iter()
        .map(|destination| (destination.name.clone(), destination.target.clone()))
        .collect::<BTreeMap<_, _>>();
    let page_links = collect_page_links(
        document,
        pages,
        &page_indices,
        &named_targets,
        &mut warnings,
    )?;
    let outlines = collect_outlines(
        document,
        catalog,
        pages,
        &page_indices,
        &named_targets,
        &mut warnings,
    )?;
    Ok(NavigationIndex {
        page_links,
        named_destinations,
        outlines,
        warnings,
    })
}

fn collect_page_links(
    document: &PdfDocument,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<Vec<Vec<LayoutLinkAnnotation>>> {
    let mut result = Vec::with_capacity(pages.len());
    let mut annotation_count = 0_usize;
    for page in pages {
        let mut links = Vec::new();
        let Some(annots) = page.dictionary.get(&PdfName(b"Annots".to_vec())) else {
            result.push(links);
            continue;
        };
        let annots = resolve(document, annots)?;
        let PdfObject::Array(annots) = annots else {
            warn_invalid(warnings, Some(page.index), "page Annots is not an array");
            result.push(links);
            continue;
        };
        for annotation in &annots {
            annotation_count = annotation_count
                .checked_add(1)
                .ok_or_else(|| limit("annotation count overflow"))?;
            if annotation_count > document.limits.max_annotations {
                return Err(limit("annotation count limit exceeded"));
            }
            match parse_link(
                document,
                page,
                pages,
                annotation,
                page_indices,
                named,
                warnings,
            ) {
                Ok(Some(mut link)) => {
                    let ordinal = links.len();
                    link.id = format!("p{}-l{ordinal}", page.index);
                    links.push(link);
                }
                Ok(None) => {}
                Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
                Err(error) => warn_invalid(warnings, Some(page.index), &error.message),
            }
        }
        result.push(links);
    }
    Ok(result)
}

fn parse_link(
    document: &PdfDocument,
    page: &PdfPage,
    pages: &[PdfPage],
    annotation: &PdfObject,
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<Option<LayoutLinkAnnotation>> {
    let object = annotation.as_reference();
    let value = resolve(document, annotation)?;
    let Some(dictionary) = value.as_dictionary() else {
        return Err(invalid("annotation is not a dictionary"));
    };
    if !matches!(
        dictionary.get(&PdfName(b"Subtype".to_vec())),
        Some(PdfObject::Name(name)) if name.is(b"Link")
    ) {
        return Ok(None);
    }
    let rect = dictionary
        .get(&PdfName(b"Rect".to_vec()))
        .ok_or_else(|| invalid("Link annotation has no Rect"))?;
    let bbox = parse_layout_bbox(document, page, rect)?;
    let quads = match dictionary.get(&PdfName(b"QuadPoints".to_vec())) {
        Some(value) => match parse_layout_quads(document, page, value) {
            Ok(quads) => quads,
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(error) => {
                warn_invalid(warnings, Some(page.index), &error.message);
                Vec::new()
            }
        },
        None => Vec::new(),
    };
    let target = parse_dictionary_target(document, dictionary, pages, page_indices, named)?;
    if target.kind == LayoutNavigationTargetKind::Unsupported {
        warn_unsupported(
            warnings,
            Some(page.index),
            target.unsupported_action.as_deref(),
        );
    }
    Ok(Some(LayoutLinkAnnotation {
        id: String::new(),
        object,
        bbox,
        quads,
        target,
        confidence: 1.0,
        rule_id: "stage5c_link_annotation_v1".to_owned(),
    }))
}

fn collect_named_destinations(
    document: &PdfDocument,
    catalog: &PdfDictionary,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<Vec<LayoutNamedDestination>> {
    let mut raw = BTreeMap::<String, PdfObject>::new();
    if let Some(dests) = catalog.get(&PdfName(b"Dests".to_vec())) {
        match resolve_dictionary(document, dests) {
            Ok(dictionary) => {
                for (name, destination) in dictionary {
                    insert_named(
                        document,
                        &mut raw,
                        String::from_utf8_lossy(name.as_bytes()).into_owned(),
                        destination,
                    )?;
                }
            }
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(error) => warn_invalid(warnings, None, &error.message),
        }
    }
    if let Some(names) = catalog.get(&PdfName(b"Names".to_vec())) {
        collect_name_tree_root(document, names, &mut raw, warnings)?;
    }
    let mut result = Vec::with_capacity(raw.len());
    for (name, destination) in raw {
        match parse_destination(
            document,
            &destination,
            pages,
            page_indices,
            &BTreeMap::new(),
        ) {
            Ok(mut target) => {
                target.destination_name = Some(name.clone());
                result.push(LayoutNamedDestination {
                    name,
                    target,
                    rule_id: "stage5c_named_destination_v1".to_owned(),
                });
            }
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(error) => warn_invalid(warnings, None, &error.message),
        }
    }
    Ok(result)
}

fn collect_name_tree_root(
    document: &PdfDocument,
    names: &PdfObject,
    raw: &mut BTreeMap<String, PdfObject>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    let names = match resolve_dictionary(document, names) {
        Ok(names) => names,
        Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
        Err(error) => {
            warn_invalid(warnings, None, &error.message);
            return Ok(());
        }
    };
    let Some(dests) = names.get(&PdfName(b"Dests".to_vec())) else {
        return Ok(());
    };
    let mut visited = BTreeSet::new();
    collect_name_tree_node(document, dests, raw, &mut visited, 0, warnings)
}

fn collect_name_tree_node(
    document: &PdfDocument,
    node: &PdfObject,
    raw: &mut BTreeMap<String, PdfObject>,
    visited: &mut BTreeSet<ObjectId>,
    depth: usize,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    if depth > document.limits.max_object_depth {
        return Err(limit("destination name-tree depth limit exceeded"));
    }
    if let Some(id) = node.as_reference()
        && !visited.insert(id)
    {
        warn_invalid(warnings, None, "cyclic destination name tree");
        return Ok(());
    }
    let dictionary = resolve_dictionary(document, node)?;
    collect_name_pairs(document, &dictionary, raw, warnings)?;
    if let Some(kids) = dictionary.get(&PdfName(b"Kids".to_vec())) {
        let kids = resolve(document, kids)?;
        let PdfObject::Array(kids) = kids else {
            warn_invalid(warnings, None, "destination name-tree Kids is not an array");
            return Ok(());
        };
        for kid in &kids {
            collect_name_tree_node(document, kid, raw, visited, depth + 1, warnings)?;
        }
    }
    Ok(())
}

fn collect_name_pairs(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
    raw: &mut BTreeMap<String, PdfObject>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    let Some(names) = dictionary.get(&PdfName(b"Names".to_vec())) else {
        return Ok(());
    };
    let names = resolve(document, names)?;
    let PdfObject::Array(names) = names else {
        warn_invalid(
            warnings,
            None,
            "destination name-tree Names is not an array",
        );
        return Ok(());
    };
    if !names.len().is_multiple_of(2) {
        warn_invalid(
            warnings,
            None,
            "destination name-tree Names has an odd length",
        );
    }
    for pair in names.chunks_exact(2) {
        let name = navigation_name(document, &pair[0])?;
        insert_named(document, raw, name, pair[1].clone())?;
    }
    Ok(())
}

fn insert_named(
    document: &PdfDocument,
    raw: &mut BTreeMap<String, PdfObject>,
    name: String,
    destination: PdfObject,
) -> PdfResult<()> {
    if !raw.contains_key(&name) && raw.len() >= document.limits.max_named_destinations {
        return Err(limit("named destination count limit exceeded"));
    }
    raw.insert(name, destination);
    Ok(())
}

fn collect_outlines(
    document: &PdfDocument,
    catalog: &PdfDictionary,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<Vec<LayoutOutlineItem>> {
    let Some(outlines) = catalog.get(&PdfName(b"Outlines".to_vec())) else {
        return Ok(Vec::new());
    };
    let outlines = match resolve_dictionary(document, outlines) {
        Ok(outlines) => outlines,
        Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
        Err(error) => {
            warn_invalid(warnings, None, &error.message);
            return Ok(Vec::new());
        }
    };
    let Some(first) = outlines.get(&PdfName(b"First".to_vec())) else {
        return Ok(Vec::new());
    };
    let mut output = Vec::new();
    let mut visited = BTreeSet::new();
    walk_outline_chain(
        document,
        first,
        pages,
        page_indices,
        named,
        0,
        None,
        &mut visited,
        &mut output,
        warnings,
    )?;
    Ok(output)
}

#[allow(clippy::too_many_arguments)]
fn walk_outline_chain(
    document: &PdfDocument,
    first: &PdfObject,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
    depth: usize,
    parent_id: Option<&str>,
    visited: &mut BTreeSet<ObjectId>,
    output: &mut Vec<LayoutOutlineItem>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    if depth > document.limits.max_object_depth {
        return Err(limit("outline depth limit exceeded"));
    }
    let mut current = Some(first.clone());
    while let Some(object) = current {
        let object_id = object.as_reference();
        if let Some(id) = object_id
            && !visited.insert(id)
        {
            warn_invalid(warnings, None, "cyclic outline item chain");
            break;
        }
        if output.len() >= document.limits.max_outline_items {
            return Err(limit("outline item count limit exceeded"));
        }
        let dictionary = resolve_dictionary(document, &object)?;
        let ordinal = output.len();
        let id = format!("o{ordinal}");
        let title = outline_title(document, &dictionary, warnings);
        let target = outline_target(document, &dictionary, pages, page_indices, named, warnings)?;
        output.push(LayoutOutlineItem {
            id: id.clone(),
            title,
            depth,
            parent_id: parent_id.map(str::to_owned),
            object: object_id,
            target,
            rule_id: "stage5c_outline_v1".to_owned(),
        });
        if let Some(child) = dictionary.get(&PdfName(b"First".to_vec())) {
            walk_outline_chain(
                document,
                child,
                pages,
                page_indices,
                named,
                depth + 1,
                Some(&id),
                visited,
                output,
                warnings,
            )?;
        }
        current = dictionary.get(&PdfName(b"Next".to_vec())).cloned();
    }
    Ok(())
}

fn outline_title(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
    warnings: &mut Vec<LayoutWarning>,
) -> String {
    let Some(title) = dictionary.get(&PdfName(b"Title".to_vec())) else {
        warn_invalid(warnings, None, "outline item has no Title");
        return String::new();
    };
    match resolve_text(document, title, "outline Title") {
        Ok(title) => title,
        Err(error) => {
            warn_invalid(warnings, None, &error.message);
            String::new()
        }
    }
}

fn outline_target(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<Option<LayoutNavigationTarget>> {
    if dictionary.get(&PdfName(b"Dest".to_vec())).is_none()
        && dictionary.get(&PdfName(b"A".to_vec())).is_none()
    {
        return Ok(None);
    }
    match parse_dictionary_target(document, dictionary, pages, page_indices, named) {
        Ok(target) => {
            if target.kind == LayoutNavigationTargetKind::Unsupported {
                warn_unsupported(warnings, None, target.unsupported_action.as_deref());
            }
            Ok(Some(target))
        }
        Err(error) if error.code == ErrorCode::LimitExceeded => Err(error),
        Err(error) => {
            warn_invalid(warnings, None, &error.message);
            Ok(None)
        }
    }
}

fn parse_dictionary_target(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
) -> PdfResult<LayoutNavigationTarget> {
    if let Some(destination) = dictionary.get(&PdfName(b"Dest".to_vec())) {
        return parse_destination(document, destination, pages, page_indices, named);
    }
    let action = dictionary
        .get(&PdfName(b"A".to_vec()))
        .ok_or_else(|| invalid("navigation item has neither Dest nor action"))?;
    parse_action(document, action, pages, page_indices, named)
}

fn parse_action(
    document: &PdfDocument,
    action: &PdfObject,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
) -> PdfResult<LayoutNavigationTarget> {
    let dictionary = resolve_dictionary(document, action)?;
    let action_name = match dictionary.get(&PdfName(b"S".to_vec())) {
        Some(PdfObject::Name(name)) => String::from_utf8_lossy(name.as_bytes()).into_owned(),
        _ => return Err(invalid("navigation action has no name S")),
    };
    match action_name.as_str() {
        "URI" => {
            let uri = dictionary
                .get(&PdfName(b"URI".to_vec()))
                .ok_or_else(|| invalid("URI action has no URI"))?;
            Ok(LayoutNavigationTarget {
                kind: LayoutNavigationTargetKind::Uri,
                uri: Some(resolve_text(document, uri, "URI")?),
                destination_name: None,
                page_index: None,
                page_object: None,
                fit: None,
                unsupported_action: None,
            })
        }
        "GoTo" => {
            let destination = dictionary
                .get(&PdfName(b"D".to_vec()))
                .ok_or_else(|| invalid("GoTo action has no D destination"))?;
            parse_destination(document, destination, pages, page_indices, named)
        }
        _ => Ok(LayoutNavigationTarget {
            kind: LayoutNavigationTargetKind::Unsupported,
            uri: None,
            destination_name: None,
            page_index: None,
            page_object: None,
            fit: None,
            unsupported_action: Some(action_name),
        }),
    }
}

fn parse_destination(
    document: &PdfDocument,
    destination: &PdfObject,
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
    named: &BTreeMap<String, LayoutNavigationTarget>,
) -> PdfResult<LayoutNavigationTarget> {
    let value = resolve(document, destination)?;
    match value {
        PdfObject::Name(_) | PdfObject::String(_) => {
            let name = navigation_name(document, &value)?;
            if let Some(target) = named.get(&name) {
                let mut target = target.clone();
                target.destination_name = Some(name);
                return Ok(target);
            }
            Ok(LayoutNavigationTarget {
                kind: LayoutNavigationTargetKind::GoTo,
                uri: None,
                destination_name: Some(name),
                page_index: None,
                page_object: None,
                fit: None,
                unsupported_action: None,
            })
        }
        PdfObject::Array(values) => parse_destination_array(&values, pages, page_indices),
        PdfObject::Dictionary(dictionary) => {
            let nested = dictionary
                .get(&PdfName(b"D".to_vec()))
                .ok_or_else(|| invalid("destination dictionary has no D"))?;
            parse_destination(document, nested, pages, page_indices, named)
        }
        _ => Err(invalid(
            "destination must be a name, string, array, or dictionary",
        )),
    }
}

fn parse_destination_array(
    values: &[PdfObject],
    pages: &[PdfPage],
    page_indices: &BTreeMap<ObjectId, usize>,
) -> PdfResult<LayoutNavigationTarget> {
    let first = values
        .first()
        .ok_or_else(|| invalid("destination array is empty"))?;
    let (page_index, page_object) = match first {
        PdfObject::Reference(id) => (page_indices.get(id).copied(), Some(*id)),
        PdfObject::Integer(index) if *index >= 0 => {
            let index =
                usize::try_from(*index).map_err(|_| invalid("destination page index overflow"))?;
            (Some(index), pages.get(index).map(|page| page.id))
        }
        _ => return Err(invalid("destination array has an invalid page target")),
    };
    if page_index.is_none() {
        return Err(invalid("destination references an unknown page"));
    }
    let fit = values.get(1).and_then(|value| match value {
        PdfObject::Name(name) => Some(String::from_utf8_lossy(name.as_bytes()).into_owned()),
        _ => None,
    });
    Ok(LayoutNavigationTarget {
        kind: LayoutNavigationTargetKind::GoTo,
        uri: None,
        destination_name: None,
        page_index,
        page_object,
        fit,
        unsupported_action: None,
    })
}

fn parse_layout_bbox(
    document: &PdfDocument,
    page: &PdfPage,
    object: &PdfObject,
) -> PdfResult<BBox> {
    let values = numeric_array(document, object)?;
    if values.len() != 4 {
        return Err(invalid("annotation Rect must contain four numbers"));
    }
    BBox::try_new(values[0], values[1], values[2], values[3])?
        .transformed(page.geometry.pdf_to_layout)
}

fn parse_layout_quads(
    document: &PdfDocument,
    page: &PdfPage,
    object: &PdfObject,
) -> PdfResult<Vec<Quad>> {
    let values = numeric_array(document, object)?;
    if values.is_empty() || !values.len().is_multiple_of(8) {
        return Err(invalid(
            "annotation QuadPoints must contain groups of eight numbers",
        ));
    }
    values
        .chunks_exact(8)
        .map(|values| {
            Ok(Quad {
                top_left: layout_point(page, values[0], values[1])?,
                top_right: layout_point(page, values[2], values[3])?,
                bottom_right: layout_point(page, values[6], values[7])?,
                bottom_left: layout_point(page, values[4], values[5])?,
            })
        })
        .collect()
}

fn layout_point(page: &PdfPage, x: f64, y: f64) -> PdfResult<Point> {
    let point = page.geometry.pdf_point_to_layout(Point::try_new(x, y)?);
    Point::try_new(point.x, point.y)
}

fn numeric_array(document: &PdfDocument, object: &PdfObject) -> PdfResult<Vec<f64>> {
    let value = resolve(document, object)?;
    let PdfObject::Array(values) = value else {
        return Err(invalid("expected numeric array"));
    };
    values
        .iter()
        .map(|value| match resolve(document, value)? {
            PdfObject::Integer(value) if value.unsigned_abs() <= (1_u64 << 53) => value
                .to_string()
                .parse::<f64>()
                .map_err(|_| invalid("integer array entry cannot be represented as f64")),
            PdfObject::Integer(_) => Err(invalid(
                "integer array entry exceeds exact f64 coordinate range",
            )),
            PdfObject::Real(value) if value.is_finite() => Ok(value),
            _ => Err(invalid("array entry must be a finite number")),
        })
        .collect()
}

fn navigation_name(document: &PdfDocument, object: &PdfObject) -> PdfResult<String> {
    match resolve(document, object)? {
        PdfObject::Name(name) => Ok(String::from_utf8_lossy(name.as_bytes()).into_owned()),
        PdfObject::String(string) => {
            resolve_text(document, &PdfObject::String(string), "destination name")
        }
        _ => Err(invalid("destination name must be a name or string")),
    }
}

fn resolve_dictionary(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfDictionary> {
    resolve(document, object)?
        .as_dictionary()
        .cloned()
        .ok_or_else(|| invalid("expected navigation dictionary"))
}

fn resolve(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfObject> {
    let mut value = object.clone();
    let mut visited = BTreeSet::new();
    loop {
        let PdfObject::Reference(id) = value else {
            return Ok(value);
        };
        if visited.len() >= document.limits.max_object_depth {
            return Err(limit("navigation reference depth limit exceeded"));
        }
        if !visited.insert(id) {
            return Err(PdfError::new(
                ErrorCode::InvalidReference,
                None,
                "cyclic navigation reference",
            ));
        }
        value = document.object(id)?.value;
    }
}

fn warn_invalid(warnings: &mut Vec<LayoutWarning>, page_index: Option<usize>, message: &str) {
    if warnings.iter().any(|warning| {
        warning.code == "navigation_target_invalid" && warning.page_index == page_index
    }) {
        return;
    }
    warnings.push(LayoutWarning {
        code: "navigation_target_invalid".to_owned(),
        page_index,
        font_resource: None,
        node_id: None,
        message: message.to_owned(),
    });
}

fn warn_unsupported(
    warnings: &mut Vec<LayoutWarning>,
    page_index: Option<usize>,
    action: Option<&str>,
) {
    if warnings.iter().any(|warning| {
        warning.code == "navigation_action_unsupported" && warning.page_index == page_index
    }) {
        return;
    }
    warnings.push(LayoutWarning {
        code: "navigation_action_unsupported".to_owned(),
        page_index,
        font_resource: None,
        node_id: None,
        message: format!(
            "navigation action {} was retained as metadata and not executed",
            action.unwrap_or("unknown")
        ),
    });
}

fn invalid(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidObject, None, message)
}

fn limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}
