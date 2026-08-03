use std::collections::BTreeSet;

use crate::{
    ContentOperation, ErrorCode, LayoutImagePlacement, LayoutProvenance, PageGeometry,
    PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfPage, PdfResult, Point, Quad,
    graphics::{Matrix, parse_matrix, resolve_dictionary},
    marked_content::{MarkedContentProperties, resolve_marked_content_properties},
    parse_content,
};

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct VectorSegment {
    pub start: Point,
    pub end: Point,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub(crate) struct PageVectorPaths {
    pub segments: Vec<VectorSegment>,
    pub image_placements: Vec<LayoutImagePlacement>,
    pub image_placement_error: Option<String>,
    next_image_paint_ordinal: u64,
}

#[derive(Debug, Default)]
pub(crate) struct VectorCollectionState {
    collected_segments: usize,
    collected_images: usize,
    operation_count: usize,
}

#[derive(Debug, Clone, Copy)]
struct GraphicsState {
    ctm: Matrix,
}

impl Default for GraphicsState {
    fn default() -> Self {
        Self {
            ctm: Matrix::IDENTITY,
        }
    }
}

#[derive(Debug, Default)]
struct PathState {
    current: Option<Point>,
    subpath_start: Option<Point>,
    pending: Vec<VectorSegment>,
}

pub(crate) fn collect_vector_paths(
    document: &PdfDocument,
    page: &PdfPage,
    operations: &[ContentOperation],
    collection: &mut VectorCollectionState,
) -> PdfResult<PageVectorPaths> {
    let mut output = PageVectorPaths::default();
    let mut form_stack = BTreeSet::new();
    let mut marked_content = Vec::new();
    process_operations(
        document,
        page,
        &page.resources,
        "",
        operations,
        GraphicsState::default(),
        &mut output,
        &mut form_stack,
        &mut marked_content,
        &mut collection.collected_segments,
        &mut collection.collected_images,
        &mut collection.operation_count,
        0,
    )?;
    Ok(output)
}

#[allow(clippy::too_many_arguments, clippy::too_many_lines)]
fn process_operations(
    document: &PdfDocument,
    page: &PdfPage,
    resources: &PdfDictionary,
    resource_prefix: &str,
    operations: &[ContentOperation],
    initial_state: GraphicsState,
    output: &mut PageVectorPaths,
    form_stack: &mut BTreeSet<crate::ObjectId>,
    marked_content: &mut Vec<MarkedContentProperties>,
    collected_segments: &mut usize,
    collected_images: &mut usize,
    operation_count: &mut usize,
    depth: usize,
) -> PdfResult<()> {
    if depth > document.limits.max_object_depth {
        return Err(limit("vector Form XObject nesting limit exceeded"));
    }
    *operation_count = operation_count
        .checked_add(operations.len())
        .ok_or_else(|| limit("vector content operation count overflow"))?;
    if *operation_count > document.limits.max_content_operations {
        return Err(limit("vector content operation limit exceeded"));
    }
    let mut state = initial_state;
    let mut graphics_stack = Vec::new();
    let mut path = PathState::default();
    for operation in operations {
        match operation.operator.as_slice() {
            b"BMC" => {
                require_operands(operation, 1)?;
                let PdfObject::Name(tag) = &operation.operands[0] else {
                    return invalid(operation, "BMC tag operand must be a name");
                };
                push_marked_content(
                    document,
                    marked_content,
                    MarkedContentProperties::for_tag(tag),
                    operation,
                )?;
            }
            b"BDC" => {
                require_operands(operation, 2)?;
                let PdfObject::Name(tag) = &operation.operands[0] else {
                    return invalid(operation, "BDC tag operand must be a name");
                };
                let properties = match resolve_marked_content_properties(
                    document,
                    resources,
                    tag,
                    &operation.operands[1],
                ) {
                    Ok(resolution) => resolution.properties,
                    Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
                    Err(_) => MarkedContentProperties::for_tag(tag),
                };
                push_marked_content(document, marked_content, properties, operation)?;
            }
            b"EMC" => {
                require_operands(operation, 0)?;
                marked_content.pop();
            }
            b"q" => {
                if graphics_stack.len() >= document.limits.max_object_depth {
                    return Err(limit("vector graphics-state depth limit exceeded"));
                }
                graphics_stack.push(state);
            }
            b"Q" => {
                if let Some(saved) = graphics_stack.pop() {
                    state = saved;
                }
            }
            b"cm" => {
                let values = six_numbers(operation)?;
                state.ctm = state.ctm.multiply(Matrix {
                    a: values[0],
                    b: values[1],
                    c: values[2],
                    d: values[3],
                    e: values[4],
                    f: values[5],
                });
                if !state.ctm.is_finite() {
                    return invalid(operation, "vector graphics matrix must be finite");
                }
            }
            b"m" => {
                let [x, y] = two_numbers(operation)?;
                let point = layout_point(&page.geometry, state.ctm, x, y)?;
                path.current = Some(point);
                path.subpath_start = Some(point);
            }
            b"l" => {
                let [x, y] = two_numbers(operation)?;
                let point = layout_point(&page.geometry, state.ctm, x, y)?;
                add_line(
                    &mut path,
                    point,
                    document.limits.max_path_segments,
                    operation,
                )?;
            }
            b"re" => {
                let values = four_numbers(operation)?;
                let points = [
                    layout_point(&page.geometry, state.ctm, values[0], values[1])?,
                    layout_point(&page.geometry, state.ctm, values[0] + values[2], values[1])?,
                    layout_point(
                        &page.geometry,
                        state.ctm,
                        values[0] + values[2],
                        values[1] + values[3],
                    )?,
                    layout_point(&page.geometry, state.ctm, values[0], values[1] + values[3])?,
                ];
                path.current = Some(points[0]);
                path.subpath_start = Some(points[0]);
                for point in points.into_iter().skip(1) {
                    add_line(
                        &mut path,
                        point,
                        document.limits.max_path_segments,
                        operation,
                    )?;
                }
                close_path(&mut path, document.limits.max_path_segments, operation)?;
            }
            b"h" => close_path(&mut path, document.limits.max_path_segments, operation)?,
            b"c" => update_curve_endpoint(operation, &page.geometry, state.ctm, &mut path, 4)?,
            b"v" | b"y" => {
                update_curve_endpoint(operation, &page.geometry, state.ctm, &mut path, 2)?;
            }
            b"S" | b"B" | b"B*" => stroke_path(
                &mut path,
                output,
                collected_segments,
                document.limits.max_path_segments,
            )?,
            b"s" | b"b" | b"b*" => {
                close_path(&mut path, document.limits.max_path_segments, operation)?;
                stroke_path(
                    &mut path,
                    output,
                    collected_segments,
                    document.limits.max_path_segments,
                )?;
            }
            b"f" | b"F" | b"f*" | b"n" => clear_path(&mut path),
            b"Do" => process_form(
                document,
                page,
                resources,
                resource_prefix,
                operation,
                state,
                output,
                form_stack,
                marked_content,
                collected_segments,
                collected_images,
                operation_count,
                depth,
            )?,
            _ => {}
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn process_form(
    document: &PdfDocument,
    page: &PdfPage,
    resources: &PdfDictionary,
    resource_prefix: &str,
    operation: &ContentOperation,
    state: GraphicsState,
    output: &mut PageVectorPaths,
    form_stack: &mut BTreeSet<crate::ObjectId>,
    marked_content: &mut Vec<MarkedContentProperties>,
    collected_segments: &mut usize,
    collected_images: &mut usize,
    operation_count: &mut usize,
    depth: usize,
) -> PdfResult<()> {
    require_operands(operation, 1)?;
    let PdfObject::Name(name) = &operation.operands[0] else {
        return invalid(operation, "Do operand must be a name");
    };
    let Some(xobjects) = resources.get(&PdfName(b"XObject".to_vec())) else {
        return Ok(());
    };
    let xobjects = resolve_dictionary(document, xobjects)?;
    let Some(target) = xobjects.get(name) else {
        return Ok(());
    };
    let target_id = target.as_reference();
    if let Some(id) = target_id
        && !form_stack.insert(id)
    {
        return Err(PdfError::new(
            ErrorCode::InvalidReference,
            Some(operation.offset),
            "cyclic vector Form XObject reference",
        ));
    }
    let marked_content_depth = marked_content.len();
    let result = (|| {
        let value = if let Some(id) = target_id {
            document.object(id)?.value
        } else {
            target.clone()
        };
        let PdfObject::Stream(stream) = value else {
            return Ok(());
        };
        let resource_name = join_resource_name(resource_prefix, name);
        let subtype = stream.dictionary.get(&PdfName(b"Subtype".to_vec()));
        if matches!(
            subtype,
            Some(PdfObject::Name(subtype)) if subtype.is(b"Image")
        ) {
            match append_image_placement(
                document,
                page,
                state,
                resource_name,
                target_id,
                marked_content.last(),
                output,
                collected_images,
            ) {
                Ok(()) => {}
                Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
                Err(error) => {
                    output.image_placement_error.get_or_insert(error.message);
                }
            }
            return Ok(());
        }
        if !matches!(subtype, Some(PdfObject::Name(subtype)) if subtype.is(b"Form")) {
            return Ok(());
        }
        let form_resources = stream
            .dictionary
            .get(&PdfName(b"Resources".to_vec()))
            .map(|object| resolve_dictionary(document, object))
            .transpose()?
            .unwrap_or_else(|| resources.clone());
        let mut form_state = state;
        if let Some(matrix) = stream.dictionary.get(&PdfName(b"Matrix".to_vec())) {
            form_state.ctm = form_state.ctm.multiply(parse_matrix(matrix)?);
            if !form_state.ctm.is_finite() {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "vector Form transformation matrix must be finite",
                ));
            }
        }
        let decoded = document.decode_stream(&stream)?;
        let operations = parse_content(&decoded, &document.limits)?;
        process_operations(
            document,
            page,
            &form_resources,
            &resource_name,
            &operations,
            form_state,
            output,
            form_stack,
            marked_content,
            collected_segments,
            collected_images,
            operation_count,
            depth + 1,
        )
    })();
    marked_content.truncate(marked_content_depth);
    if let Some(id) = target_id {
        form_stack.remove(&id);
    }
    result
}

fn push_marked_content(
    document: &PdfDocument,
    marked_content: &mut Vec<MarkedContentProperties>,
    properties: MarkedContentProperties,
    operation: &ContentOperation,
) -> PdfResult<()> {
    if marked_content.len() >= document.limits.max_object_depth {
        return Err(limit("image marked-content depth limit exceeded"));
    }
    let properties = properties.inherit_context(
        marked_content.last().is_some_and(|parent| parent.artifact),
        marked_content
            .last()
            .and_then(|parent| parent.alt_text.clone()),
    );
    if properties.mcid.is_some_and(|mcid| mcid < 0) {
        return invalid(operation, "image marked-content MCID must be non-negative");
    }
    marked_content.push(properties);
    Ok(())
}

fn join_resource_name(prefix: &str, name: &PdfName) -> String {
    let component = String::from_utf8_lossy(name.as_bytes());
    if prefix.is_empty() {
        component.into_owned()
    } else {
        format!("{prefix}/{component}")
    }
}

#[allow(clippy::too_many_arguments)]
fn append_image_placement(
    document: &PdfDocument,
    page: &PdfPage,
    state: GraphicsState,
    resource_name: String,
    object: Option<crate::ObjectId>,
    marked_content: Option<&MarkedContentProperties>,
    output: &mut PageVectorPaths,
    collected_images: &mut usize,
) -> PdfResult<()> {
    *collected_images = collected_images
        .checked_add(1)
        .ok_or_else(|| limit("image placement count overflow"))?;
    if *collected_images > document.limits.max_images {
        return Err(limit("image placement count limit exceeded"));
    }
    let paint_ordinal = output.next_image_paint_ordinal;
    output.next_image_paint_ordinal = output
        .next_image_paint_ordinal
        .checked_add(1)
        .ok_or_else(|| limit("image paint ordinal overflow"))?;
    let quad = Quad {
        top_left: layout_point(&page.geometry, state.ctm, 0.0, 1.0)?,
        top_right: layout_point(&page.geometry, state.ctm, 1.0, 1.0)?,
        bottom_right: layout_point(&page.geometry, state.ctm, 1.0, 0.0)?,
        bottom_left: layout_point(&page.geometry, state.ctm, 0.0, 0.0)?,
    };
    let bbox = quad.bounding_box()?;
    output.image_placements.push(LayoutImagePlacement {
        id: format!("p{}-i{paint_ordinal}", page.index),
        paint_ordinal,
        resource_name,
        object,
        bbox,
        quad,
        source_node_ids: Vec::new(),
        tag: marked_content.and_then(|properties| properties.tag.clone()),
        artifact: marked_content.is_some_and(|properties| properties.artifact),
        structure_object: None,
        alt_text: marked_content.and_then(|properties| properties.alt_text.clone()),
        confidence: 1.0,
        rule_id: "stage5a_image_do_v1".to_owned(),
        provenance: LayoutProvenance {
            page_object: page.id,
            source_ordinal_start: paint_ordinal,
            source_ordinal_end: paint_ordinal,
            mcids: marked_content
                .and_then(|properties| properties.mcid)
                .into_iter()
                .collect(),
            text_origins: Vec::new(),
        },
    });
    Ok(())
}

fn add_line(
    path: &mut PathState,
    point: Point,
    max_segments: usize,
    operation: &ContentOperation,
) -> PdfResult<()> {
    let Some(start) = path.current else {
        return invalid(operation, "line path has no current point");
    };
    if path.pending.len() >= max_segments {
        return Err(limit("pending vector path segment limit exceeded"));
    }
    if start != point {
        path.pending.push(VectorSegment { start, end: point });
    }
    path.current = Some(point);
    Ok(())
}

fn close_path(
    path: &mut PathState,
    max_segments: usize,
    operation: &ContentOperation,
) -> PdfResult<()> {
    if let Some(start) = path.subpath_start
        && path.current.is_some()
    {
        add_line(path, start, max_segments, operation)?;
    }
    Ok(())
}

fn stroke_path(
    path: &mut PathState,
    output: &mut PageVectorPaths,
    collected_segments: &mut usize,
    max_segments: usize,
) -> PdfResult<()> {
    let next_count = collected_segments
        .checked_add(path.pending.len())
        .ok_or_else(|| limit("collected vector path segment count overflow"))?;
    if next_count > max_segments {
        return Err(limit("collected vector path segment limit exceeded"));
    }
    *collected_segments = next_count;
    output.segments.append(&mut path.pending);
    path.current = None;
    path.subpath_start = None;
    Ok(())
}

fn clear_path(path: &mut PathState) {
    path.current = None;
    path.subpath_start = None;
    path.pending.clear();
}

fn update_curve_endpoint(
    operation: &ContentOperation,
    geometry: &PageGeometry,
    ctm: Matrix,
    path: &mut PathState,
    endpoint_index: usize,
) -> PdfResult<()> {
    require_operands(operation, endpoint_index + 2)?;
    if path.current.is_none() {
        return invalid(operation, "curve path has no current point");
    }
    let x = number(&operation.operands[endpoint_index], operation)?;
    let y = number(&operation.operands[endpoint_index + 1], operation)?;
    path.current = Some(layout_point(geometry, ctm, x, y)?);
    Ok(())
}

fn layout_point(geometry: &PageGeometry, ctm: Matrix, x: f64, y: f64) -> PdfResult<Point> {
    let point = ctm.transform(x, y);
    let projected = geometry.pdf_point_to_layout(Point::try_new(point.0, point.1)?);
    Point::try_new(projected.x, projected.y)
}

fn require_operands(operation: &ContentOperation, expected: usize) -> PdfResult<()> {
    if operation.operands.len() == expected {
        Ok(())
    } else {
        invalid(
            operation,
            &format!(
                "{} expects {expected} operand(s), got {}",
                String::from_utf8_lossy(&operation.operator),
                operation.operands.len()
            ),
        )
    }
}

fn two_numbers(operation: &ContentOperation) -> PdfResult<[f64; 2]> {
    require_operands(operation, 2)?;
    Ok([
        number(&operation.operands[0], operation)?,
        number(&operation.operands[1], operation)?,
    ])
}

fn four_numbers(operation: &ContentOperation) -> PdfResult<[f64; 4]> {
    require_operands(operation, 4)?;
    Ok([
        number(&operation.operands[0], operation)?,
        number(&operation.operands[1], operation)?,
        number(&operation.operands[2], operation)?,
        number(&operation.operands[3], operation)?,
    ])
}

fn six_numbers(operation: &ContentOperation) -> PdfResult<[f64; 6]> {
    require_operands(operation, 6)?;
    Ok([
        number(&operation.operands[0], operation)?,
        number(&operation.operands[1], operation)?,
        number(&operation.operands[2], operation)?,
        number(&operation.operands[3], operation)?,
        number(&operation.operands[4], operation)?,
        number(&operation.operands[5], operation)?,
    ])
}

#[allow(clippy::cast_precision_loss)]
fn number(object: &PdfObject, operation: &ContentOperation) -> PdfResult<f64> {
    let value = match object {
        PdfObject::Integer(value) => *value as f64,
        PdfObject::Real(value) => *value,
        _ => return invalid(operation, "numeric operand required"),
    };
    if value.is_finite() {
        Ok(value)
    } else {
        invalid(operation, "numeric operand must be finite")
    }
}

fn invalid<T>(operation: &ContentOperation, message: &str) -> PdfResult<T> {
    Err(PdfError::new(
        ErrorCode::InvalidObject,
        Some(operation.offset),
        message,
    ))
}

fn limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}
