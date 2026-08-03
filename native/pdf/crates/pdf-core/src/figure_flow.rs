use std::collections::BTreeSet;

use crate::{BBox, LayoutNode, LayoutNodeRole, LayoutWarning, PageLayout};

const MAX_CAPTION_GAP_PT: f64 = 48.0;
const AMBIGUOUS_SCORE_DELTA_PT: f64 = 4.0;

#[derive(Debug, Clone, Copy)]
struct CaptionCandidate {
    node_index: usize,
    score: f64,
}

#[allow(dead_code)] // Complete-document compatibility wrapper during 6C2 migration.
pub(crate) fn apply_figure_caption_flow(
    pages: &mut [PageLayout],
    warnings: &mut Vec<LayoutWarning>,
) {
    for page in pages {
        apply_page_figure_caption_flow(page, warnings);
    }
}

pub(crate) fn apply_page_figure_caption_flow(
    page: &mut PageLayout,
    warnings: &mut Vec<LayoutWarning>,
) {
    let table_node_ids = page
        .tables
        .iter()
        .flat_map(|table| table.source_node_ids.iter())
        .chain(
            page.tables
                .iter()
                .flat_map(|table| table.cells.iter())
                .flat_map(|cell| cell.source_node_ids.iter()),
        )
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut warned_ambiguous = false;
    let mut warned_unassigned = false;
    for placement_index in 0..page.image_placements.len() {
        let placement = &page.image_placements[placement_index];
        if placement.artifact || !placement.source_node_ids.is_empty() {
            continue;
        }
        let mut candidates = page
            .semantic_nodes
            .iter()
            .enumerate()
            .filter(|(_, node)| {
                caption_node_eligible(node, &table_node_ids)
                    && caption_score(placement.bbox, node.bbox).is_some()
            })
            .map(|(node_index, node)| CaptionCandidate {
                node_index,
                score: caption_score(placement.bbox, node.bbox).expect("candidate score checked"),
            })
            .collect::<Vec<_>>();
        candidates.sort_by(|left, right| {
            left.score
                .total_cmp(&right.score)
                .then_with(|| left.node_index.cmp(&right.node_index))
        });
        if candidates.len() > 1
            && candidates[1].score - candidates[0].score <= AMBIGUOUS_SCORE_DELTA_PT
        {
            if !warned_ambiguous {
                warnings.push(LayoutWarning {
                    code: "figure_caption_ambiguous".to_owned(),
                    page_index: Some(page.page_index),
                    font_resource: None,
                    node_id: None,
                    message:
                        "multiple equally plausible caption nodes were preserved without an image link"
                            .to_owned(),
                });
                warned_ambiguous = true;
            }
            continue;
        }
        if let Some(candidate) = candidates.first() {
            let node = &page.semantic_nodes[candidate.node_index];
            let node_id = node.id.clone();
            let author_caption = node.role == LayoutNodeRole::Caption;
            let placement = &mut page.image_placements[placement_index];
            placement.source_node_ids.push(node_id);
            if placement.structure_object.is_some() || placement.tag.as_deref() == Some("Figure") {
                "stage5b_tagged_figure_caption_v1".clone_into(&mut placement.rule_id);
                placement.confidence = if author_caption { 1.0 } else { 0.95 };
            } else {
                "stage5b_geometry_caption_v1".clone_into(&mut placement.rule_id);
                placement.confidence = if author_caption { 0.95 } else { 0.85 };
            }
        } else if placement_has_figure_evidence(placement) && !warned_unassigned {
            warnings.push(LayoutWarning {
                code: "image_placement_unassigned".to_owned(),
                page_index: Some(page.page_index),
                font_resource: None,
                node_id: None,
                message: "author-identified figure has no compatible source-node anchor".to_owned(),
            });
            warned_unassigned = true;
        }
    }
}
fn placement_has_figure_evidence(placement: &crate::LayoutImagePlacement) -> bool {
    placement.structure_object.is_some()
        || placement.alt_text.is_some()
        || placement.tag.as_deref() == Some("Figure")
}

fn caption_node_eligible(node: &LayoutNode, table_node_ids: &BTreeSet<String>) -> bool {
    !node.artifact
        && !table_node_ids.contains(&node.id)
        && !matches!(
            node.role,
            LayoutNodeRole::Header
                | LayoutNodeRole::Footer
                | LayoutNodeRole::PageNumber
                | LayoutNodeRole::Artifact
                | LayoutNodeRole::Table
                | LayoutNodeRole::TableRow
                | LayoutNodeRole::TableHeader
                | LayoutNodeRole::TableCell
        )
        && (node.role == LayoutNodeRole::Caption || caption_text_prefix(&node.text))
}

fn caption_text_prefix(text: &str) -> bool {
    let trimmed = text.trim_start();
    let lower = trimmed.to_ascii_lowercase();
    lower.starts_with("figure ")
        || lower.starts_with("figure:")
        || lower.starts_with("fig. ")
        || lower.starts_with("fig ")
        || trimmed.starts_with('\u{5716}')
        || trimmed.starts_with('\u{56fe}')
}

fn caption_score(image: BBox, caption: BBox) -> Option<f64> {
    let overlap = image.x1.min(caption.x1) - image.x0.max(caption.x0);
    let minimum_width = image.width().min(caption.width());
    if overlap <= 0.0 || minimum_width <= 0.0 || overlap / minimum_width < 0.5 {
        return None;
    }
    let below_gap = caption.y0 - image.y1;
    if (-1.0..=MAX_CAPTION_GAP_PT).contains(&below_gap) {
        return Some(below_gap.max(0.0));
    }
    let above_gap = image.y0 - caption.y1;
    if (-1.0..=MAX_CAPTION_GAP_PT).contains(&above_gap) {
        return Some(above_gap.max(0.0) + 8.0);
    }
    None
}
