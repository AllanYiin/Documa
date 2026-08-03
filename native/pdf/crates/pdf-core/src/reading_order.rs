use std::collections::{BTreeMap, BTreeSet};

use crate::layout_ir::{
    LayoutNode, LayoutNodeKind, LayoutNodeRole, LayoutOrders, LayoutProvenance, LayoutTextSpan,
    LayoutVisualBlock, LayoutVisualBlockOrder, LayoutVisualCue, LayoutVisualFocus,
    LayoutVisualReading, LayoutVisualTransition, LayoutVisualTransitionKind, LayoutWarning,
    PageLayout,
};
use crate::{BBox, ErrorCode, ParseLimits, PdfError, PdfResult, TextOrigin, WritingMode};

const LINE_GAP_FACTOR: f64 = 0.55;
const WORD_GAP_FACTOR: f64 = 0.35;
const COLUMN_GAP_FACTOR: f64 = 1.5;
const PARAGRAPH_GAP_FACTOR: f64 = 1.8;
const MARGIN_FRACTION: f64 = 0.12;
const PARAGRAPH_CONFIDENCE: f32 = 0.78;
const FURNITURE_CONFIDENCE: f32 = 0.90;
const PARAGRAPH_RULE: &str = "stage3_paragraph_geometry_v1";
const LIST_ITEM_RULE: &str = "stage3_list_marker_v1";
const SOURCE_FALLBACK_RULE: &str = "stage3_source_fallback_v1";
const HEADER_RULE: &str = "stage3_repeated_header_v1";
const FOOTER_RULE: &str = "stage3_repeated_footer_v1";
const PAGE_NUMBER_RULE: &str = "stage3_page_number_v1";
const VISUAL_READING_RULE: &str = "visual_attention_graph_v1";
const MAX_FOCUS_CANDIDATES: usize = 3;

#[derive(Debug, Clone)]
struct Line {
    spans: Vec<LayoutTextSpan>,
    bbox: BBox,
    font_size: f64,
    source_start: u64,
    source_end: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum MarginBand {
    Top,
    Bottom,
}

#[derive(Debug)]
struct FurnitureCandidate {
    page_index: usize,
    node_id: String,
    role: LayoutNodeRole,
    confidence: f32,
    rule_id: String,
    artifact: bool,
    band: MarginBand,
    fingerprint: String,
    page_number: bool,
}

#[derive(Debug)]
struct FurniturePageState {
    page_index: usize,
    inferred_order: Vec<String>,
    base_excluded_ids: BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct FurnitureNodePatch {
    pub page_index: usize,
    pub node_id: String,
    pub original_role: LayoutNodeRole,
    pub original_confidence: f32,
    pub original_rule_id: String,
    pub role: LayoutNodeRole,
    pub confidence: f32,
    pub rule_id: String,
}

#[derive(Debug, Default)]
pub(crate) struct FurnitureFinalization {
    pub node_patches: Vec<FurnitureNodePatch>,
    pub page_main_flow: Vec<(usize, Vec<String>)>,
    pub warnings: Vec<LayoutWarning>,
    pub available: bool,
}

#[derive(Debug)]
pub(crate) struct FurnitureCollector {
    page_count: usize,
    candidates: Vec<FurnitureCandidate>,
    occurrences: BTreeMap<(MarginBand, String), BTreeSet<usize>>,
    pages: Vec<FurniturePageState>,
}

impl FurnitureCollector {
    pub(crate) fn new(page_count: usize) -> Self {
        Self {
            page_count,
            candidates: Vec::new(),
            occurrences: BTreeMap::new(),
            pages: Vec::with_capacity(page_count),
        }
    }

    pub(crate) fn push_page(&mut self, page: &PageLayout) {
        let candidates = collect_page_furniture_candidates(page);
        for candidate in &candidates {
            self.occurrences
                .entry((candidate.band, candidate.fingerprint.clone()))
                .or_default()
                .insert(candidate.page_index);
        }
        self.candidates.extend(candidates);
        self.pages.push(FurniturePageState {
            page_index: page.page_index,
            inferred_order: page.orders.inferred_order.clone(),
            base_excluded_ids: page
                .semantic_nodes
                .iter()
                .filter(|node| excluded_from_main_flow(node))
                .map(|node| node.id.clone())
                .collect(),
        });
    }

    pub(crate) fn finish(self) -> FurnitureFinalization {
        let required = self.page_count.div_ceil(2).max(2);
        let mut excluded_by_page = self
            .pages
            .iter()
            .map(|page| (page.page_index, page.base_excluded_ids.clone()))
            .collect::<BTreeMap<_, _>>();
        let mut node_patches = Vec::new();
        let mut ambiguous_pages = BTreeSet::new();
        for candidate in self.candidates {
            let occurrence_count = self
                .occurrences
                .get(&(candidate.band, candidate.fingerprint.clone()))
                .map_or(0, BTreeSet::len);
            let page_number = candidate.page_number;
            if candidate.artifact || !role_accepts_furniture(candidate.role) {
                continue;
            }
            let patch = if page_number {
                Some((LayoutNodeRole::PageNumber, PAGE_NUMBER_RULE))
            } else if occurrence_count >= required {
                Some(match candidate.band {
                    MarginBand::Top => (LayoutNodeRole::Header, HEADER_RULE),
                    MarginBand::Bottom => (LayoutNodeRole::Footer, FOOTER_RULE),
                })
            } else {
                None
            };
            if let Some((role, rule_id)) = patch {
                excluded_by_page
                    .entry(candidate.page_index)
                    .or_default()
                    .insert(candidate.node_id.clone());
                node_patches.push(FurnitureNodePatch {
                    page_index: candidate.page_index,
                    node_id: candidate.node_id,
                    original_role: candidate.role,
                    original_confidence: candidate.confidence,
                    original_rule_id: candidate.rule_id,
                    role,
                    confidence: candidate.confidence.max(FURNITURE_CONFIDENCE),
                    rule_id: rule_id.to_owned(),
                });
            } else if occurrence_count >= 2 {
                ambiguous_pages.insert(candidate.page_index);
            }
        }
        let warnings = ambiguous_pages
            .into_iter()
            .map(|page_index| LayoutWarning {
                code: "page_furniture_ambiguous".to_owned(),
                page_index: Some(page_index),
                font_resource: None,
                node_id: None,
                message:
                    "repeated margin text lacked the document frequency required for exclusion"
                        .to_owned(),
            })
            .collect();
        let available = self
            .pages
            .iter()
            .any(|page| !page.inferred_order.is_empty());
        let page_main_flow = self
            .pages
            .into_iter()
            .map(|page| {
                let excluded = excluded_by_page
                    .remove(&page.page_index)
                    .unwrap_or_default();
                let main_flow = page
                    .inferred_order
                    .into_iter()
                    .filter(|node_id| !excluded.contains(node_id))
                    .collect();
                (page.page_index, main_flow)
            })
            .collect();
        FurnitureFinalization {
            node_patches,
            page_main_flow,
            warnings,
            available,
        }
    }
}

#[derive(Debug, Default)]
pub(crate) struct ReadingOrderState {
    total_spans: usize,
    any_inferred: bool,
}

impl ReadingOrderState {
    pub(crate) fn available(&self) -> bool {
        self.any_inferred
    }
}

#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn rebuild_pages(
    pages: &mut [PageLayout],
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
) -> PdfResult<bool> {
    let mut state = ReadingOrderState::default();
    for page in pages {
        rebuild_page(page, limits, warnings, &mut state)?;
    }
    Ok(state.any_inferred)
}

pub(crate) fn rebuild_page(
    page: &mut PageLayout,
    limits: &ParseLimits,
    warnings: &mut Vec<LayoutWarning>,
    state: &mut ReadingOrderState,
) -> PdfResult<()> {
    let spans = take_page_spans(page);
    state.total_spans = state
        .total_spans
        .checked_add(spans.len())
        .ok_or_else(|| limit("Stage 3 span count overflow"))?;
    if state.total_spans > limits.max_text_spans {
        return Err(limit("Stage 3 span limit exceeded"));
    }
    if spans.is_empty() {
        page.semantic_nodes.clear();
        page.orders = LayoutOrders::default();
        return Ok(());
    }

    let supported = spans
        .iter()
        .all(|span| span.writing_mode == WritingMode::Horizontal && span.rotation == 0);
    let lines = if supported {
        let clustered = cluster_lines(spans);
        let (ordered, depth_limited) = xy_order(clustered, 0, limits.max_object_depth);
        if depth_limited {
            push_reading_order_warning(
                warnings,
                page.page_index,
                "XY-cut depth limit used deterministic visual-order fallback",
            );
        }
        ordered
    } else {
        push_reading_order_warning(
            warnings,
            page.page_index,
            "rotated or vertical text used deterministic source-order fallback",
        );
        source_lines(spans)
    };
    let nodes = build_paragraphs(page, lines, supported);
    let source_order = source_node_ids(&nodes);
    let inferred_order = nodes.iter().map(|node| node.id.clone()).collect();
    state.any_inferred |= !nodes.is_empty();
    page.semantic_nodes = nodes;
    page.orders = LayoutOrders {
        source_order,
        inferred_order,
        ..LayoutOrders::default()
    };
    Ok(())
}

pub(crate) fn build_visual_reading(page: &PageLayout) -> Option<LayoutVisualReading> {
    if page.semantic_nodes.is_empty() {
        return None;
    }

    let bounds = page.geometry.layout_bounds;
    let typical_font_size = typical_node_font_size(&page.semantic_nodes);
    let blocks = page
        .semantic_nodes
        .iter()
        .enumerate()
        .map(|(index, node)| build_visual_block(page, node, index, typical_font_size, bounds))
        .collect::<Vec<_>>();
    let focus_candidates = build_focus_candidates(&blocks);
    let transitions = build_visual_transitions(page, &blocks, bounds);

    Some(LayoutVisualReading {
        blocks,
        focus_candidates,
        transitions,
        rule_id: VISUAL_READING_RULE.to_owned(),
    })
}

fn build_visual_block(
    page: &PageLayout,
    node: &LayoutNode,
    index: usize,
    typical_font_size: f64,
    bounds: BBox,
) -> LayoutVisualBlock {
    let node_font_size = node
        .spans
        .iter()
        .map(|span| span.font_size)
        .max_by(f64::total_cmp)
        .unwrap_or(typical_font_size);
    let center_x = (node.bbox.x0 + node.bbox.x1) * 0.5;
    let center_y = (node.bbox.y0 + node.bbox.y1) * 0.5;
    let normalized_x = ((center_x - bounds.x0) / bounds.width().max(1.0)).clamp(0.0, 1.0);
    let normalized_y = ((center_y - bounds.y0) / bounds.height().max(1.0)).clamp(0.0, 1.0);
    let mut salience = 0.45_f64;
    let mut cues = Vec::new();

    match node.role {
        LayoutNodeRole::Heading => {
            salience += 0.35;
            cues.push(LayoutVisualCue::Heading);
        }
        LayoutNodeRole::List
        | LayoutNodeRole::ListItem
        | LayoutNodeRole::Figure
        | LayoutNodeRole::Formula
        | LayoutNodeRole::Form => {
            salience += 0.12;
            cues.push(LayoutVisualCue::StructuredContent);
        }
        _ => {}
    }
    if node_font_size >= typical_font_size * 1.30 {
        salience += 0.20;
        cues.push(LayoutVisualCue::LargeText);
    }
    if normalized_y <= 0.35 {
        salience += 0.10;
        cues.push(LayoutVisualCue::TopEntry);
    }
    if (0.20..=0.80).contains(&normalized_x) {
        salience += 0.05;
        cues.push(LayoutVisualCue::CentralPlacement);
    }
    if page
        .tables
        .iter()
        .any(|table| table.source_node_ids.contains(&node.id))
    {
        salience += 0.08;
        cues.push(LayoutVisualCue::TableAnchor);
    }
    if page
        .image_placements
        .iter()
        .any(|image| image.source_node_ids.contains(&node.id))
    {
        salience += 0.08;
        cues.push(LayoutVisualCue::ImageAnchor);
    }

    let furniture = matches!(
        node.role,
        LayoutNodeRole::Header | LayoutNodeRole::Footer | LayoutNodeRole::PageNumber
    );
    let margin =
        (normalized_y <= 0.10 || normalized_y >= 0.90) && node.role != LayoutNodeRole::Heading;
    let may_be_skipped = node.artifact || furniture || margin;
    if node.artifact {
        salience -= 0.40;
        cues.push(LayoutVisualCue::Artifact);
    } else if furniture {
        salience -= 0.30;
        cues.push(LayoutVisualCue::PageFurniture);
    } else if margin {
        salience -= 0.15;
        cues.push(LayoutVisualCue::PeripheralMargin);
    }

    LayoutVisualBlock {
        id: format!("p{}-vb{index}", page.page_index),
        node_id: node.id.clone(),
        bbox: node.bbox,
        internal_order: LayoutVisualBlockOrder::Simultaneous,
        salience: quantize_unit(salience),
        may_be_skipped,
        cues,
        rule_id: VISUAL_READING_RULE.to_owned(),
    }
}

fn build_focus_candidates(blocks: &[LayoutVisualBlock]) -> Vec<LayoutVisualFocus> {
    let mut focus_indices = (0..blocks.len())
        .filter(|index| !blocks[*index].may_be_skipped)
        .collect::<Vec<_>>();
    if focus_indices.is_empty() {
        focus_indices.extend(0..blocks.len());
    }
    focus_indices.sort_by(|left, right| {
        blocks[*right]
            .salience
            .total_cmp(&blocks[*left].salience)
            .then_with(|| blocks[*left].bbox.y0.total_cmp(&blocks[*right].bbox.y0))
            .then_with(|| blocks[*left].bbox.x0.total_cmp(&blocks[*right].bbox.x0))
            .then_with(|| blocks[*left].id.cmp(&blocks[*right].id))
    });
    focus_indices
        .into_iter()
        .take(MAX_FOCUS_CANDIDATES)
        .map(|index| LayoutVisualFocus {
            block_id: blocks[index].id.clone(),
            salience: blocks[index].salience,
        })
        .collect()
}

fn build_visual_transitions(
    page: &PageLayout,
    blocks: &[LayoutVisualBlock],
    bounds: BBox,
) -> Vec<LayoutVisualTransition> {
    let block_by_node = blocks
        .iter()
        .enumerate()
        .map(|(index, block)| (block.node_id.as_str(), index))
        .collect::<BTreeMap<_, _>>();
    let ordered_indices = page
        .orders
        .inferred_order
        .iter()
        .filter_map(|node_id| block_by_node.get(node_id.as_str()).copied())
        .collect::<Vec<_>>();
    let mut transitions = Vec::with_capacity(ordered_indices.len().saturating_mul(3));
    for (position, &from_index) in ordered_indices.iter().enumerate() {
        if let Some(&to_index) = ordered_indices.get(position + 1) {
            transitions.push(visual_transition(
                &blocks[from_index],
                &blocks[to_index],
                LayoutVisualTransitionKind::Continue,
                0.90,
                bounds,
            ));
        }
        if let Some(&to_index) = ordered_indices.get(position + 2) {
            transitions.push(visual_transition(
                &blocks[from_index],
                &blocks[to_index],
                LayoutVisualTransitionKind::SkipAhead,
                0.55,
                bounds,
            ));
        }
        if position > 0 {
            transitions.push(visual_transition(
                &blocks[from_index],
                &blocks[ordered_indices[position - 1]],
                LayoutVisualTransitionKind::Regression,
                0.32,
                bounds,
            ));
        }
    }
    transitions
}
fn typical_node_font_size(nodes: &[LayoutNode]) -> f64 {
    let mut sizes = nodes
        .iter()
        .flat_map(|node| node.spans.iter().map(|span| span.font_size))
        .filter(|size| size.is_finite() && *size > 0.0)
        .collect::<Vec<_>>();
    if sizes.is_empty() {
        return 1.0;
    }
    sizes.sort_by(f64::total_cmp);
    sizes[sizes.len() / 2]
}

fn visual_transition(
    from: &LayoutVisualBlock,
    to: &LayoutVisualBlock,
    kind: LayoutVisualTransitionKind,
    base_weight: f64,
    page_bounds: BBox,
) -> LayoutVisualTransition {
    let from_x = (from.bbox.x0 + from.bbox.x1) * 0.5;
    let from_y = (from.bbox.y0 + from.bbox.y1) * 0.5;
    let to_x = (to.bbox.x0 + to.bbox.x1) * 0.5;
    let to_y = (to.bbox.y0 + to.bbox.y1) * 0.5;
    let page_diagonal = page_bounds.width().hypot(page_bounds.height()).max(1.0);
    let normalized_distance = (to_x - from_x).hypot(to_y - from_y) / page_diagonal;
    let weight = base_weight * (1.0 - 0.35 * normalized_distance.clamp(0.0, 1.0));
    LayoutVisualTransition {
        from_block_id: from.id.clone(),
        to_block_id: to.id.clone(),
        kind,
        weight: quantize_unit(weight),
    }
}

fn quantize_unit(value: f64) -> f64 {
    (value.clamp(0.0, 1.0) * 1_000.0).round() / 1_000.0
}

fn push_reading_order_warning(warnings: &mut Vec<LayoutWarning>, page_index: usize, message: &str) {
    warnings.push(LayoutWarning {
        code: "reading_order_ambiguous".to_owned(),
        page_index: Some(page_index),
        font_resource: None,
        node_id: None,
        message: message.to_owned(),
    });
}
#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn classify_furniture_and_main_flow(
    pages: &mut [PageLayout],
    warnings: &mut Vec<LayoutWarning>,
) -> bool {
    let mut collector = FurnitureCollector::new(pages.len());
    for page in pages.iter() {
        collector.push_page(page);
    }
    let finalization = collector.finish();
    apply_furniture_finalization(pages, &finalization);
    warnings.extend(finalization.warnings.iter().cloned());
    finalization.available
}

pub(crate) fn apply_furniture_finalization(
    pages: &mut [PageLayout],
    finalization: &FurnitureFinalization,
) {
    for patch in &finalization.node_patches {
        let Some(node) = pages.get_mut(patch.page_index).and_then(|page| {
            page.semantic_nodes
                .iter_mut()
                .find(|node| node.id == patch.node_id)
        }) else {
            continue;
        };
        node.role = patch.role;
        node.confidence = patch.confidence;
        node.rule_id.clone_from(&patch.rule_id);
    }
    for (page_index, main_flow) in &finalization.page_main_flow {
        if let Some(page) = pages.get_mut(*page_index) {
            page.orders.main_flow.clone_from(main_flow);
        }
    }
}
fn take_page_spans(page: &mut PageLayout) -> Vec<LayoutTextSpan> {
    page.semantic_nodes
        .iter_mut()
        .flat_map(|node| std::mem::take(&mut node.spans))
        .collect()
}

fn cluster_lines(mut spans: Vec<LayoutTextSpan>) -> Vec<Line> {
    spans.sort_by(span_visual_cmp);
    let mut rows = Vec::<Vec<LayoutTextSpan>>::new();
    for span in spans {
        if let Some(row) = rows.last_mut() {
            let reference = row.last().expect("row is non-empty");
            let tolerance = reference
                .bbox
                .height()
                .max(span.bbox.height())
                .mul_add(LINE_GAP_FACTOR, 0.5);
            if (bbox_center_y(reference.bbox) - bbox_center_y(span.bbox)).abs() <= tolerance {
                row.push(span);
                continue;
            }
        }
        rows.push(vec![span]);
    }

    let mut lines = Vec::new();
    for mut row in rows {
        row.sort_by(|left, right| {
            left.bbox.x0.total_cmp(&right.bbox.x0).then_with(|| {
                left.provenance
                    .source_ordinal_start
                    .cmp(&right.provenance.source_ordinal_start)
            })
        });
        let mut segment = Vec::new();
        for span in row {
            let split = segment.last().is_some_and(|previous: &LayoutTextSpan| {
                let gap = span.bbox.x0 - previous.bbox.x1;
                let threshold = previous.font_size.max(span.font_size).mul_add(3.0, 12.0);
                !same_metadata(previous, &span) || gap > threshold
            });
            if split {
                lines.push(line_from_spans(std::mem::take(&mut segment)));
            }
            segment.push(span);
        }
        if !segment.is_empty() {
            lines.push(line_from_spans(segment));
        }
    }
    lines
}

fn source_lines(mut spans: Vec<LayoutTextSpan>) -> Vec<Line> {
    spans.sort_by_key(|span| span.provenance.source_ordinal_start);
    spans
        .into_iter()
        .map(|span| line_from_spans(vec![span]))
        .collect()
}

fn line_from_spans(spans: Vec<LayoutTextSpan>) -> Line {
    let first = spans.first().expect("line is non-empty");
    let mut bbox = first.bbox;
    let mut font_size = first.font_size;
    let mut source_start = first.provenance.source_ordinal_start;
    let mut source_end = first.provenance.source_ordinal_end;
    for span in &spans[1..] {
        bbox = union_bbox(bbox, span.bbox);
        font_size = font_size.max(span.font_size);
        source_start = source_start.min(span.provenance.source_ordinal_start);
        source_end = source_end.max(span.provenance.source_ordinal_end);
    }
    Line {
        spans,
        bbox,
        font_size,
        source_start,
        source_end,
    }
}

fn xy_order(mut lines: Vec<Line>, depth: usize, max_depth: usize) -> (Vec<Line>, bool) {
    if lines.len() <= 1 {
        lines.sort_by(line_visual_cmp);
        return (lines, false);
    }
    if depth >= max_depth {
        lines.sort_by(line_visual_cmp);
        return (lines, true);
    }
    let vertical = best_axis_cut(&lines, true);
    let horizontal = best_axis_cut(&lines, false);
    let selected = match (vertical, horizontal) {
        (Some(vertical), Some(horizontal)) => {
            if vertical.1 >= horizontal.1 {
                Some((true, vertical.0))
            } else {
                Some((false, horizontal.0))
            }
        }
        (Some(vertical), None) => Some((true, vertical.0)),
        (None, Some(horizontal)) => Some((false, horizontal.0)),
        (None, None) => None,
    };
    let Some((vertical_axis, cut)) = selected else {
        lines.sort_by(line_visual_cmp);
        return (lines, false);
    };
    let mut first = Vec::new();
    let mut second = Vec::new();
    for line in lines {
        let before = if vertical_axis {
            line.bbox.x1 <= cut
        } else {
            line.bbox.y1 <= cut
        };
        if before {
            first.push(line);
        } else {
            second.push(line);
        }
    }
    if first.is_empty() || second.is_empty() {
        first.extend(second);
        first.sort_by(line_visual_cmp);
        return (first, false);
    }
    let (mut ordered, first_limited) = xy_order(first, depth + 1, max_depth);
    let (second, second_limited) = xy_order(second, depth + 1, max_depth);
    ordered.extend(second);
    (ordered, first_limited || second_limited)
}

fn best_axis_cut(lines: &[Line], vertical: bool) -> Option<(f64, f64)> {
    let mut intervals = lines
        .iter()
        .map(|line| {
            if vertical {
                (line.bbox.x0, line.bbox.x1)
            } else {
                (line.bbox.y0, line.bbox.y1)
            }
        })
        .collect::<Vec<_>>();
    intervals.sort_by(|left, right| left.0.total_cmp(&right.0));
    let font_scale = lines
        .iter()
        .map(|line| line.font_size)
        .fold(0.0_f64, f64::max)
        .max(8.0);
    let threshold = font_scale * COLUMN_GAP_FACTOR;
    let mut prefix_end = intervals[0].1;
    let mut best = None;
    for interval in &intervals[1..] {
        let gap = interval.0 - prefix_end;
        if gap > threshold && best.is_none_or(|(_, best_gap)| gap > best_gap) {
            best = Some(((prefix_end + interval.0) * 0.5, gap));
        }
        prefix_end = prefix_end.max(interval.1);
    }
    best
}

fn build_paragraphs(
    page: &PageLayout,
    lines: Vec<Line>,
    geometry_supported: bool,
) -> Vec<LayoutNode> {
    let mut groups = Vec::<Vec<Line>>::new();
    for line in lines {
        if let Some(group) = groups.last_mut()
            && group
                .last()
                .is_some_and(|previous| can_join_paragraph(previous, &line))
        {
            group.push(line);
        } else {
            groups.push(vec![line]);
        }
    }
    groups
        .into_iter()
        .enumerate()
        .map(|(index, group)| build_paragraph_node(page, index, group, geometry_supported))
        .collect()
}

fn can_join_paragraph(left: &Line, right: &Line) -> bool {
    let first_left = left.spans.first().expect("line is non-empty");
    let first_right = right.spans.first().expect("line is non-empty");
    let vertical_gap = right.bbox.y0 - left.bbox.y1;
    let max_gap = left.font_size.max(right.font_size) * PARAGRAPH_GAP_FACTOR;
    let overlap = (left.bbox.x1.min(right.bbox.x1) - left.bbox.x0.max(right.bbox.x0)).max(0.0);
    let overlap_ratio = overlap / left.bbox.width().min(right.bbox.width()).max(1.0);
    let indent_delta = (left.bbox.x0 - right.bbox.x0).abs();
    vertical_gap >= -2.0
        && vertical_gap <= max_gap
        && overlap_ratio >= 0.15
        && indent_delta <= left.font_size.max(right.font_size) * 2.0
        && (left.font_size - right.font_size).abs() <= left.font_size.max(right.font_size) * 0.20
        && same_metadata(first_left, first_right)
        && !line_starts_list_marker(right)
}

fn line_starts_list_marker(line: &Line) -> bool {
    let text = line_text(line);
    let trimmed = text.trim_start();
    let mut characters = trimmed.chars();
    let Some(first) = characters.next() else {
        return false;
    };
    if matches!(first, '-' | '*' | '\u{2022}' | '\u{25cf}' | '\u{25aa}') {
        return characters.next().is_some_and(char::is_whitespace);
    }
    if first.is_ascii_digit() {
        let suffix = characters.find(|character| !character.is_ascii_digit());
        return matches!(suffix, Some('.' | ')'));
    }
    first.is_ascii_alphabetic() && matches!(characters.next(), Some('.' | ')'))
}

fn same_metadata(left: &LayoutTextSpan, right: &LayoutTextSpan) -> bool {
    left.tag == right.tag
        && left.alt_text == right.alt_text
        && left.actual_text == right.actual_text
        && left.artifact == right.artifact
        && left.provenance.mcids == right.provenance.mcids
}

fn build_paragraph_node(
    page: &PageLayout,
    index: usize,
    lines: Vec<Line>,
    geometry_supported: bool,
) -> LayoutNode {
    let first_line = lines.first().expect("paragraph is non-empty");
    let inferred_list_item = line_starts_list_marker(first_line);
    let first_span = first_line.spans.first().expect("line is non-empty");
    let artifact = first_span.artifact;
    let tag = first_span.tag.clone();
    let alt_text = first_span.alt_text.clone();
    let actual_text = first_span.actual_text.clone();
    let mut bbox = first_line.bbox;
    let mut text = String::new();
    let mut spans = Vec::new();
    let mut mcids = BTreeSet::new();
    let mut origins = Vec::<TextOrigin>::new();
    let mut source_start = u64::MAX;
    let mut source_end = 0_u64;
    for (line_index, line) in lines.into_iter().enumerate() {
        if line_index > 0 {
            text.push('\n');
        }
        text.push_str(&line_text(&line));
        bbox = union_bbox(bbox, line.bbox);
        source_start = source_start.min(line.source_start);
        source_end = source_end.max(line.source_end);
        for span in line.spans {
            mcids.extend(span.provenance.mcids.iter().copied());
            for origin in &span.provenance.text_origins {
                if !origins.contains(origin) {
                    origins.push(*origin);
                }
            }
            spans.push(span);
        }
    }

    let mut role = if artifact {
        LayoutNodeRole::Artifact
    } else {
        role_for_tag(tag.as_deref())
    };
    if role == LayoutNodeRole::Unclassified && inferred_list_item {
        role = LayoutNodeRole::ListItem;
    }
    LayoutNode {
        id: format!("p{}-n{index}", page.page_index),
        kind: LayoutNodeKind::TextBlock,
        role,
        tag,
        alt_text,
        actual_text,
        artifact,
        structure_object: None,
        text,
        bbox,
        quad: None,
        confidence: PARAGRAPH_CONFIDENCE,
        rule_id: if !geometry_supported {
            SOURCE_FALLBACK_RULE.to_owned()
        } else if inferred_list_item {
            LIST_ITEM_RULE.to_owned()
        } else {
            PARAGRAPH_RULE.to_owned()
        },
        provenance: LayoutProvenance {
            page_object: page.object,
            source_ordinal_start: source_start,
            source_ordinal_end: source_end,
            mcids: mcids.into_iter().collect(),
            text_origins: origins,
        },
        spans,
    }
}

fn line_text(line: &Line) -> String {
    let mut output = String::new();
    let mut previous: Option<&LayoutTextSpan> = None;
    for span in &line.spans {
        if let Some(left) = previous
            && should_insert_space(left, span)
        {
            output.push(' ');
        }
        output.push_str(&span.text);
        previous = Some(span);
    }
    output
}

fn should_insert_space(left: &LayoutTextSpan, right: &LayoutTextSpan) -> bool {
    if left.text.chars().last().is_some_and(char::is_whitespace)
        || right.text.chars().next().is_some_and(char::is_whitespace)
    {
        return false;
    }
    let latin_edges = left
        .text
        .chars()
        .last()
        .zip(right.text.chars().next())
        .is_some_and(|(left, right)| left.is_ascii_alphanumeric() && right.is_ascii_alphanumeric());
    let gap = right.bbox.x0 - left.bbox.x1;
    latin_edges && gap > left.font_size.max(right.font_size) * WORD_GAP_FACTOR
}

fn source_node_ids(nodes: &[LayoutNode]) -> Vec<String> {
    let mut indices = (0..nodes.len()).collect::<Vec<_>>();
    indices.sort_by(|left, right| {
        nodes[*left]
            .provenance
            .source_ordinal_start
            .cmp(&nodes[*right].provenance.source_ordinal_start)
            .then_with(|| nodes[*left].id.cmp(&nodes[*right].id))
    });
    indices
        .into_iter()
        .map(|index| nodes[index].id.clone())
        .collect()
}

fn collect_page_furniture_candidates(page: &PageLayout) -> Vec<FurnitureCandidate> {
    let mut candidates = Vec::new();
    let bounds = page.geometry.layout_bounds;
    let margin = bounds.height() * MARGIN_FRACTION;
    for node in &page.semantic_nodes {
        if node.text.chars().count() > 256 {
            continue;
        }
        let band = if node.bbox.y1 <= bounds.y0 + margin {
            Some(MarginBand::Top)
        } else if node.bbox.y0 >= bounds.y1 - margin {
            Some(MarginBand::Bottom)
        } else {
            None
        };
        let Some(band) = band else {
            continue;
        };
        let fingerprint = furniture_fingerprint(&node.text);
        if fingerprint.is_empty() {
            continue;
        }
        candidates.push(FurnitureCandidate {
            page_index: page.page_index,
            node_id: node.id.clone(),
            role: node.role,
            confidence: node.confidence,
            rule_id: node.rule_id.clone(),
            artifact: node.artifact,
            band,
            page_number: page_number_like(&node.text, page.page_number),
            fingerprint,
        });
    }
    candidates
}
fn furniture_fingerprint(text: &str) -> String {
    let mut output = String::new();
    let mut in_digits = false;
    let mut in_space = true;
    for character in text.chars().flat_map(char::to_lowercase) {
        if character.is_numeric() {
            if !in_digits {
                output.push('#');
                in_digits = true;
            }
            in_space = false;
        } else if character.is_whitespace() {
            if !in_space && !output.is_empty() {
                output.push(' ');
            }
            in_digits = false;
            in_space = true;
        } else {
            output.push(character);
            in_digits = false;
            in_space = false;
        }
    }
    output.trim().to_owned()
}

fn page_number_like(text: &str, expected: usize) -> bool {
    let compact = text
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>();
    if compact.is_empty() || compact.chars().count() > 16 || expected == 0 {
        return false;
    }
    let lower = compact.to_ascii_lowercase();
    if lower
        .chars()
        .all(|character| matches!(character, 'i' | 'v' | 'x' | 'l' | 'c' | 'd' | 'm'))
    {
        return roman_value(&lower) == Some(expected)
            && canonical_roman(expected).as_deref() == Some(lower.as_str());
    }

    let digit_run = lower
        .split(|character: char| !character.is_ascii_digit())
        .find(|run| !run.is_empty());
    let Some(number) = digit_run.and_then(|run| run.parse::<usize>().ok()) else {
        return false;
    };
    if number != expected {
        return false;
    }
    lower.chars().all(|character| character.is_ascii_digit())
        || lower.starts_with("page")
        || lower.starts_with("p.")
        || lower.contains('/')
        || lower.contains("of")
}

fn roman_value(text: &str) -> Option<usize> {
    let mut total = 0_usize;
    let mut previous = 0_usize;
    for character in text.chars().rev() {
        let value = match character {
            'i' => 1,
            'v' => 5,
            'x' => 10,
            'l' => 50,
            'c' => 100,
            'd' => 500,
            'm' => 1_000,
            _ => return None,
        };
        if value < previous {
            total = total.checked_sub(value)?;
        } else {
            total = total.checked_add(value)?;
            previous = value;
        }
    }
    Some(total)
}

fn canonical_roman(mut value: usize) -> Option<String> {
    if value == 0 || value > 3_999 {
        return None;
    }
    let mut output = String::new();
    for (number, digits) in [
        (1_000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ] {
        while value >= number {
            output.push_str(digits);
            value -= number;
        }
    }
    Some(output)
}
fn role_accepts_furniture(role: LayoutNodeRole) -> bool {
    matches!(
        role,
        LayoutNodeRole::Unclassified | LayoutNodeRole::Paragraph
    )
}

fn excluded_from_main_flow(node: &LayoutNode) -> bool {
    node.artifact
        || matches!(
            node.role,
            LayoutNodeRole::Artifact
                | LayoutNodeRole::Header
                | LayoutNodeRole::Footer
                | LayoutNodeRole::PageNumber
        )
}

fn role_for_tag(tag: Option<&str>) -> LayoutNodeRole {
    match tag {
        Some("H" | "H1" | "H2" | "H3" | "H4" | "H5" | "H6") => LayoutNodeRole::Heading,
        Some("P") => LayoutNodeRole::Paragraph,
        Some("L") => LayoutNodeRole::List,
        Some("LI") => LayoutNodeRole::ListItem,
        Some("Lbl") => LayoutNodeRole::Label,
        Some("LBody") => LayoutNodeRole::ListBody,
        Some("Figure") => LayoutNodeRole::Figure,
        Some("Formula") => LayoutNodeRole::Formula,
        Some("Form") => LayoutNodeRole::Form,
        Some("Artifact") => LayoutNodeRole::Artifact,
        _ => LayoutNodeRole::Unclassified,
    }
}

fn span_visual_cmp(left: &LayoutTextSpan, right: &LayoutTextSpan) -> std::cmp::Ordering {
    bbox_center_y(left.bbox)
        .total_cmp(&bbox_center_y(right.bbox))
        .then_with(|| left.bbox.x0.total_cmp(&right.bbox.x0))
        .then_with(|| {
            left.provenance
                .source_ordinal_start
                .cmp(&right.provenance.source_ordinal_start)
        })
}

fn line_visual_cmp(left: &Line, right: &Line) -> std::cmp::Ordering {
    left.bbox
        .y0
        .total_cmp(&right.bbox.y0)
        .then_with(|| left.bbox.x0.total_cmp(&right.bbox.x0))
        .then_with(|| left.source_start.cmp(&right.source_start))
}

fn bbox_center_y(bbox: BBox) -> f64 {
    (bbox.y0 + bbox.y1) * 0.5
}

fn union_bbox(left: BBox, right: BBox) -> BBox {
    BBox {
        x0: left.x0.min(right.x0),
        y0: left.y0.min(right.y0),
        x1: left.x1.max(right.x1),
        y1: left.y1.max(right.y1),
    }
}

fn limit(message: &str) -> PdfError {
    PdfError::new(ErrorCode::LimitExceeded, None, message)
}

#[cfg(test)]
mod tests {
    use super::{BBox, Line, xy_order};

    fn line(x0: f64, y0: f64, source_start: u64) -> Line {
        Line {
            spans: Vec::new(),
            bbox: BBox {
                x0,
                y0,
                x1: x0 + 10.0,
                y1: y0 + 10.0,
            },
            font_size: 10.0,
            source_start,
            source_end: source_start,
        }
    }

    #[test]
    fn xy_cut_zero_depth_is_an_exact_warned_boundary() {
        let (ordered, depth_limited) =
            xy_order(vec![line(100.0, 20.0, 0), line(10.0, 10.0, 1)], 0, 0);
        assert!(depth_limited);
        assert_eq!(ordered[0].source_start, 1);
        assert_eq!(ordered[1].source_start, 0);
    }
}
