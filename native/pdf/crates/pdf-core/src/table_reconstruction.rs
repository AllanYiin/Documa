use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::{
    BBox, ErrorCode, LayoutNode, LayoutProvenance, LayoutTable, LayoutTableCell,
    LayoutTableCellRole, LayoutTableEvidence, LayoutWarning, PageLayout, ParseLimits, PdfError,
    PdfResult,
    tagged_structure::{
        TaggedTable, TaggedTableCell, TaggedTableCellKind, TaggedTableScope,
        tagged_table_page_index,
    },
    vector_paths::{PageVectorPaths, VectorSegment},
};

const TAGGED_TABLE_RULE_ID: &str = "stage4a_tagged_table_v1";
const TAGGED_CELL_RULE_ID: &str = "stage4a_tagged_cell_v1";
const TAGGED_CONFIDENCE: f32 = 0.98;
const EMPTY_CELL_CONFIDENCE: f32 = 0.75;
const VECTOR_TABLE_RULE_ID: &str = "stage4b_vector_lattice_v1";
const VECTOR_CELL_RULE_ID: &str = "stage4b_vector_cell_v1";
const VECTOR_TABLE_CONFIDENCE: f32 = 0.92;
const VECTOR_EMPTY_CELL_CONFIDENCE: f32 = 0.85;
const AXIS_TOLERANCE: f64 = 0.5;
const EDGE_GAP_TOLERANCE: f64 = 1.0;
const MIN_EDGE_LENGTH: f64 = 3.0;
const TEXT_TABLE_RULE_ID: &str = "stage4c_text_alignment_v1";
const TEXT_CELL_RULE_ID: &str = "stage4c_text_cell_v1";
const TEXT_TABLE_CONFIDENCE: f32 = 0.84;
const FUSED_TABLE_RULE_ID: &str = "stage4d_tagged_vector_fusion_v1";
const FUSED_CELL_RULE_ID: &str = "stage4d_tagged_vector_cell_v1";
const FUSED_TABLE_CONFIDENCE: f32 = 0.99;

#[derive(Debug)]
struct PlacedCell<'a> {
    row: usize,
    column: usize,
    cell: &'a TaggedTableCell,
}

#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn apply_tagged_tables(
    pages: &mut [PageLayout],
    tables: &[TaggedTable],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    for source_table_index in 0..tables.len() {
        let page_position = tagged_table_page_index(&tables[source_table_index])
            .ok()
            .flatten()
            .and_then(|page_index| pages.iter().position(|page| page.page_index == page_index));
        if let Some(page_position) = page_position {
            apply_page_tagged_tables(
                &mut pages[page_position],
                tables,
                &[source_table_index],
                limits,
                warnings,
            )?;
        } else {
            apply_tagged_table_indices(
                pages,
                tables,
                std::iter::once(source_table_index),
                limits,
                warnings,
            )?;
        }
    }
    Ok(())
}

pub(crate) fn apply_page_tagged_tables(
    page: &mut PageLayout,
    tables: &[TaggedTable],
    table_indices: &[usize],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    apply_tagged_table_indices(
        std::slice::from_mut(page),
        tables,
        table_indices.iter().copied(),
        limits,
        warnings,
    )
}

#[allow(clippy::too_many_lines)]
pub(crate) fn apply_tagged_table_indices(
    pages: &mut [PageLayout],
    tables: &[TaggedTable],
    table_indices: impl IntoIterator<Item = usize>,
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    let mut warning_keys = warnings
        .iter()
        .map(|warning| (warning.code.clone(), warning.page_index))
        .collect::<BTreeSet<_>>();
    for source_table_index in table_indices {
        let table = tables.get(source_table_index).ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "tagged table page index references an unknown table",
            )
        })?;
        let page_index = match tagged_table_page_index(table) {
            Ok(Some(page_index)) => page_index,
            Ok(None) => {
                warn_once(
                    warnings,
                    &mut warning_keys,
                    "tagged_table_invalid",
                    table.page_index,
                    "tagged table has no page association",
                );
                continue;
            }
            Err(()) => {
                warn_once(
                    warnings,
                    &mut warning_keys,
                    "tagged_table_invalid",
                    table.page_index,
                    "tagged table content spans multiple pages",
                );
                continue;
            }
        };
        let Some(page_position) = pages.iter().position(|page| page.page_index == page_index)
        else {
            warn_once(
                warnings,
                &mut warning_keys,
                "tagged_table_invalid",
                Some(page_index),
                "tagged table references an unknown page",
            );
            continue;
        };
        let page = &mut pages[page_position];
        if page.tables.len() >= limits.max_tables {
            return Err(limit("accepted table limit exceeded"));
        }
        let Some((placed, columns)) = place_tagged_cells(table, limits)? else {
            warn_once(
                warnings,
                &mut warning_keys,
                "tagged_table_invalid",
                Some(page_index),
                "tagged table does not form a non-overlapping rectangular grid",
            );
            continue;
        };
        let table_index = page.tables.len();
        let node_indices = mcid_node_indices(page);
        let mut cells = Vec::with_capacity(placed.len());
        let mut table_node_ids = Vec::new();
        let mut table_node_set = BTreeSet::new();
        let mut table_bbox = None;
        let mut table_provenance = None;
        let mut confidence = TAGGED_CONFIDENCE;
        for (cell_index, placed_cell) in placed.into_iter().enumerate() {
            let mut source_node_ids = Vec::new();
            let mut source_node_set = BTreeSet::new();
            let mut selected_indices = Vec::new();
            for (association_page, mcid) in &placed_cell.cell.associations {
                if *association_page != page_index {
                    continue;
                }
                if let Some(indices) = node_indices.get(mcid) {
                    for &index in indices {
                        if source_node_set.insert(page.semantic_nodes[index].id.clone()) {
                            selected_indices.push(index);
                            source_node_ids.push(page.semantic_nodes[index].id.clone());
                        }
                    }
                }
            }
            if placed_cell.cell.associations.is_empty() {
                warn_once(
                    warnings,
                    &mut warning_keys,
                    "table_cell_unassigned",
                    Some(page_index),
                    "tagged table cell has no marked-content association",
                );
            } else if selected_indices.is_empty() {
                warn_once(
                    warnings,
                    &mut warning_keys,
                    "table_cell_unassigned",
                    Some(page_index),
                    "tagged table cell MCIDs have no collected page node",
                );
            }
            let selected_nodes = selected_indices
                .iter()
                .map(|&index| &page.semantic_nodes[index])
                .collect::<Vec<_>>();
            let text = join_node_text(&selected_nodes);
            let bbox = union_node_bbox(&selected_nodes)?;
            let provenance = merge_node_provenance(&selected_nodes)?;
            if bbox.is_none() {
                confidence = confidence.min(EMPTY_CELL_CONFIDENCE);
            }
            if let Some(cell_bbox) = bbox {
                table_bbox = Some(match table_bbox {
                    Some(current) => union_bbox(current, cell_bbox)?,
                    None => cell_bbox,
                });
            }
            if let Some(cell_provenance) = provenance.as_ref() {
                table_provenance = Some(match table_provenance {
                    Some(current) => merge_provenance(current, cell_provenance)?,
                    None => cell_provenance.clone(),
                });
            }
            for node_id in &source_node_ids {
                if table_node_set.insert(node_id.clone()) {
                    table_node_ids.push(node_id.clone());
                }
            }
            cells.push(LayoutTableCell {
                id: format!("p{page_index}-t{table_index}-c{cell_index}"),
                row: placed_cell.row,
                column: placed_cell.column,
                row_span: placed_cell.cell.row_span,
                column_span: placed_cell.cell.column_span,
                role: cell_role(placed_cell.cell, placed_cell.row),
                text,
                bbox,
                source_node_ids,
                confidence: if selected_nodes.is_empty() {
                    EMPTY_CELL_CONFIDENCE
                } else {
                    TAGGED_CONFIDENCE
                },
                rule_id: TAGGED_CELL_RULE_ID.to_owned(),
                structure_object: placed_cell.cell.structure_object,
                provenance,
            });
        }
        page.tables.push(LayoutTable {
            id: format!("p{page_index}-t{table_index}"),
            bbox: table_bbox,
            rows: table.rows.len(),
            columns,
            cells,
            evidence: LayoutTableEvidence::Tagged,
            source_node_ids: table_node_ids,
            confidence,
            rule_id: TAGGED_TABLE_RULE_ID.to_owned(),
            structure_object: table.structure_object,
            provenance: table_provenance,
        });
    }
    Ok(())
}

#[derive(Debug, Clone, Copy)]
enum AxisEdge {
    Horizontal { fixed: f64, start: f64, end: f64 },
    Vertical { fixed: f64, start: f64, end: f64 },
}

#[derive(Debug, Clone, Copy)]
struct GridCandidate {
    row_start: usize,
    row_end: usize,
    column_start: usize,
    column_end: usize,
}

#[derive(Debug, Default)]
pub(crate) struct VectorTableState {
    warning_keys: BTreeSet<(String, Option<usize>)>,
}

impl VectorTableState {
    pub(crate) fn from_warnings(warnings: &[LayoutWarning]) -> Self {
        Self {
            warning_keys: warnings
                .iter()
                .map(|warning| (warning.code.clone(), warning.page_index))
                .collect(),
        }
    }

    pub(crate) fn observe_warnings(&mut self, warnings: &[LayoutWarning]) {
        self.warning_keys.extend(
            warnings
                .iter()
                .map(|warning| (warning.code.clone(), warning.page_index)),
        );
    }
}

#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn apply_vector_tables(
    pages: &mut [PageLayout],
    paths: Vec<PageVectorPaths>,
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    if pages.len() != paths.len() {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "page and vector-path counts differ",
        ));
    }
    let mut state = VectorTableState::from_warnings(warnings);
    for (page, page_paths) in pages.iter_mut().zip(paths) {
        apply_page_vector_tables(page, &page_paths.segments, limits, warnings, &mut state)?;
    }
    Ok(())
}

#[allow(clippy::too_many_lines)]
pub(crate) fn apply_page_vector_tables(
    page: &mut PageLayout,
    segments: &[VectorSegment],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
    state: &mut VectorTableState,
) -> PdfResult<()> {
    let (horizontal, vertical) = normalize_and_merge_edges(segments);
    if horizontal.len() < 3 || vertical.len() < 3 {
        return Ok(());
    }
    let xs = unique_coordinates(vertical.iter().map(edge_fixed));
    let ys = unique_coordinates(horizontal.iter().map(edge_fixed));
    if xs.len() < 3 || ys.len() < 3 {
        return Ok(());
    }
    let columns = xs.len() - 1;
    let rows = ys.len() - 1;
    let candidate_cells = rows
        .checked_mul(columns)
        .ok_or_else(|| limit("vector table candidate count overflow"))?;
    if candidate_cells > limits.max_table_candidates {
        return Err(limit("vector table candidate limit exceeded"));
    }
    let mut closed = vec![false; candidate_cells];
    for row in 0..rows {
        for column in 0..columns {
            closed[row * columns + column] =
                edge_covers(&horizontal, ys[row], xs[column], xs[column + 1])
                    && edge_covers(&horizontal, ys[row + 1], xs[column], xs[column + 1])
                    && edge_covers(&vertical, xs[column], ys[row], ys[row + 1])
                    && edge_covers(&vertical, xs[column + 1], ys[row], ys[row + 1]);
        }
    }
    let candidates = rectangular_components(&closed, rows, columns, limits)?;
    if candidates.is_empty() {
        return Ok(());
    }
    let mut owner = vec![None; candidate_cells];
    for (candidate_index, candidate) in candidates.iter().enumerate() {
        for row in candidate.row_start..=candidate.row_end {
            for column in candidate.column_start..=candidate.column_end {
                owner[row * columns + column] = Some(candidate_index);
            }
        }
    }
    let mut assigned = candidates
        .iter()
        .map(|candidate| {
            vec![
                Vec::<usize>::new();
                (candidate.row_end - candidate.row_start + 1)
                    * (candidate.column_end - candidate.column_start + 1)
            ]
        })
        .collect::<Vec<_>>();
    for (node_index, node) in page.semantic_nodes.iter().enumerate() {
        let center_x = (node.bbox.x0 + node.bbox.x1) * 0.5;
        let center_y = (node.bbox.y0 + node.bbox.y1) * 0.5;
        let (Some(column), Some(row)) = (
            coordinate_interval(&xs, center_x),
            coordinate_interval(&ys, center_y),
        ) else {
            continue;
        };
        let Some(candidate_index) = owner[row * columns + column] else {
            continue;
        };
        let candidate = &candidates[candidate_index];
        let local_columns = candidate.column_end - candidate.column_start + 1;
        let local_index =
            (row - candidate.row_start) * local_columns + (column - candidate.column_start);
        assigned[candidate_index][local_index].push(node_index);
    }
    for (candidate, cell_nodes) in candidates.into_iter().zip(assigned) {
        let local_rows = candidate.row_end - candidate.row_start + 1;
        let local_columns = candidate.column_end - candidate.column_start + 1;
        let total_cells = local_rows
            .checked_mul(local_columns)
            .ok_or_else(|| limit("vector table logical cell count overflow"))?;
        if total_cells > limits.max_table_cells {
            return Err(limit("vector table logical cell limit exceeded"));
        }
        let non_empty = cell_nodes.iter().filter(|nodes| !nodes.is_empty()).count();
        let required = total_cells.div_ceil(2).max(3).min(total_cells);
        let occupied_rows = (0..local_rows)
            .filter(|row| {
                cell_nodes[*row * local_columns..(*row + 1) * local_columns]
                    .iter()
                    .any(|nodes| !nodes.is_empty())
            })
            .count();
        let occupied_columns = (0..local_columns)
            .filter(|column| {
                (0..local_rows).any(|row| !cell_nodes[row * local_columns + column].is_empty())
            })
            .count();
        if non_empty < required || occupied_rows < 2 || occupied_columns < 2 {
            warn_once(
                warnings,
                &mut state.warning_keys,
                "table_detection_ambiguous",
                Some(page.page_index),
                "closed vector grid lacks enough distributed text for safe table acceptance",
            );
            continue;
        }
        let candidate_bbox = vector_candidate_bbox(&xs, &ys, candidate)?;
        if let Some(existing_index) = page.tables.iter().position(|table| {
            matches!(
                table.evidence,
                LayoutTableEvidence::Tagged | LayoutTableEvidence::Fused
            ) && table
                .bbox
                .is_some_and(|bbox| bboxes_have_area_overlap(bbox, candidate_bbox))
        }) {
            if page.tables[existing_index].rows == local_rows
                && page.tables[existing_index].columns == local_columns
            {
                fuse_tagged_vector_table(&mut page.tables[existing_index], &xs, &ys, candidate)?;
            } else {
                warn_once(
                    warnings,
                    &mut state.warning_keys,
                    "table_evidence_conflict",
                    Some(page.page_index),
                    "tagged table topology conflicts with overlapping vector lattice",
                );
            }
            continue;
        }
        if page.tables.len() >= limits.max_tables {
            return Err(limit("accepted table limit exceeded"));
        }
        append_vector_table(
            page,
            &xs,
            &ys,
            candidate,
            cell_nodes,
            local_rows,
            local_columns,
        )?;
    }
    Ok(())
}

fn vector_candidate_bbox(xs: &[f64], ys: &[f64], candidate: GridCandidate) -> PdfResult<BBox> {
    BBox::try_new(
        xs[candidate.column_start],
        ys[candidate.row_start],
        xs[candidate.column_end + 1],
        ys[candidate.row_end + 1],
    )
}

fn bboxes_have_area_overlap(left: BBox, right: BBox) -> bool {
    left.x0.max(right.x0) < left.x1.min(right.x1) && left.y0.max(right.y0) < left.y1.min(right.y1)
}

fn fuse_tagged_vector_table(
    table: &mut LayoutTable,
    xs: &[f64],
    ys: &[f64],
    candidate: GridCandidate,
) -> PdfResult<()> {
    for cell in &mut table.cells {
        let row_end = cell
            .row
            .checked_add(cell.row_span)
            .ok_or_else(|| limit("fused table row span overflow"))?;
        let column_end = cell
            .column
            .checked_add(cell.column_span)
            .ok_or_else(|| limit("fused table column span overflow"))?;
        if row_end > table.rows || column_end > table.columns {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "tagged table span exceeds compatible vector lattice",
            ));
        }
        cell.bbox = Some(BBox::try_new(
            xs[candidate.column_start + cell.column],
            ys[candidate.row_start + cell.row],
            xs[candidate.column_start + column_end],
            ys[candidate.row_start + row_end],
        )?);
        cell.confidence = cell.confidence.max(FUSED_TABLE_CONFIDENCE);
        FUSED_CELL_RULE_ID.clone_into(&mut cell.rule_id);
    }
    table.bbox = Some(vector_candidate_bbox(xs, ys, candidate)?);
    table.evidence = LayoutTableEvidence::Fused;
    table.confidence = table.confidence.max(FUSED_TABLE_CONFIDENCE);
    FUSED_TABLE_RULE_ID.clone_into(&mut table.rule_id);
    Ok(())
}

fn append_vector_table(
    page: &mut PageLayout,
    xs: &[f64],
    ys: &[f64],
    candidate: GridCandidate,
    cell_nodes: Vec<Vec<usize>>,
    rows: usize,
    columns: usize,
) -> PdfResult<()> {
    let table_index = page.tables.len();
    let mut cells = Vec::with_capacity(cell_nodes.len());
    let mut table_node_ids = Vec::new();
    let mut table_node_set = BTreeSet::new();
    let mut table_provenance = None;
    for (cell_index, indices) in cell_nodes.into_iter().enumerate() {
        let row = cell_index / columns;
        let column = cell_index % columns;
        let nodes = indices
            .iter()
            .map(|&index| &page.semantic_nodes[index])
            .collect::<Vec<_>>();
        let source_node_ids = nodes.iter().map(|node| node.id.clone()).collect::<Vec<_>>();
        for node_id in &source_node_ids {
            if table_node_set.insert(node_id.clone()) {
                table_node_ids.push(node_id.clone());
            }
        }
        let provenance = merge_node_provenance(&nodes)?;
        if let Some(cell_provenance) = provenance.as_ref() {
            table_provenance = Some(match table_provenance {
                Some(current) => merge_provenance(current, cell_provenance)?,
                None => cell_provenance.clone(),
            });
        }
        cells.push(LayoutTableCell {
            id: format!("p{}-t{table_index}-c{cell_index}", page.page_index),
            row,
            column,
            row_span: 1,
            column_span: 1,
            role: LayoutTableCellRole::Data,
            text: join_node_text(&nodes),
            bbox: Some(BBox::try_new(
                xs[candidate.column_start + column],
                ys[candidate.row_start + row],
                xs[candidate.column_start + column + 1],
                ys[candidate.row_start + row + 1],
            )?),
            source_node_ids,
            confidence: if nodes.is_empty() {
                VECTOR_EMPTY_CELL_CONFIDENCE
            } else {
                VECTOR_TABLE_CONFIDENCE
            },
            rule_id: VECTOR_CELL_RULE_ID.to_owned(),
            structure_object: None,
            provenance,
        });
    }
    page.tables.push(LayoutTable {
        id: format!("p{}-t{table_index}", page.page_index),
        bbox: Some(vector_candidate_bbox(xs, ys, candidate)?),
        rows,
        columns,
        cells,
        evidence: LayoutTableEvidence::VectorLattice,
        source_node_ids: table_node_ids,
        confidence: VECTOR_TABLE_CONFIDENCE,
        rule_id: VECTOR_TABLE_RULE_ID.to_owned(),
        structure_object: None,
        provenance: table_provenance,
    });
    Ok(())
}

fn normalize_and_merge_edges(segments: &[VectorSegment]) -> (Vec<AxisEdge>, Vec<AxisEdge>) {
    let mut horizontal = Vec::new();
    let mut vertical = Vec::new();
    for segment in segments {
        let dx = segment.end.x - segment.start.x;
        let dy = segment.end.y - segment.start.y;
        if dy.abs() <= AXIS_TOLERANCE && dx.abs() >= MIN_EDGE_LENGTH {
            horizontal.push(AxisEdge::Horizontal {
                fixed: (segment.start.y + segment.end.y) * 0.5,
                start: segment.start.x.min(segment.end.x),
                end: segment.start.x.max(segment.end.x),
            });
        } else if dx.abs() <= AXIS_TOLERANCE && dy.abs() >= MIN_EDGE_LENGTH {
            vertical.push(AxisEdge::Vertical {
                fixed: (segment.start.x + segment.end.x) * 0.5,
                start: segment.start.y.min(segment.end.y),
                end: segment.start.y.max(segment.end.y),
            });
        }
    }
    (merge_axis_edges(horizontal), merge_axis_edges(vertical))
}

fn merge_axis_edges(mut edges: Vec<AxisEdge>) -> Vec<AxisEdge> {
    edges.sort_by(|left, right| {
        edge_fixed(left)
            .total_cmp(&edge_fixed(right))
            .then_with(|| edge_start(left).total_cmp(&edge_start(right)))
            .then_with(|| edge_end(left).total_cmp(&edge_end(right)))
    });
    let mut merged = Vec::<AxisEdge>::new();
    for edge in edges {
        if let Some(previous) = merged.last_mut()
            && same_orientation(previous, &edge)
            && (edge_fixed(previous) - edge_fixed(&edge)).abs() <= AXIS_TOLERANCE
            && edge_start(&edge) <= edge_end(previous) + EDGE_GAP_TOLERANCE
        {
            set_edge_end(previous, edge_end(previous).max(edge_end(&edge)));
        } else {
            merged.push(edge);
        }
    }
    merged
}

fn rectangular_components(
    closed: &[bool],
    rows: usize,
    columns: usize,
    limits: &ParseLimits,
) -> PdfResult<Vec<GridCandidate>> {
    let mut visited = vec![false; closed.len()];
    let mut candidates = Vec::new();
    for start in 0..closed.len() {
        if !closed[start] || visited[start] {
            continue;
        }
        let mut queue = VecDeque::from([start]);
        visited[start] = true;
        let mut cells = Vec::new();
        while let Some(index) = queue.pop_front() {
            cells.push(index);
            let row = index / columns;
            let column = index % columns;
            for neighbor in [
                row.checked_sub(1).map(|value| value * columns + column),
                (row + 1 < rows).then_some((row + 1) * columns + column),
                column.checked_sub(1).map(|value| row * columns + value),
                (column + 1 < columns).then_some(row * columns + column + 1),
            ]
            .into_iter()
            .flatten()
            {
                if closed[neighbor] && !visited[neighbor] {
                    visited[neighbor] = true;
                    queue.push_back(neighbor);
                }
            }
        }
        let row_start = cells
            .iter()
            .map(|index| index / columns)
            .min()
            .expect("non-empty");
        let row_end = cells
            .iter()
            .map(|index| index / columns)
            .max()
            .expect("non-empty");
        let column_start = cells
            .iter()
            .map(|index| index % columns)
            .min()
            .expect("non-empty");
        let column_end = cells
            .iter()
            .map(|index| index % columns)
            .max()
            .expect("non-empty");
        let component_rows = row_end - row_start + 1;
        let component_columns = column_end - column_start + 1;
        let expected = component_rows
            .checked_mul(component_columns)
            .ok_or_else(|| limit("vector table component size overflow"))?;
        if component_rows >= 2 && component_columns >= 2 && expected == cells.len() {
            if candidates.len() >= limits.max_table_candidates {
                return Err(limit("vector table component limit exceeded"));
            }
            candidates.push(GridCandidate {
                row_start,
                row_end,
                column_start,
                column_end,
            });
        }
    }
    Ok(candidates)
}

fn unique_coordinates(values: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut values = values.collect::<Vec<_>>();
    values.sort_by(f64::total_cmp);
    let mut unique = Vec::<f64>::new();
    for value in values {
        if unique
            .last()
            .is_none_or(|previous| (value - *previous).abs() > AXIS_TOLERANCE)
        {
            unique.push(value);
        }
    }
    unique
}

fn coordinate_interval(coordinates: &[f64], value: f64) -> Option<usize> {
    if coordinates.len() < 2
        || value < coordinates[0] - AXIS_TOLERANCE
        || value > coordinates[coordinates.len() - 1] + AXIS_TOLERANCE
    {
        return None;
    }
    let upper = coordinates.partition_point(|coordinate| *coordinate <= value);
    Some(upper.saturating_sub(1).min(coordinates.len() - 2))
}

fn edge_covers(edges: &[AxisEdge], fixed: f64, start: f64, end: f64) -> bool {
    edges.iter().any(|edge| {
        (edge_fixed(edge) - fixed).abs() <= AXIS_TOLERANCE
            && edge_start(edge) <= start + AXIS_TOLERANCE
            && edge_end(edge) >= end - AXIS_TOLERANCE
    })
}

fn edge_fixed(edge: &AxisEdge) -> f64 {
    match edge {
        AxisEdge::Horizontal { fixed, .. } | AxisEdge::Vertical { fixed, .. } => *fixed,
    }
}

fn edge_start(edge: &AxisEdge) -> f64 {
    match edge {
        AxisEdge::Horizontal { start, .. } | AxisEdge::Vertical { start, .. } => *start,
    }
}

fn edge_end(edge: &AxisEdge) -> f64 {
    match edge {
        AxisEdge::Horizontal { end, .. } | AxisEdge::Vertical { end, .. } => *end,
    }
}

fn same_orientation(left: &AxisEdge, right: &AxisEdge) -> bool {
    matches!(
        (left, right),
        (AxisEdge::Horizontal { .. }, AxisEdge::Horizontal { .. })
            | (AxisEdge::Vertical { .. }, AxisEdge::Vertical { .. })
    )
}

fn set_edge_end(edge: &mut AxisEdge, value: f64) {
    match edge {
        AxisEdge::Horizontal { end, .. } | AxisEdge::Vertical { end, .. } => *end = value,
    }
}
#[derive(Debug)]
struct TextRow {
    y0: f64,
    y1: f64,
    nodes: Vec<usize>,
}

#[derive(Debug, Default)]
pub(crate) struct TextTableState {
    warning_keys: BTreeSet<(String, Option<usize>)>,
    candidate_count: usize,
}

impl TextTableState {
    pub(crate) fn from_warnings(warnings: &[LayoutWarning]) -> Self {
        Self {
            warning_keys: warnings
                .iter()
                .map(|warning| (warning.code.clone(), warning.page_index))
                .collect(),
            candidate_count: 0,
        }
    }

    pub(crate) fn observe_warnings(&mut self, warnings: &[LayoutWarning]) {
        self.warning_keys.extend(
            warnings
                .iter()
                .map(|warning| (warning.code.clone(), warning.page_index)),
        );
    }
}

#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn apply_text_tables(
    pages: &mut [PageLayout],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<()> {
    let mut state = TextTableState::from_warnings(warnings);
    for page in pages {
        apply_page_text_tables(page, limits, warnings, &mut state)?;
    }
    Ok(())
}

pub(crate) fn apply_page_text_tables(
    page: &mut PageLayout,
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
    state: &mut TextTableState,
) -> PdfResult<()> {
    let claimed_nodes = page
        .tables
        .iter()
        .flat_map(|table| table.source_node_ids.iter().cloned())
        .collect::<BTreeSet<_>>();
    let mut indices = page
        .semantic_nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| text_table_node_eligible(node) && !claimed_nodes.contains(&node.id))
        .map(|(index, _)| index)
        .collect::<Vec<_>>();
    indices.sort_by(|left, right| {
        page.semantic_nodes[*left]
            .bbox
            .y0
            .total_cmp(&page.semantic_nodes[*right].bbox.y0)
            .then_with(|| {
                page.semantic_nodes[*left]
                    .bbox
                    .x0
                    .total_cmp(&page.semantic_nodes[*right].bbox.x0)
            })
            .then_with(|| {
                page.semantic_nodes[*left]
                    .id
                    .cmp(&page.semantic_nodes[*right].id)
            })
    });
    let mut rows = Vec::<TextRow>::new();
    for index in indices {
        let bbox = page.semantic_nodes[index].bbox;
        if let Some(row) = rows.last_mut()
            && same_text_row(row, bbox)
        {
            row.y0 = row.y0.min(bbox.y0);
            row.y1 = row.y1.max(bbox.y1);
            row.nodes.push(index);
        } else {
            rows.push(TextRow {
                y0: bbox.y0,
                y1: bbox.y1,
                nodes: vec![index],
            });
        }
    }
    for row in &mut rows {
        row.nodes.sort_by(|left, right| {
            page.semantic_nodes[*left]
                .bbox
                .x0
                .total_cmp(&page.semantic_nodes[*right].bbox.x0)
                .then_with(|| {
                    page.semantic_nodes[*left]
                        .id
                        .cmp(&page.semantic_nodes[*right].id)
                })
        });
    }
    let rows = rows
        .into_iter()
        .filter(|row| row.nodes.len() >= 2)
        .collect::<Vec<_>>();
    if rows.len() < 3 {
        return Ok(());
    }
    let median_height = median(
        rows.iter()
            .map(|row| row.y1 - row.y0)
            .filter(|height| *height > 0.0)
            .collect(),
    )
    .unwrap_or(1.0);
    let mut group_start = 0;
    while group_start < rows.len() {
        let mut group_end = group_start + 1;
        while group_end < rows.len()
            && rows[group_end].y0 - rows[group_end - 1].y1 <= median_height * 5.0 + 2.0
        {
            group_end += 1;
        }
        detect_text_row_group(
            page,
            &rows[group_start..group_end],
            limits,
            warnings,
            &mut state.warning_keys,
            &mut state.candidate_count,
        )?;
        group_start = group_end;
    }
    Ok(())
}

fn detect_text_row_group(
    page: &mut PageLayout,
    rows: &[TextRow],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
    warning_keys: &mut BTreeSet<(String, Option<usize>)>,
    candidate_count: &mut usize,
) -> PdfResult<()> {
    let mut start = 0;
    while start < rows.len() {
        let columns = rows[start].nodes.len();
        let mut end = start + 1;
        while end < rows.len() && rows[end].nodes.len() == columns {
            end += 1;
        }
        let run = &rows[start..end];
        if run.len() >= 3 && columns >= 2 {
            *candidate_count = candidate_count
                .checked_add(1)
                .ok_or_else(|| limit("text-alignment table candidate count overflow"))?;
            if *candidate_count > limits.max_table_candidates {
                return Err(limit("text-alignment table candidate limit exceeded"));
            }
            if text_run_is_table(page, run, columns) {
                append_text_table(page, run, columns, limits)?;
            } else {
                warn_once(
                    warnings,
                    warning_keys,
                    "table_detection_ambiguous",
                    Some(page.page_index),
                    "repeated text rows lack safe table alignment or two-column evidence",
                );
            }
        }
        start = end;
    }
    Ok(())
}

fn text_run_is_table(page: &PageLayout, rows: &[TextRow], columns: usize) -> bool {
    if columns == 2 {
        if rows.len() < 4 || key_value_like(page, rows) {
            return false;
        }
        let numeric_column = (0..columns).any(|column| {
            rows.iter()
                .skip(1)
                .filter(|row| numeric_like(&page.semantic_nodes[row.nodes[column]].text))
                .count()
                * 4
                >= rows.len().saturating_sub(1) * 3
        });
        if !numeric_column {
            return false;
        }
    }
    let tolerance = median(
        rows.iter()
            .flat_map(|row| {
                row.nodes
                    .iter()
                    .map(|index| page.semantic_nodes[*index].bbox.height())
            })
            .collect(),
    )
    .unwrap_or(1.0)
    .mul_add(0.75, 0.0)
    .max(3.0);
    for column in 0..columns {
        let x0 = rows
            .iter()
            .map(|row| page.semantic_nodes[row.nodes[column]].bbox.x0)
            .collect::<Vec<_>>();
        let x1 = rows
            .iter()
            .map(|row| page.semantic_nodes[row.nodes[column]].bbox.x1)
            .collect::<Vec<_>>();
        if spread(&x0) > tolerance && spread(&x1) > tolerance {
            return false;
        }
    }
    rows.iter().all(|row| {
        row.nodes.windows(2).all(|pair| {
            page.semantic_nodes[pair[1]].bbox.x0 - page.semantic_nodes[pair[0]].bbox.x1 >= 4.0
        })
    })
}

fn append_text_table(
    page: &mut PageLayout,
    rows: &[TextRow],
    columns: usize,
    limits: &ParseLimits,
) -> PdfResult<()> {
    if page.tables.len() >= limits.max_tables {
        return Err(limit("accepted table limit exceeded"));
    }
    let cell_count = rows
        .len()
        .checked_mul(columns)
        .ok_or_else(|| limit("text-alignment table cell count overflow"))?;
    if cell_count > limits.max_table_cells {
        return Err(limit("text-alignment table cell limit exceeded"));
    }
    let table_index = page.tables.len();
    let mut cells = Vec::with_capacity(cell_count);
    let mut table_bbox = None;
    let mut table_provenance = None;
    let mut source_node_ids = Vec::with_capacity(cell_count);
    for (row_index, row) in rows.iter().enumerate() {
        for (column, &node_index) in row.nodes.iter().enumerate() {
            let node = &page.semantic_nodes[node_index];
            table_bbox = Some(match table_bbox {
                Some(current) => union_bbox(current, node.bbox)?,
                None => node.bbox,
            });
            table_provenance = Some(match table_provenance {
                Some(current) => merge_provenance(current, &node.provenance)?,
                None => node.provenance.clone(),
            });
            source_node_ids.push(node.id.clone());
            let cell_index = row_index * columns + column;
            cells.push(LayoutTableCell {
                id: format!("p{}-t{table_index}-c{cell_index}", page.page_index),
                row: row_index,
                column,
                row_span: 1,
                column_span: 1,
                role: LayoutTableCellRole::Data,
                text: node.text.clone(),
                bbox: Some(node.bbox),
                source_node_ids: vec![node.id.clone()],
                confidence: TEXT_TABLE_CONFIDENCE,
                rule_id: TEXT_CELL_RULE_ID.to_owned(),
                structure_object: None,
                provenance: Some(node.provenance.clone()),
            });
        }
    }
    page.tables.push(LayoutTable {
        id: format!("p{}-t{table_index}", page.page_index),
        bbox: table_bbox,
        rows: rows.len(),
        columns,
        cells,
        evidence: LayoutTableEvidence::TextAlignment,
        source_node_ids,
        confidence: TEXT_TABLE_CONFIDENCE,
        rule_id: TEXT_TABLE_RULE_ID.to_owned(),
        structure_object: None,
        provenance: table_provenance,
    });
    Ok(())
}

fn text_table_node_eligible(node: &LayoutNode) -> bool {
    matches!(
        node.role,
        crate::LayoutNodeRole::Unclassified
            | crate::LayoutNodeRole::Paragraph
            | crate::LayoutNodeRole::Heading
    ) && node
        .spans
        .iter()
        .all(|span| span.rotation == 0 && span.writing_mode == crate::WritingMode::Horizontal)
}

fn same_text_row(row: &TextRow, bbox: BBox) -> bool {
    let overlap = (row.y1.min(bbox.y1) - row.y0.max(bbox.y0)).max(0.0);
    let minimum_height = (row.y1 - row.y0).min(bbox.height()).max(f64::EPSILON);
    let center_delta = ((row.y0 + row.y1) - (bbox.y0 + bbox.y1)).abs() * 0.5;
    overlap / minimum_height >= 0.6 || center_delta <= minimum_height * 0.4
}

fn key_value_like(page: &PageLayout, rows: &[TextRow]) -> bool {
    rows.iter()
        .filter(|row| {
            page.semantic_nodes[row.nodes[0]]
                .text
                .trim_end()
                .ends_with(':')
        })
        .count()
        * 2
        >= rows.len()
}

fn numeric_like(text: &str) -> bool {
    let trimmed = text.trim();
    let has_digit = trimmed.chars().any(|character| character.is_ascii_digit());
    has_digit
        && trimmed.chars().all(|character| {
            character.is_ascii_digit()
                || character.is_ascii_whitespace()
                || matches!(
                    character,
                    '.' | ',' | '-' | '+' | '%' | '$' | '(' | ')' | '/' | ':'
                )
        })
}

fn spread(values: &[f64]) -> f64 {
    let minimum = values.iter().copied().fold(f64::INFINITY, f64::min);
    let maximum = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    maximum - minimum
}

fn median(mut values: Vec<f64>) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(f64::total_cmp);
    Some(values[values.len() / 2])
}

fn place_tagged_cells<'a>(
    table: &'a TaggedTable,
    limits: &ParseLimits,
) -> PdfResult<Option<(Vec<PlacedCell<'a>>, usize)>> {
    if table.rows.is_empty() {
        return Ok(None);
    }
    let row_count = table.rows.len();
    let mut occupancy = vec![Vec::<bool>::new(); row_count];
    let mut placed = Vec::new();
    for (row_index, row) in table.rows.iter().enumerate() {
        if row.cells.is_empty() {
            return Ok(None);
        }
        for cell in &row.cells {
            if !cell.valid || cell.row_span == 0 || cell.column_span == 0 {
                return Ok(None);
            }
            let Some(row_end) = row_index.checked_add(cell.row_span) else {
                return Err(limit("tagged table row span overflow"));
            };
            if row_end > row_count {
                return Ok(None);
            }
            let column = occupancy[row_index]
                .iter()
                .position(|occupied| !occupied)
                .unwrap_or(occupancy[row_index].len());
            let Some(column_end) = column.checked_add(cell.column_span) else {
                return Err(limit("tagged table column span overflow"));
            };
            if column_end > limits.max_table_cells {
                return Err(limit("tagged table column limit exceeded"));
            }
            for occupied_row in occupancy.iter_mut().take(row_end).skip(row_index) {
                occupied_row.resize(column_end, false);
                if occupied_row[column..column_end]
                    .iter()
                    .any(|occupied| *occupied)
                {
                    return Ok(None);
                }
                occupied_row[column..column_end].fill(true);
            }
            if placed.len() >= limits.max_table_cells {
                return Err(limit("tagged table cell limit exceeded"));
            }
            placed.push(PlacedCell {
                row: row_index,
                column,
                cell,
            });
        }
    }
    let columns = occupancy.iter().map(Vec::len).max().unwrap_or(0);
    let Some(logical_cells) = row_count.checked_mul(columns) else {
        return Err(limit("tagged table logical cell count overflow"));
    };
    if logical_cells > limits.max_table_cells {
        return Err(limit("tagged table logical cell limit exceeded"));
    }
    if columns == 0
        || occupancy
            .iter()
            .any(|row| row.len() != columns || row.iter().any(|occupied| !occupied))
    {
        return Ok(None);
    }
    Ok(Some((placed, columns)))
}

fn mcid_node_indices(page: &PageLayout) -> BTreeMap<i64, Vec<usize>> {
    let mut indices = BTreeMap::<i64, Vec<usize>>::new();
    for (node_index, node) in page.semantic_nodes.iter().enumerate() {
        for mcid in &node.provenance.mcids {
            indices.entry(*mcid).or_default().push(node_index);
        }
    }
    indices
}

fn cell_role(cell: &TaggedTableCell, row: usize) -> LayoutTableCellRole {
    match (cell.kind, cell.scope) {
        (TaggedTableCellKind::Data, _) => LayoutTableCellRole::Data,
        (TaggedTableCellKind::Header, Some(TaggedTableScope::Row)) => {
            LayoutTableCellRole::RowHeader
        }
        (TaggedTableCellKind::Header, Some(TaggedTableScope::Column)) => {
            LayoutTableCellRole::ColumnHeader
        }
        (TaggedTableCellKind::Header, Some(TaggedTableScope::Both)) => {
            LayoutTableCellRole::BothHeader
        }
        (TaggedTableCellKind::Header, None) if row == 0 => LayoutTableCellRole::ColumnHeader,
        (TaggedTableCellKind::Header, None) => LayoutTableCellRole::RowHeader,
    }
}

fn join_node_text(nodes: &[&LayoutNode]) -> String {
    let mut output = String::new();
    for node in nodes {
        if !output.is_empty()
            && !output.ends_with(char::is_whitespace)
            && !node.text.starts_with(char::is_whitespace)
        {
            output.push('\n');
        }
        output.push_str(&node.text);
    }
    output
}

fn union_node_bbox(nodes: &[&LayoutNode]) -> PdfResult<Option<BBox>> {
    let mut bbox = None;
    for node in nodes {
        bbox = Some(match bbox {
            Some(current) => union_bbox(current, node.bbox)?,
            None => node.bbox,
        });
    }
    Ok(bbox)
}

fn merge_node_provenance(nodes: &[&LayoutNode]) -> PdfResult<Option<LayoutProvenance>> {
    let mut provenance = None;
    for node in nodes {
        provenance = Some(match provenance {
            Some(current) => merge_provenance(current, &node.provenance)?,
            None => node.provenance.clone(),
        });
    }
    Ok(provenance)
}

fn merge_provenance(
    left: LayoutProvenance,
    right: &LayoutProvenance,
) -> PdfResult<LayoutProvenance> {
    if left.page_object != right.page_object {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "table provenance crosses page objects",
        ));
    }
    let mut mcids = left.mcids.into_iter().collect::<BTreeSet<_>>();
    mcids.extend(right.mcids.iter().copied());
    let mut text_origins = left.text_origins;
    for origin in &right.text_origins {
        if !text_origins.contains(origin) {
            text_origins.push(*origin);
        }
    }
    Ok(LayoutProvenance {
        page_object: left.page_object,
        source_ordinal_start: left.source_ordinal_start.min(right.source_ordinal_start),
        source_ordinal_end: left.source_ordinal_end.max(right.source_ordinal_end),
        mcids: mcids.into_iter().collect(),
        text_origins,
    })
}

fn union_bbox(left: BBox, right: BBox) -> PdfResult<BBox> {
    BBox::try_new(
        left.x0.min(right.x0),
        left.y0.min(right.y0),
        left.x1.max(right.x1),
        left.y1.max(right.y1),
    )
}

fn warn_once(
    warnings: &mut Vec<LayoutWarning>,
    keys: &mut BTreeSet<(String, Option<usize>)>,
    code: &str,
    page_index: Option<usize>,
    message: &str,
) {
    if keys.insert((code.to_owned(), page_index)) {
        warnings.push(LayoutWarning {
            code: code.to_owned(),
            page_index,
            font_resource: None,
            node_id: None,
            message: message.to_owned(),
        });
    }
}

fn limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}
