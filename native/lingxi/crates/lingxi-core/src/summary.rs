//! 零 LLM、結構感知的階層式抽取摘要。

use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

use regex::Regex;
use serde::Serialize;

use crate::clause::{split_clauses_with_options, ClauseSpan, ClauseSplitOptions};
use crate::sentence::{split_sentences_with_options, SentenceSpan, SentenceSplitOptions};
use crate::Segmenter;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum SentenceSimilarity {
    #[default]
    Bm25,
    LexicalOverlap,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SummaryOptions {
    pub min_sentence_chars: usize,
    pub min_token_chars: usize,
    pub stopwords: Vec<String>,
    pub similarity: SentenceSimilarity,
    pub damping: f32,
    pub max_iterations: usize,
    pub tolerance: Option<f32>,
    pub redundancy_threshold: Option<f32>,
    pub min_explainability: Option<f32>,
    pub preserve_original_order: bool,
    pub comma_boundary: bool,
    pub colon_boundary: bool,
    pub semicolon_boundary: bool,
    pub long_block_min_clauses: usize,
    pub long_block_min_chars: usize,
    pub long_block_min_words: usize,
    pub max_clauses_per_long_block: usize,
    pub max_clauses_per_long_list_item: usize,
}

impl Default for SummaryOptions {
    fn default() -> Self {
        Self {
            min_sentence_chars: 8,
            min_token_chars: 1,
            stopwords: Vec::new(),
            similarity: SentenceSimilarity::Bm25,
            damping: 0.85,
            max_iterations: 50,
            tolerance: Some(1e-4),
            redundancy_threshold: Some(0.8),
            min_explainability: Some(0.35),
            preserve_original_order: true,
            comma_boundary: false,
            colon_boundary: false,
            semicolon_boundary: false,
            long_block_min_clauses: 3,
            long_block_min_chars: 240,
            long_block_min_words: 60,
            max_clauses_per_long_block: 2,
            max_clauses_per_long_list_item: 2,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum SummaryBlockKind {
    Paragraph,
    Heading,
    FencedCode,
    IndentedCode,
    OrderedListItem,
    UnorderedListItem,
    Blockquote,
    Table,
    Html,
    ThematicBreak,
}

impl SummaryBlockKind {
    fn is_ranked(self) -> bool {
        matches!(self, Self::Paragraph | Self::Blockquote)
    }

    fn is_list_item(self) -> bool {
        matches!(self, Self::OrderedListItem | Self::UnorderedListItem)
    }

    fn is_protected(self) -> bool {
        matches!(
            self,
            Self::FencedCode | Self::IndentedCode | Self::Table | Self::Html
        )
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SummaryDecision {
    PreserveExact,
    SelectExact,
    SummarizeWithin,
    ContextOnly,
    Omit,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalKind {
    ProperNoun,
    Negation,
    Emphasis,
    ListItem,
    ObjectName,
    Date,
    Number,
    Quantity,
    Money,
    Acronym,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SignalSpan {
    pub kind: SignalKind,
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummarySignals {
    pub proper_noun_count: usize,
    pub model_proper_noun_count: usize,
    pub negation_count: usize,
    pub emphasis_count: usize,
    pub list_item: bool,
    pub object_name_count: usize,
    pub date_count: usize,
    pub number_count: usize,
    pub quantity_count: usize,
    pub money_count: usize,
    pub acronym_count: usize,
    pub spans: Vec<SignalSpan>,
}

impl SummarySignals {
    fn portable_score(&self) -> f32 {
        (0.30 * present(self.proper_noun_count)
            + 0.25 * present(self.money_count)
            + 0.15 * present(self.date_count)
            + 0.10 * present(self.quantity_count)
            + 0.10 * present(self.number_count)
            + 0.05 * present(self.acronym_count)
            + 0.05 * present(self.object_name_count))
        .min(1.0)
    }

    fn has_negation(&self) -> bool {
        self.negation_count > 0
    }
}

fn present(count: usize) -> f32 {
    f32::from(count > 0)
}

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummaryScore {
    pub relevance: f32,
    pub coverage_gain: f32,
    pub novelty: f32,
    pub signal: f32,
    pub final_score: f32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SelectedSpan {
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
    pub forced_by_negation: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummaryBlock {
    pub index: usize,
    pub kind: SummaryBlockKind,
    pub byte_start: usize,
    pub byte_end: usize,
    pub depth: usize,
    pub decision: SummaryDecision,
    pub source_text: String,
    pub output_text: String,
    pub selected_spans: Vec<SelectedSpan>,
    pub signals: SummarySignals,
    pub score: Option<SummaryScore>,
    pub children: Vec<SummaryBlock>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummaryBudget {
    pub requested_max_blocks: usize,
    pub selected_ranked_blocks: usize,
    pub preserved_blocks: usize,
    pub forced_negation_clauses: usize,
    pub actual_output_blocks: usize,
    pub overflow_reasons: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SummaryDocument {
    pub schema_version: u32,
    pub mode: String,
    pub text: String,
    pub blocks: Vec<SummaryBlock>,
    pub budget: SummaryBudget,
    pub input_chars: usize,
    pub output_chars: usize,
    pub reduction_percent: f32,
}

#[derive(Clone, Debug)]
struct ParsedBlock {
    kind: SummaryBlockKind,
    byte_start: usize,
    byte_end: usize,
    depth: usize,
}

#[derive(Clone, Debug)]
struct Candidate {
    block_index: usize,
    frequencies: HashMap<String, usize>,
    length: usize,
    signals: SummarySignals,
}

impl Segmenter {
    pub fn split_sentences(&self, text: &str) -> Vec<SentenceSpan> {
        crate::sentence::split_sentences(text)
    }

    pub fn split_sentences_with_options(
        &self,
        text: &str,
        options: SentenceSplitOptions,
    ) -> Vec<SentenceSpan> {
        split_sentences_with_options(text, options)
    }

    pub fn split_clauses(&self, text: &str) -> Vec<ClauseSpan> {
        crate::clause::split_clauses(text)
    }

    pub fn split_clauses_with_options(
        &self,
        text: &str,
        options: ClauseSplitOptions,
    ) -> Vec<ClauseSpan> {
        split_clauses_with_options(text, options)
    }

    pub fn extract_summary(&self, text: &str, max_blocks: usize) -> SummaryDocument {
        self.extract_summary_with_options(text, max_blocks, &SummaryOptions::default())
    }

    pub fn extract_summary_with_options(
        &self,
        text: &str,
        max_blocks: usize,
        options: &SummaryOptions,
    ) -> SummaryDocument {
        summarize_document(self, text, max_blocks, options)
    }
}

fn summarize_document(
    segmenter: &Segmenter,
    text: &str,
    max_blocks: usize,
    options: &SummaryOptions,
) -> SummaryDocument {
    let parsed = parse_document_blocks(text);
    let stopwords: HashSet<String> = options
        .stopwords
        .iter()
        .map(|word| word.to_lowercase())
        .collect();
    let mut blocks: Vec<SummaryBlock> = parsed
        .iter()
        .enumerate()
        .map(|(index, block)| {
            let source = &text[block.byte_start..block.byte_end];
            let mut signals = portable_signals(source, block.byte_start, block.kind.is_list_item());
            signals.model_proper_noun_count = segmenter
                .tokenize(source)
                .iter()
                .filter(|token| matches!(segmenter.tag_name(token.tag), "Nb" | "Nc"))
                .count();
            SummaryBlock {
                index,
                kind: block.kind,
                byte_start: block.byte_start,
                byte_end: block.byte_end,
                depth: block.depth,
                decision: SummaryDecision::Omit,
                source_text: source.to_string(),
                output_text: String::new(),
                selected_spans: Vec::new(),
                signals,
                score: None,
                children: Vec::new(),
            }
        })
        .collect();

    let candidates: Vec<Candidate> = blocks
        .iter()
        .enumerate()
        .filter(|(_, block)| block.kind.is_ranked())
        .filter_map(|(block_index, block)| {
            let frequencies =
                portable_terms(&block.source_text, &stopwords, options.min_token_chars);
            let length = frequencies.values().sum();
            (length > 0).then(|| Candidate {
                block_index,
                frequencies,
                length,
                signals: block.signals.clone(),
            })
        })
        .collect();
    let total_candidates = candidates.len();
    let candidates = bound_candidates(candidates, &blocks, max_blocks);
    let selected = rank_candidates(&candidates, &blocks, max_blocks, options);
    let mut budget = SummaryBudget {
        requested_max_blocks: max_blocks,
        ..SummaryBudget::default()
    };
    if candidates.len() < total_candidates {
        budget.overflow_reasons.push(format!(
            "{} 個可排名區塊先以 portable proxy 收斂為 {} 個，再建立 TextRank 圖",
            total_candidates,
            candidates.len()
        ));
    }

    for block in &mut blocks {
        if block.kind.is_protected() {
            preserve_exact(block);
            budget.preserved_blocks += 1;
        } else if block.kind.is_list_item() {
            budget.forced_negation_clauses += summarize_or_preserve_block(
                block,
                options.max_clauses_per_long_list_item,
                options,
                true,
            );
            budget.preserved_blocks += 1;
        }
    }
    for chosen in selected {
        let candidate = &candidates[chosen.candidate_index];
        let block = &mut blocks[candidate.block_index];
        block.score = Some(chosen.score);
        budget.forced_negation_clauses +=
            summarize_or_preserve_block(block, options.max_clauses_per_long_block, options, false);
        budget.selected_ranked_blocks += 1;
    }
    attach_context_blocks(&mut blocks);
    attach_thematic_breaks(&mut blocks);
    attach_list_children(&mut blocks);
    budget.actual_output_blocks = blocks
        .iter()
        .filter(|block| block.decision != SummaryDecision::Omit)
        .count();
    if budget.forced_negation_clauses > 0 {
        budget.overflow_reasons.push(format!(
            "{} 個有效否定子句超過局部 clause 上限並依語意閉包保留",
            budget.forced_negation_clauses
        ));
    }
    if budget.preserved_blocks > 0 {
        budget.overflow_reasons.push(format!(
            "{} 個 code/list/table/HTML block 不計入 maxBlocks",
            budget.preserved_blocks
        ));
    }
    let summary = assemble_output(text, &blocks);
    let input_chars = text.chars().count();
    let output_chars = summary.chars().count();
    let reduction_percent = if input_chars == 0 {
        0.0
    } else {
        ((1.0 - output_chars as f32 / input_chars as f32) * 100.0).max(0.0)
    };
    SummaryDocument {
        schema_version: 2,
        mode: "hierarchical-extractive".to_string(),
        text: summary,
        blocks,
        budget,
        input_chars,
        output_chars,
        reduction_percent,
    }
}

fn bound_candidates(
    candidates: Vec<Candidate>,
    blocks: &[SummaryBlock],
    max_blocks: usize,
) -> Vec<Candidate> {
    let cap = 256usize.max(max_blocks.saturating_mul(2).min(1024));
    if candidates.len() <= cap {
        return candidates;
    }
    let priority_count = cap * 3 / 4;
    let sample_count = cap - priority_count;
    let mut ranked: Vec<usize> = (0..candidates.len()).collect();
    ranked.sort_by(|left, right| {
        let proxy = |index: usize| {
            let candidate = &candidates[index];
            0.60 * discourse_score(&blocks[candidate.block_index].source_text)
                + 0.30 * candidate.signals.portable_score()
                + 0.10 * (candidate.length.min(100) as f32 / 100.0)
        };
        proxy(*right).total_cmp(&proxy(*left)).then(
            candidates[*left]
                .block_index
                .cmp(&candidates[*right].block_index),
        )
    });
    let mut selected: HashSet<usize> = ranked.into_iter().take(priority_count).collect();
    if sample_count > 0 {
        for offset in 0..sample_count {
            let index = if sample_count == 1 {
                candidates.len() / 2
            } else {
                offset * (candidates.len() - 1) / (sample_count - 1)
            };
            selected.insert(index);
        }
    }
    candidates
        .into_iter()
        .enumerate()
        .filter_map(|(index, candidate)| selected.contains(&index).then_some(candidate))
        .collect()
}

fn preserve_exact(block: &mut SummaryBlock) {
    block.decision = SummaryDecision::PreserveExact;
    block.output_text = block.source_text.clone();
    block.selected_spans.push(SelectedSpan {
        text: block.source_text.clone(),
        byte_start: block.byte_start,
        byte_end: block.byte_end,
        forced_by_negation: false,
    });
}

fn summarize_or_preserve_block(
    block: &mut SummaryBlock,
    max_clauses: usize,
    options: &SummaryOptions,
    list_item: bool,
) -> usize {
    if list_item && contains_embedded_code(&block.source_text) {
        preserve_exact(block);
        return 0;
    }
    let clauses = split_clauses_with_options(
        &block.source_text,
        ClauseSplitOptions {
            comma_boundary: options.comma_boundary,
            semicolon_boundary: options.semicolon_boundary,
            colon_boundary: options.colon_boundary,
        },
    );
    if !is_long_block(&block.source_text, &clauses, options) {
        block.decision = if list_item {
            SummaryDecision::PreserveExact
        } else {
            SummaryDecision::SelectExact
        };
        block.output_text = block.source_text.clone();
        block.selected_spans.push(SelectedSpan {
            text: block.source_text.clone(),
            byte_start: block.byte_start,
            byte_end: block.byte_end,
            forced_by_negation: false,
        });
        return 0;
    }

    block.decision = SummaryDecision::SummarizeWithin;
    let mut ranked: Vec<(usize, f32, SummarySignals)> = clauses
        .iter()
        .enumerate()
        .map(|(index, clause)| {
            let signals = portable_signals(
                &clause.text,
                block.byte_start + clause.byte_start,
                list_item,
            );
            let score = clause_local_score(&clause.text, &signals, index, clauses.len());
            (index, score, signals)
        })
        .collect();
    ranked.sort_by(|left, right| right.1.total_cmp(&left.1).then(left.0.cmp(&right.0)));
    let limit = max_clauses.max(1);
    let baseline: HashSet<usize> = ranked
        .iter()
        .take(limit)
        .map(|(index, _, _)| *index)
        .collect();
    let mut selected = baseline.clone();
    let mut forced = 0usize;
    for (index, _, signals) in &ranked {
        if signals.has_negation() && selected.insert(*index) {
            forced += 1;
        }
    }
    let mut chosen: Vec<usize> = selected.into_iter().collect();
    chosen.sort_unstable();
    let marker = list_item.then(|| list_marker(&block.source_text)).flatten();
    let mut output_parts = Vec::new();
    for index in chosen {
        let clause = &clauses[index];
        let mut value = clause.text.clone();
        if let Some((_, marker_end)) = marker {
            if index == 0 {
                value = block.source_text[..marker_end].to_string()
                    + block.source_text[marker_end..clause.byte_end].trim();
            }
        }
        let negation = portable_signals(
            &clause.text,
            block.byte_start + clause.byte_start,
            list_item,
        )
        .has_negation();
        block.selected_spans.push(SelectedSpan {
            text: value.clone(),
            byte_start: block.byte_start + clause.byte_start,
            byte_end: block.byte_start + clause.byte_end,
            forced_by_negation: negation && !baseline.contains(&index),
        });
        output_parts.push(value);
    }
    if list_item
        && output_parts
            .first()
            .is_none_or(|value| list_marker(value).is_none())
    {
        if let Some((_, marker_end)) = marker {
            if let Some(first) = output_parts.first_mut() {
                *first = format!("{}{}", &block.source_text[..marker_end], first.trim_start());
            } else {
                output_parts.push(block.source_text[..marker_end].to_string());
            }
        }
    }
    block.output_text = output_parts.join(" ");
    forced
}

fn contains_embedded_code(text: &str) -> bool {
    text.lines().any(|line| fence_marker(line).is_some())
        || text.lines().skip(1).any(is_indented_code)
        || text.lines().skip(1).any(is_html_start)
}

fn is_long_block(text: &str, clauses: &[ClauseSpan], options: &SummaryOptions) -> bool {
    clauses.len() >= options.long_block_min_clauses.max(1)
        && (text.chars().count() > options.long_block_min_chars
            || latin_word_count(text) > options.long_block_min_words)
}

fn latin_word_count(text: &str) -> usize {
    text.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '\'')
        .filter(|value| value.chars().any(|ch| ch.is_ascii_alphabetic()))
        .count()
}

fn clause_local_score(text: &str, signals: &SummarySignals, index: usize, count: usize) -> f32 {
    let position = if count <= 1 {
        1.0
    } else {
        1.0 - index as f32 / count as f32 * 0.25
    };
    (0.60 * discourse_score(text) + 0.25 * signals.portable_score() + 0.15 * position)
        .clamp(0.0, 1.0)
}

#[derive(Debug)]
struct RankedChoice {
    candidate_index: usize,
    score: SummaryScore,
}

fn rank_candidates(
    candidates: &[Candidate],
    blocks: &[SummaryBlock],
    max_blocks: usize,
    options: &SummaryOptions,
) -> Vec<RankedChoice> {
    if candidates.is_empty() || max_blocks == 0 {
        return Vec::new();
    }
    let matrix = similarity_matrix(candidates, options.similarity);
    let centrality = page_rank(&matrix, options);
    let max_centrality = centrality.iter().copied().fold(0.0f32, f32::max).max(1e-6);
    let threshold = options
        .min_explainability
        .filter(|value| value.is_finite())
        .map(|value| value.clamp(0.0, 1.0));
    let redundancy = options
        .redundancy_threshold
        .filter(|value| value.is_finite())
        .map(|value| value.clamp(0.0, 1.0));
    let mut selected: Vec<RankedChoice> = Vec::new();
    let mut coverage = vec![0.0f32; candidates.len()];
    while selected.len() < max_blocks.min(candidates.len()) {
        let mut best: Option<(usize, SummaryScore)> = None;
        for index in 0..candidates.len() {
            if selected
                .iter()
                .any(|choice| choice.candidate_index == index)
            {
                continue;
            }
            let overlap = selected
                .iter()
                .map(|choice| matrix[index][choice.candidate_index])
                .fold(0.0f32, f32::max);
            if redundancy.is_some_and(|limit| overlap >= limit) {
                continue;
            }
            let novelty = 1.0 - overlap;
            let coverage_gain = marginal_coverage_gain(index, &matrix, &coverage);
            let relevance = (0.80 * (centrality[index] / max_centrality))
                .max(discourse_score(
                    &blocks[candidates[index].block_index].source_text,
                ))
                .clamp(0.0, 1.0);
            let signal = candidates[index].signals.portable_score();
            let final_score =
                (0.50 * relevance + 0.25 * coverage_gain + 0.15 * novelty + 0.10 * signal)
                    .clamp(0.0, 1.0);
            if threshold.is_some_and(|limit| final_score < limit) {
                continue;
            }
            let score = SummaryScore {
                relevance,
                coverage_gain,
                novelty,
                signal,
                final_score,
            };
            if best.as_ref().is_none_or(|(current, current_score)| {
                score.final_score > current_score.final_score
                    || (score.final_score == current_score.final_score
                        && candidates[index].block_index < candidates[*current].block_index)
            }) {
                best = Some((index, score));
            }
        }
        let Some((index, score)) = best else { break };
        update_coverage(index, &matrix, &mut coverage);
        selected.push(RankedChoice {
            candidate_index: index,
            score,
        });
    }
    if options.preserve_original_order {
        selected.sort_by_key(|choice| candidates[choice.candidate_index].block_index);
    }
    selected
}

fn discourse_score(text: &str) -> f32 {
    let lower = text.to_lowercase();
    if [
        "研究核心發現",
        "核心發現",
        "研究結論",
        "主要結論",
        "是指",
        "定義為",
        "key finding",
        "main finding",
        "research conclusion",
        "main conclusion",
        "is defined as",
        "refers to",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
    {
        1.0
    } else if [
        "結果顯示",
        "結果指出",
        "結論",
        "建議",
        "因此",
        "所以",
        "results show",
        "results indicate",
        "conclusion",
        "recommend",
        "therefore",
    ]
    .iter()
    .any(|marker| lower.contains(marker))
    {
        0.75
    } else {
        0.25
    }
}

fn attach_context_blocks(blocks: &mut [SummaryBlock]) {
    for index in 0..blocks.len() {
        if blocks[index].kind != SummaryBlockKind::Heading {
            continue;
        }
        let current_depth = blocks[index].depth;
        let next_heading = blocks[index + 1..]
            .iter()
            .position(|block| {
                block.kind == SummaryBlockKind::Heading && block.depth <= current_depth
            })
            .map(|offset| index + 1 + offset)
            .unwrap_or(blocks.len());
        if blocks[index + 1..next_heading]
            .iter()
            .any(|block| block.decision != SummaryDecision::Omit)
        {
            blocks[index].decision = SummaryDecision::ContextOnly;
            blocks[index].output_text = blocks[index].source_text.clone();
            blocks[index].selected_spans.push(SelectedSpan {
                text: blocks[index].source_text.clone(),
                byte_start: blocks[index].byte_start,
                byte_end: blocks[index].byte_end,
                forced_by_negation: false,
            });
        }
    }
}

fn attach_thematic_breaks(blocks: &mut [SummaryBlock]) {
    for index in 0..blocks.len() {
        if blocks[index].kind != SummaryBlockKind::ThematicBreak {
            continue;
        }
        let before = blocks[..index]
            .iter()
            .rev()
            .any(|block| block.decision != SummaryDecision::Omit);
        let after = blocks[index + 1..]
            .iter()
            .any(|block| block.decision != SummaryDecision::Omit);
        if before && after {
            blocks[index].decision = SummaryDecision::ContextOnly;
            blocks[index].output_text = blocks[index].source_text.clone();
        }
    }
}

fn attach_list_children(blocks: &mut [SummaryBlock]) {
    let snapshot = blocks.to_vec();
    for index in 0..blocks.len() {
        if !blocks[index].kind.is_list_item() {
            continue;
        }
        let depth = blocks[index].depth;
        let end = snapshot[index + 1..]
            .iter()
            .position(|block| block.kind.is_list_item() && block.depth <= depth)
            .map(|offset| index + 1 + offset)
            .unwrap_or(snapshot.len());
        let child_depth = snapshot[index + 1..end]
            .iter()
            .filter(|block| block.kind.is_list_item() && block.depth > depth)
            .map(|block| block.depth)
            .min();
        blocks[index].children = child_depth
            .map(|wanted| {
                snapshot[index + 1..end]
                    .iter()
                    .filter(|block| block.kind.is_list_item() && block.depth == wanted)
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
    }
}

fn assemble_output(source: &str, blocks: &[SummaryBlock]) -> String {
    let mut output = String::new();
    let mut previous_end = None;
    for block in blocks
        .iter()
        .filter(|block| block.decision != SummaryDecision::Omit && !block.output_text.is_empty())
    {
        if let Some(end) = previous_end {
            let gap = &source[end..block.byte_start];
            if !gap.is_empty() && gap.chars().all(char::is_whitespace) {
                output.push_str(gap);
            } else if !output.ends_with('\n') {
                output.push_str("\n\n");
            }
        }
        output.push_str(&block.output_text);
        previous_end = Some(block.byte_end);
    }
    output
}

fn parse_document_blocks(text: &str) -> Vec<ParsedBlock> {
    let lines = line_ranges(text);
    let mut blocks = Vec::new();
    let mut index = 0usize;
    while index < lines.len() {
        let (start, end) = lines[index];
        let line = trim_line_ending(&text[start..end]);
        if line.trim().is_empty() {
            index += 1;
            continue;
        }
        if let Some((marker, marker_len)) = fence_marker(line) {
            let mut finish = index + 1;
            while finish < lines.len() {
                let value = trim_line_ending(&text[lines[finish].0..lines[finish].1]);
                finish += 1;
                if closes_fence(value, marker, marker_len) {
                    break;
                }
            }
            push_parsed(
                &mut blocks,
                SummaryBlockKind::FencedCode,
                start,
                lines[finish - 1].1,
                0,
            );
            index = finish;
            continue;
        }
        if is_indented_code(line) {
            let finish = consume_while(&lines, text, index, |value| {
                value.trim().is_empty() || is_indented_code(value)
            });
            push_parsed(
                &mut blocks,
                SummaryBlockKind::IndentedCode,
                start,
                lines[finish - 1].1,
                0,
            );
            index = finish;
            continue;
        }
        if is_atx_heading(line) {
            let level = line
                .trim_start()
                .chars()
                .take_while(|ch| *ch == '#')
                .count();
            push_parsed(&mut blocks, SummaryBlockKind::Heading, start, end, level);
            index += 1;
            continue;
        }
        if index + 1 < lines.len() {
            let next = trim_line_ending(&text[lines[index + 1].0..lines[index + 1].1]);
            if is_setext_underline(next) {
                let level = if next.trim().starts_with('=') { 1 } else { 2 };
                push_parsed(
                    &mut blocks,
                    SummaryBlockKind::Heading,
                    start,
                    lines[index + 1].1,
                    level,
                );
                index += 2;
                continue;
            }
            if is_table_delimiter(next) && line.contains('|') {
                let finish = consume_while(&lines, text, index + 2, |value| {
                    !value.trim().is_empty() && value.contains('|')
                });
                push_parsed(
                    &mut blocks,
                    SummaryBlockKind::Table,
                    start,
                    lines[finish - 1].1,
                    0,
                );
                index = finish;
                continue;
            }
        }
        if is_thematic_break(line) {
            push_parsed(&mut blocks, SummaryBlockKind::ThematicBreak, start, end, 0);
            index += 1;
            continue;
        }
        if let Some((ordered, indent)) = list_line(line) {
            let mut finish = index + 1;
            while finish < lines.len() {
                let value = trim_line_ending(&text[lines[finish].0..lines[finish].1]);
                if value.trim().is_empty() {
                    finish += 1;
                    continue;
                }
                if list_line(value).is_some() {
                    break;
                }
                if leading_indent(value) > indent {
                    finish += 1;
                    continue;
                }
                break;
            }
            push_parsed(
                &mut blocks,
                if ordered {
                    SummaryBlockKind::OrderedListItem
                } else {
                    SummaryBlockKind::UnorderedListItem
                },
                start,
                lines[finish - 1].1,
                indent / 2,
            );
            index = finish;
            continue;
        }
        if line.trim_start().starts_with('>') {
            let finish = consume_while(&lines, text, index, |value| {
                value.trim_start().starts_with('>') || value.trim().is_empty()
            });
            push_parsed(
                &mut blocks,
                SummaryBlockKind::Blockquote,
                start,
                lines[finish - 1].1,
                0,
            );
            index = finish;
            continue;
        }
        if is_html_start(line) {
            let finish = consume_while(&lines, text, index, |value| !value.trim().is_empty());
            push_parsed(
                &mut blocks,
                SummaryBlockKind::Html,
                start,
                lines[finish - 1].1,
                0,
            );
            index = finish;
            continue;
        }

        let mut finish = index + 1;
        while finish < lines.len() {
            let value = trim_line_ending(&text[lines[finish].0..lines[finish].1]);
            if value.trim().is_empty() || starts_structural(value) {
                break;
            }
            if finish + 1 < lines.len() {
                let next = trim_line_ending(&text[lines[finish + 1].0..lines[finish + 1].1]);
                if is_setext_underline(next) || (value.contains('|') && is_table_delimiter(next)) {
                    break;
                }
            }
            finish += 1;
        }
        push_parsed(
            &mut blocks,
            SummaryBlockKind::Paragraph,
            start,
            lines[finish - 1].1,
            0,
        );
        index = finish;
    }
    blocks
}

fn line_ranges(text: &str) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut start = 0usize;
    for (index, ch) in text.char_indices() {
        if ch == '\n' {
            out.push((start, index + 1));
            start = index + 1;
        }
    }
    if start < text.len() {
        out.push((start, text.len()));
    }
    out
}

fn trim_line_ending(line: &str) -> &str {
    line.strip_suffix("\r\n")
        .or_else(|| line.strip_suffix('\n'))
        .unwrap_or(line)
}

fn consume_while(
    lines: &[(usize, usize)],
    text: &str,
    start: usize,
    predicate: impl Fn(&str) -> bool,
) -> usize {
    let mut index = start;
    while index < lines.len() {
        let value = trim_line_ending(&text[lines[index].0..lines[index].1]);
        if !predicate(value) {
            break;
        }
        index += 1;
    }
    index.max(start + 1)
}

fn push_parsed(
    blocks: &mut Vec<ParsedBlock>,
    kind: SummaryBlockKind,
    byte_start: usize,
    byte_end: usize,
    depth: usize,
) {
    blocks.push(ParsedBlock {
        kind,
        byte_start,
        byte_end,
        depth,
    });
}

fn fence_marker(line: &str) -> Option<(char, usize)> {
    let value = line.trim_start();
    if line.len() - value.len() > 3 {
        return None;
    }
    let marker = value.chars().next()?;
    if !matches!(marker, '`' | '~') {
        return None;
    }
    let count = value.chars().take_while(|ch| *ch == marker).count();
    (count >= 3).then_some((marker, count))
}

fn closes_fence(line: &str, marker: char, min_len: usize) -> bool {
    let value = line.trim();
    value.chars().take_while(|ch| *ch == marker).count() >= min_len
        && value.chars().all(|ch| ch == marker || ch.is_whitespace())
}

fn is_indented_code(line: &str) -> bool {
    line.starts_with("    ") || line.starts_with('\t')
}

fn is_atx_heading(line: &str) -> bool {
    let value = line.trim_start();
    let hashes = value.chars().take_while(|ch| *ch == '#').count();
    (1..=6).contains(&hashes)
        && value[hashes..]
            .chars()
            .next()
            .is_some_and(char::is_whitespace)
}

fn is_setext_underline(line: &str) -> bool {
    let value = line.trim();
    value.len() >= 3 && (value.chars().all(|ch| ch == '=') || value.chars().all(|ch| ch == '-'))
}

fn is_table_delimiter(line: &str) -> bool {
    let value = line.trim().trim_matches('|');
    !value.is_empty()
        && value.split('|').all(|cell| {
            let cell = cell.trim().trim_matches(':');
            cell.len() >= 3 && cell.chars().all(|ch| ch == '-')
        })
}

fn is_thematic_break(line: &str) -> bool {
    let value: String = line.chars().filter(|ch| !ch.is_whitespace()).collect();
    value.len() >= 3
        && (value.chars().all(|ch| ch == '-')
            || value.chars().all(|ch| ch == '*')
            || value.chars().all(|ch| ch == '_'))
}

fn list_line(line: &str) -> Option<(bool, usize)> {
    let indent = leading_indent(line);
    let value = &line[indent.min(line.len())..];
    if ["- ", "* ", "+ ", "• ", "‧ ", "▪ ", "◦ "]
        .iter()
        .any(|prefix| value.starts_with(prefix))
    {
        return Some((false, indent));
    }
    let prefix_len = value
        .char_indices()
        .take_while(|(_, ch)| ch.is_ascii_digit() || "一二三四五六七八九十".contains(*ch))
        .last()
        .map(|(index, ch)| index + ch.len_utf8())?;
    let rest = &value[prefix_len..];
    (rest.starts_with(". ")
        || rest.starts_with(") ")
        || rest.starts_with("） ")
        || rest.starts_with("、"))
    .then_some((true, indent))
}

fn list_marker(text: &str) -> Option<(usize, usize)> {
    let first_line_end = text.find('\n').unwrap_or(text.len());
    let line = &text[..first_line_end];
    let indent = leading_indent(line);
    let value = &line[indent..];
    for prefix in ["- ", "* ", "+ ", "• ", "‧ ", "▪ ", "◦ "] {
        if value.starts_with(prefix) {
            return Some((indent, indent + prefix.len()));
        }
    }
    let mut end = 0usize;
    for (index, ch) in value.char_indices() {
        if ch.is_ascii_digit() || "一二三四五六七八九十".contains(ch) {
            end = index + ch.len_utf8();
        } else {
            break;
        }
    }
    if end == 0 {
        return None;
    }
    let rest = &value[end..];
    for suffix in [". ", ") ", "） ", "、"] {
        if rest.starts_with(suffix) {
            return Some((indent, indent + end + suffix.len()));
        }
    }
    None
}

fn leading_indent(line: &str) -> usize {
    line.bytes()
        .take_while(|byte| *byte == b' ' || *byte == b'\t')
        .count()
}

fn is_html_start(line: &str) -> bool {
    static HTML: OnceLock<Regex> = OnceLock::new();
    let value = line.trim_start();
    value.starts_with("<!--") || value.starts_with("<!DOCTYPE") || value.starts_with("<?")
        || HTML.get_or_init(|| Regex::new(r"^</?(?:address|article|aside|base|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:\s|>|/)").unwrap()).is_match(value)
}

fn starts_structural(line: &str) -> bool {
    fence_marker(line).is_some()
        || is_indented_code(line)
        || is_atx_heading(line)
        || is_thematic_break(line)
        || list_line(line).is_some()
        || line.trim_start().starts_with('>')
        || is_html_start(line)
}

fn portable_signals(text: &str, base: usize, list_item: bool) -> SummarySignals {
    let mut spans = Vec::new();
    collect_regex_signals(text, base, SignalKind::Money, money_regex(), &mut spans);
    collect_regex_signals(text, base, SignalKind::Date, date_regex(), &mut spans);
    collect_regex_signals(
        text,
        base,
        SignalKind::Quantity,
        quantity_regex(),
        &mut spans,
    );
    collect_regex_signals(text, base, SignalKind::Number, number_regex(), &mut spans);
    collect_regex_signals(text, base, SignalKind::Acronym, acronym_regex(), &mut spans);
    collect_regex_signals(
        text,
        base,
        SignalKind::ObjectName,
        object_regex(),
        &mut spans,
    );
    collect_regex_signals(
        text,
        base,
        SignalKind::ProperNoun,
        english_proper_regex(),
        &mut spans,
    );
    collect_regex_signals(
        text,
        base,
        SignalKind::ProperNoun,
        cjk_proper_regex(),
        &mut spans,
    );
    collect_regex_signals(
        text,
        base,
        SignalKind::Emphasis,
        emphasis_regex(),
        &mut spans,
    );
    collect_negations(text, base, &mut spans);
    let protected_numeric: Vec<(usize, usize)> = spans
        .iter()
        .filter(|span| matches!(span.kind, SignalKind::Date | SignalKind::Money))
        .map(|span| (span.byte_start, span.byte_end))
        .collect();
    spans.retain(|span| {
        span.kind != SignalKind::Quantity
            || !protected_numeric
                .iter()
                .any(|(start, end)| span.byte_start >= *start && span.byte_end <= *end)
    });
    if list_item {
        let marker_end = list_marker(text).map(|(_, end)| end).unwrap_or(0);
        spans.push(SignalSpan {
            kind: SignalKind::ListItem,
            text: text[..marker_end].to_string(),
            byte_start: base,
            byte_end: base + marker_end,
        });
    }
    spans.sort_by_key(|span| (span.byte_start, span.byte_end, span.kind as u8));
    spans.dedup_by(|left, right| {
        left.kind == right.kind
            && left.byte_start == right.byte_start
            && left.byte_end == right.byte_end
    });
    let count = |kind| spans.iter().filter(|span| span.kind == kind).count();
    SummarySignals {
        proper_noun_count: count(SignalKind::ProperNoun),
        model_proper_noun_count: 0,
        negation_count: count(SignalKind::Negation),
        emphasis_count: count(SignalKind::Emphasis),
        list_item,
        object_name_count: count(SignalKind::ObjectName),
        date_count: count(SignalKind::Date),
        number_count: count(SignalKind::Number),
        quantity_count: count(SignalKind::Quantity),
        money_count: count(SignalKind::Money),
        acronym_count: count(SignalKind::Acronym),
        spans,
    }
}

fn collect_regex_signals(
    text: &str,
    base: usize,
    kind: SignalKind,
    regex: &Regex,
    out: &mut Vec<SignalSpan>,
) {
    out.extend(regex.find_iter(text).map(|item| SignalSpan {
        kind,
        text: item.as_str().to_string(),
        byte_start: base + item.start(),
        byte_end: base + item.end(),
    }));
}

fn number_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)").unwrap())
}

fn date_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"(?i)(?:民國\s*)?\d{2,4}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{1,2}月\d{1,2}日|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s*)?\d{4}\b").unwrap())
}

fn money_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"(?i)(?:(?:NT|US)?[$€£¥￥]\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|(?:TWD|NTD|USD|EUR|JPY|CNY|RMB)\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)|[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:元|萬元|億元|兆元|美元|美金|歐元|日圓|人民幣|TWD|NTD|USD|EUR|JPY|CNY|RMB))").unwrap())
}

fn quantity_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"(?i)[+-]?(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(?:%|％|bps|ms|毫秒|秒|分鐘|小時|天|週|周|月|季|年|公斤|公克|克|kg|g|公里|公尺|公分|毫米|km|m|cm|mm|公升|毫升|l|ml|kb|mb|gb|tb|hz|khz|mhz|ghz|w|kw|mw|v|kv|a|ma|°c|℃|°f)").unwrap())
}

fn acronym_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"\b[A-Z][A-Z0-9&./-]{1,11}\b").unwrap())
}

fn object_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"`[^`\r\n]+`|(?:[A-Za-z][A-Za-z0-9_-]*\.)+[A-Za-z_][A-Za-z0-9_-]*|\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b|\b[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)+\b").unwrap())
}

fn english_proper_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| {
        Regex::new(
            r"\b(?:[A-Z][a-z]+(?:\s+(?:of|the|and|&|[A-Z][a-z]+)){1,5}|[A-Z][A-Z0-9&./-]{1,11})\b",
        )
        .unwrap()
    })
}

fn cjk_proper_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r#"[\p{Han}]{2,20}(?:公司|集團|大學|學院|政府|委員會|基金會|協會|銀行|醫院|研究院|法院|市|縣|國)|[「『“\"]([^」』”\"\r\n]{2,30})[」』”\"]"#).unwrap())
}

fn emphasis_regex() -> &'static Regex {
    static VALUE: OnceLock<Regex> = OnceLock::new();
    VALUE.get_or_init(|| Regex::new(r"\*\*[^*\r\n]+\*\*|（[^）\r\n]+）|\([^\)\r\n]+\)").unwrap())
}

fn collect_negations(text: &str, base: usize, out: &mut Vec<SignalSpan>) {
    let exclusions = [
        "不只", "不僅", "不但", "非常", "是否", "未來", "無論", "否則",
    ];
    for (index, ch) in text.char_indices() {
        if !matches!(ch, '不' | '未' | '無' | '沒' | '非' | '否' | '勿' | '莫') {
            continue;
        }
        let tail = &text[index..];
        let previous = text[..index].chars().next_back();
        if exclusions.iter().any(|value| tail.starts_with(value))
            || (ch == '否' && previous == Some('是'))
        {
            continue;
        }
        out.push(SignalSpan {
            kind: SignalKind::Negation,
            text: ch.to_string(),
            byte_start: base + index,
            byte_end: base + index + ch.len_utf8(),
        });
    }
    static ENGLISH: OnceLock<Regex> = OnceLock::new();
    let lower = text.to_lowercase();
    let english = ENGLISH.get_or_init(|| Regex::new(r"\b(?:not|no|never|without|cannot|can't|won't|neither|nor|prohibit(?:ed|s|ing)?|forbid(?:den|s|ding)?|avoid(?:ed|s|ing)?)\b").unwrap());
    for item in english.find_iter(&lower) {
        let tail = &lower[item.start()..];
        let before = &lower[..item.start()];
        if tail.starts_with("not only")
            || (item.as_str() == "not" && before.ends_with("whether or "))
            || (item.as_str() == "nor" && before.ends_with("neither "))
        {
            continue;
        }
        out.push(SignalSpan {
            kind: SignalKind::Negation,
            text: text[item.start()..item.end()].to_string(),
            byte_start: base + item.start(),
            byte_end: base + item.end(),
        });
    }
}

fn portable_terms(
    text: &str,
    stopwords: &HashSet<String>,
    min_chars: usize,
) -> HashMap<String, usize> {
    let mut out = HashMap::new();
    let mut latin = String::new();
    let mut han_run = Vec::new();
    let flush_latin = |value: &mut String, out: &mut HashMap<String, usize>| {
        if value.chars().count() >= min_chars.max(1) && !stopwords.contains(value) {
            *out.entry(value.clone()).or_default() += 1;
        }
        value.clear();
    };
    let flush_han = |values: &mut Vec<char>, out: &mut HashMap<String, usize>| {
        for ch in values.iter() {
            let value = ch.to_string();
            if !stopwords.contains(&value) {
                *out.entry(value).or_default() += 1;
            }
        }
        for pair in values.windows(2) {
            let value = pair.iter().collect::<String>();
            if !stopwords.contains(&value) {
                *out.entry(value).or_default() += 1;
            }
        }
        values.clear();
    };
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.') {
            flush_han(&mut han_run, &mut out);
            latin.push(ch.to_ascii_lowercase());
        } else if ('\u{3400}'..='\u{9fff}').contains(&ch) {
            flush_latin(&mut latin, &mut out);
            han_run.push(ch);
        } else {
            flush_latin(&mut latin, &mut out);
            flush_han(&mut han_run, &mut out);
        }
    }
    flush_latin(&mut latin, &mut out);
    flush_han(&mut han_run, &mut out);
    out
}

fn similarity_matrix(candidates: &[Candidate], strategy: SentenceSimilarity) -> Vec<Vec<f32>> {
    let count = candidates.len();
    let mut matrix = vec![vec![0.0; count]; count];
    let average_length =
        candidates.iter().map(|item| item.length).sum::<usize>() as f32 / count.max(1) as f32;
    let mut document_frequency = HashMap::new();
    for candidate in candidates {
        for term in candidate.frequencies.keys() {
            *document_frequency.entry(term.as_str()).or_insert(0usize) += 1;
        }
    }
    for left in 0..count {
        for right in left + 1..count {
            let value = match strategy {
                SentenceSimilarity::Bm25 => symmetric_bm25(
                    &candidates[left],
                    &candidates[right],
                    &document_frequency,
                    count,
                    average_length,
                ),
                SentenceSimilarity::LexicalOverlap => {
                    cosine_overlap(&candidates[left], &candidates[right])
                }
            };
            matrix[left][right] = value;
            matrix[right][left] = value;
        }
    }
    matrix
}

fn symmetric_bm25(
    left: &Candidate,
    right: &Candidate,
    document_frequency: &HashMap<&str, usize>,
    document_count: usize,
    average_length: f32,
) -> f32 {
    fn directed(
        query: &Candidate,
        document: &Candidate,
        df: &HashMap<&str, usize>,
        count: usize,
        average: f32,
    ) -> f32 {
        let (k1, b) = (1.2, 0.75);
        let mut score = 0.0;
        for term in query.frequencies.keys() {
            let Some(&frequency) = document.frequencies.get(term) else {
                continue;
            };
            let document_frequency = *df.get(term.as_str()).unwrap_or(&1) as f32;
            let idf =
                ((count as f32 - document_frequency + 0.5) / (document_frequency + 0.5) + 1.0).ln();
            let tf = frequency as f32;
            let norm = tf + k1 * (1.0 - b + b * document.length as f32 / average.max(1.0));
            score += idf * tf * (k1 + 1.0) / norm;
        }
        score / query.frequencies.len().max(1) as f32
    }
    let value = (directed(
        left,
        right,
        document_frequency,
        document_count,
        average_length,
    ) + directed(
        right,
        left,
        document_frequency,
        document_count,
        average_length,
    )) / 2.0;
    value / (1.0 + value)
}

fn cosine_overlap(left: &Candidate, right: &Candidate) -> f32 {
    let dot = left
        .frequencies
        .iter()
        .map(|(term, left_frequency)| {
            *left_frequency as f32 * right.frequencies.get(term).copied().unwrap_or(0) as f32
        })
        .sum::<f32>();
    let left_norm = left
        .frequencies
        .values()
        .map(|value| (*value as f32).powi(2))
        .sum::<f32>()
        .sqrt();
    let right_norm = right
        .frequencies
        .values()
        .map(|value| (*value as f32).powi(2))
        .sum::<f32>()
        .sqrt();
    if left_norm == 0.0 || right_norm == 0.0 {
        0.0
    } else {
        (dot / (left_norm * right_norm)).clamp(0.0, 1.0)
    }
}

fn page_rank(matrix: &[Vec<f32>], options: &SummaryOptions) -> Vec<f32> {
    let count = matrix.len();
    if count == 0 {
        return Vec::new();
    }
    let damping = options.damping.clamp(0.0, 1.0);
    let mut scores = vec![1.0 / count as f32; count];
    for _ in 0..options.max_iterations.max(1) {
        let mut next = vec![(1.0 - damping) / count as f32; count];
        for source in 0..count {
            let total = matrix[source].iter().sum::<f32>();
            if total <= f32::EPSILON {
                for value in &mut next {
                    *value += damping * scores[source] / count as f32;
                }
            } else {
                for target in 0..count {
                    next[target] += damping * scores[source] * matrix[source][target] / total;
                }
            }
        }
        let delta = next
            .iter()
            .zip(&scores)
            .map(|(left, right)| (left - right).abs())
            .sum::<f32>();
        scores = next;
        if options
            .tolerance
            .is_some_and(|value| delta <= value.max(0.0))
        {
            break;
        }
    }
    scores
}

fn marginal_coverage_gain(index: usize, matrix: &[Vec<f32>], coverage: &[f32]) -> f32 {
    matrix[index]
        .iter()
        .enumerate()
        .map(|(other, similarity)| {
            let value = if other == index { 1.0 } else { *similarity };
            (value - coverage[other]).max(0.0)
        })
        .sum::<f32>()
        / matrix.len().max(1) as f32
}

fn update_coverage(index: usize, matrix: &[Vec<f32>], coverage: &mut [f32]) {
    for (other, current) in coverage.iter_mut().enumerate() {
        let value = if other == index {
            1.0
        } else {
            matrix[index][other]
        };
        *current = current.max(value);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_structural_blocks_and_preserves_offsets() {
        let text = "# 標題\r\n\r\n段落一。\r\n\r\n```rs\r\nfn main() {}\r\n```\r\n\r\n- 項目\r\n\r\n| A | B |\r\n|---|---|\r\n| 1 | 2 |\r\n";
        let blocks = parse_document_blocks(text);
        let kinds: Vec<_> = blocks.iter().map(|block| block.kind).collect();
        assert_eq!(
            kinds,
            [
                SummaryBlockKind::Heading,
                SummaryBlockKind::Paragraph,
                SummaryBlockKind::FencedCode,
                SummaryBlockKind::UnorderedListItem,
                SummaryBlockKind::Table,
            ]
        );
        for block in blocks {
            assert!(!text[block.byte_start..block.byte_end].is_empty());
        }
    }

    #[test]
    fn recognizes_unclosed_fence_as_code_to_eof() {
        let text = "前文\n\n~~~js\nconst x = 1;";
        let blocks = parse_document_blocks(text);
        assert_eq!(blocks.last().unwrap().kind, SummaryBlockKind::FencedCode);
        assert_eq!(
            &text[blocks.last().unwrap().byte_start..],
            "~~~js\nconst x = 1;"
        );
    }

    #[test]
    fn parses_nested_lists_as_independent_depth_aware_items() {
        let text = "- Parent\n  - Child\n- Next";
        let blocks = parse_document_blocks(text);
        assert_eq!(blocks.len(), 3);
        assert_eq!(
            blocks.iter().map(|block| block.depth).collect::<Vec<_>>(),
            [0, 1, 0]
        );
        assert!(blocks
            .iter()
            .all(|block| block.kind == SummaryBlockKind::UnorderedListItem));
    }

    #[test]
    fn detects_code_embedded_in_list_items() {
        assert!(contains_embedded_code(
            "- Run this:\n  ```js\n  const value = 1;\n  ```"
        ));
    }

    #[test]
    fn bilingual_negation_avoids_false_positives() {
        let false_cases = "不只 不僅 非常 是否 未來 否則 not only whether or not otherwise";
        assert_eq!(portable_signals(false_cases, 0, false).negation_count, 0);
        let true_cases =
            "不得外送，也不能刪除。The service cannot retry and should never leak data.";
        assert!(portable_signals(true_cases, 0, false).negation_count >= 4);
    }

    #[test]
    fn detects_bilingual_facts_and_money() {
        let text = "OpenAI 於2026-08-20公布 USD 1,024，成長15%。Acme Corporation reported $30.5.";
        let signals = portable_signals(text, 0, false);
        assert!(signals.proper_noun_count >= 1);
        assert!(signals.date_count >= 1);
        assert!(signals.money_count >= 1);
        assert!(signals.quantity_count >= 1);
    }

    #[test]
    fn portable_terms_support_cjk_and_latin() {
        let terms = portable_terms("研究 conclusion 結果", &HashSet::new(), 1);
        assert!(terms.contains_key("研究"));
        assert!(terms.contains_key("conclusion"));
    }
}
