//! Second-order BMES Viterbi with dictionary span substitution.
//!
//! Every token has a deterministic BMES shape (S, BE, or BM..E). A
//! multi-character dictionary hit substitutes ln(freq / total) for the
//! token-internal BMES score. Sentence-start and cross-token transitions
//! remain part of the global score.

use std::collections::HashMap;

use crate::dict::Dict;
use crate::model::{BmesModel, STATE_B, STATE_E, STATE_M, STATE_S};
use crate::segment::{SegKind, Segment};

const MAX_TOKEN_CHARS: usize = u8::MAX as usize;

#[derive(Clone, Copy, Debug, Hash, PartialEq, Eq)]
enum Context {
    Start,
    One(u8),
    Pair(u8, u8),
}

#[derive(Clone, Copy)]
struct Route {
    score: f64,
    node: Option<usize>,
}

#[derive(Clone, Copy)]
struct BackNode {
    previous: Option<usize>,
    start: usize,
    end: usize,
    word_id: Option<u32>,
}

#[inline]
fn append_context(context: Context, states: &[usize]) -> Context {
    let mut values = Vec::with_capacity(4);
    match context {
        Context::Start => {}
        Context::One(a) => values.push(a),
        Context::Pair(a, b) => {
            values.push(a);
            values.push(b);
        }
    }
    values.extend(states.iter().map(|&state| state as u8));
    match values.as_slice() {
        [single] => Context::One(*single),
        _ => Context::Pair(values[values.len() - 2], values[values.len() - 1]),
    }
}

#[inline]
fn emit1(model: &BmesModel, row: Option<usize>, state: usize) -> f64 {
    row.map(|index| model.emit1[index][state] as f64)
        .unwrap_or(model.emit1_unknown[state] as f64)
}

#[inline]
fn emit2(model: &BmesModel, row: Option<usize>, previous: usize, state: usize) -> f64 {
    row.map(|index| model.emit2[index][previous][state] as f64)
        .unwrap_or(model.emit2_unknown[previous][state] as f64)
}

#[inline]
fn boundary_score(model: &BmesModel, context: Context, state: usize) -> f64 {
    match context {
        Context::Start => model.start[state] as f64,
        Context::One(previous) => model.trans1[previous as usize][state] as f64,
        Context::Pair(previous2, previous1) => {
            model.trans2[previous2 as usize][previous1 as usize][state] as f64
        }
    }
}

fn bmes_token_score(
    model: &BmesModel,
    rows: &[Option<usize>],
    start: usize,
    len: usize,
    context: Context,
    m_loop_prefix: &[f64],
) -> f64 {
    if len == 1 {
        let previous = match context {
            Context::Start => {
                return model.start[STATE_S] as f64 + emit1(model, rows[start], STATE_S);
            }
            Context::One(previous) => previous as usize,
            Context::Pair(_, previous) => previous as usize,
        };
        return boundary_score(model, context, STATE_S)
            + emit2(model, rows[start], previous, STATE_S);
    }

    let previous = match context {
        Context::Start => None,
        Context::One(previous) => Some(previous as usize),
        Context::Pair(_, previous) => Some(previous as usize),
    };
    let mut score = boundary_score(model, context, STATE_B)
        + previous.map_or_else(
            || emit1(model, rows[start], STATE_B),
            |state| emit2(model, rows[start], state, STATE_B),
        );

    if len == 2 {
        score += match context {
            Context::Start => model.trans1[STATE_B][STATE_E] as f64,
            Context::One(previous) | Context::Pair(_, previous) => {
                model.trans2[previous as usize][STATE_B][STATE_E] as f64
            }
        } + emit2(model, rows[start + 1], STATE_B, STATE_E);
        return score;
    }

    score += match context {
        Context::Start => model.trans1[STATE_B][STATE_M] as f64,
        Context::One(previous) | Context::Pair(_, previous) => {
            model.trans2[previous as usize][STATE_B][STATE_M] as f64
        }
    } + emit2(model, rows[start + 1], STATE_B, STATE_M);

    if len == 3 {
        return score
            + model.trans2[STATE_B][STATE_M][STATE_E] as f64
            + emit2(model, rows[start + 2], STATE_M, STATE_E);
    }

    score += model.trans2[STATE_B][STATE_M][STATE_M] as f64
        + emit2(model, rows[start + 2], STATE_M, STATE_M);
    score += m_loop_prefix[start + len - 1] - m_loop_prefix[start + 3];
    score
        + model.trans2[STATE_M][STATE_M][STATE_E] as f64
        + emit2(model, rows[start + len - 1], STATE_M, STATE_E)
}

#[cfg(test)]
fn bmes_token_score_reference(
    model: &BmesModel,
    rows: &[Option<usize>],
    start: usize,
    len: usize,
    initial: Context,
) -> f64 {
    let states: Vec<usize> = if len == 1 {
        vec![STATE_S]
    } else {
        std::iter::once(STATE_B)
            .chain(std::iter::repeat_n(STATE_M, len - 2))
            .chain(std::iter::once(STATE_E))
            .collect()
    };
    let mut context = initial;
    let mut score = 0.0;
    for (offset, &state) in states.iter().enumerate() {
        score += boundary_score(model, context, state);
        score += match context {
            Context::Start => emit1(model, rows[start + offset], state),
            Context::One(previous) | Context::Pair(_, previous) => {
                emit2(model, rows[start + offset], previous as usize, state)
            }
        };
        context = append_context(context, &[state]);
    }
    score
}

fn path_ends(arena: &[BackNode], mut node: Option<usize>) -> Vec<usize> {
    let mut ends = Vec::new();
    while let Some(index) = node {
        ends.push(arena[index].end);
        node = arena[index].previous;
    }
    ends.reverse();
    ends
}
fn update_route(
    routes: &mut HashMap<Context, Route>,
    arena: &mut Vec<BackNode>,
    context: Context,
    score: f64,
    candidate: BackNode,
) {
    let BackNode {
        previous,
        start,
        end,
        word_id,
    } = candidate;
    if let Some(current) = routes.get(&context).copied() {
        if score < current.score {
            return;
        }
        if score == current.score {
            let mut candidate_ends = path_ends(arena, previous);
            candidate_ends.push(end);
            if candidate_ends >= path_ends(arena, current.node) {
                return;
            }
        }
        // `end` has not been expanded yet, so this route node cannot have
        // descendants. Reuse it to keep the arena bounded by contexts × n.
        let node = current.node.expect("non-start route has a backpointer");
        arena[node] = BackNode {
            previous,
            start,
            end,
            word_id,
        };
        routes.insert(
            context,
            Route {
                score,
                node: Some(node),
            },
        );
        return;
    }
    let node = arena.len();
    arena.push(BackNode {
        previous,
        start,
        end,
        word_id,
    });
    routes.insert(
        context,
        Route {
            score,
            node: Some(node),
        },
    );
}

/// Decode one normalized chunk and append segments in original byte coordinates.
fn cut_viterbi_impl(
    dict: &Dict,
    model: &BmesModel,
    chunk: &str,
    byte_base: usize,
    out: &mut Vec<Segment>,
    reference: bool,
) {
    let boundaries: Vec<usize> = chunk
        .char_indices()
        .map(|(byte, _)| byte)
        .chain(std::iter::once(chunk.len()))
        .collect();
    let n = boundaries.len().saturating_sub(1);
    if n == 0 {
        return;
    }
    let rows: Vec<Option<usize>> = chunk
        .chars()
        .map(|character| model.chars.index_of(character))
        .collect();

    #[derive(Clone, Copy)]
    struct DictionaryEdge {
        score: f64,
        word_id: u32,
        custom_bias: Option<f64>,
    }
    let mut dictionary_edges: Vec<HashMap<usize, DictionaryEdge>> =
        (0..n).map(|_| HashMap::new()).collect();
    for found in dict.matches(chunk) {
        let start = boundaries.binary_search(&found.byte_start).unwrap();
        let end = boundaries.binary_search(&found.byte_end).unwrap();
        if end - start < 2 {
            continue;
        }
        let candidate = DictionaryEdge {
            score: found
                .custom_bias
                .unwrap_or_else(|| dict.log_prob(found.word_id) as f64),
            word_id: found.word_id,
            custom_bias: found.custom_bias,
        };
        let slot = dictionary_edges[start].entry(end).or_insert(candidate);
        let candidate_rank = (candidate.custom_bias.is_some(), candidate.score);
        let slot_rank = (slot.custom_bias.is_some(), slot.score);
        if candidate_rank > slot_rank {
            *slot = candidate;
        }
    }

    let mut m_loop_prefix = vec![0.0f64; n + 1];
    for position in 0..n {
        m_loop_prefix[position + 1] = m_loop_prefix[position]
            + model.trans2[STATE_M][STATE_M][STATE_M] as f64
            + emit2(model, rows[position], STATE_M, STATE_M);
    }

    let mut routes: Vec<HashMap<Context, Route>> = (0..=n).map(|_| HashMap::new()).collect();
    routes[0].insert(
        Context::Start,
        Route {
            score: 0.0,
            node: None,
        },
    );
    let mut arena = Vec::new();

    #[allow(clippy::needless_range_loop)] // end indexes synchronized route and edge tables
    for start in 0..n {
        let sources: Vec<(Context, Route)> = routes[start]
            .iter()
            .map(|(&context, &route)| (context, route))
            .collect();
        if sources.is_empty() {
            continue;
        }
        for end in start + 1..=n.min(start + MAX_TOKEN_CHARS) {
            let len = end - start;
            let dictionary = dictionary_edges[start].get(&end).copied();
            let shape: &[usize] = if len == 1 {
                &[STATE_S]
            } else if len == 2 {
                &[STATE_B, STATE_E]
            } else {
                &[STATE_M, STATE_E]
            };
            for &(context, source) in &sources {
                let bmes_score = || {
                    if reference {
                        #[cfg(test)]
                        {
                            bmes_token_score_reference(model, &rows, start, len, context)
                        }
                        #[cfg(not(test))]
                        {
                            unreachable!()
                        }
                    } else {
                        bmes_token_score(model, &rows, start, len, context, &m_loop_prefix)
                    }
                };
                let (token_score, word_id) = match dictionary {
                    Some(edge) => (
                        match edge.custom_bias {
                            Some(bias) => bmes_score() + bias,
                            None => boundary_score(model, context, STATE_B) + edge.score,
                        },
                        Some(edge.word_id),
                    ),
                    None => (bmes_score(), None),
                };
                update_route(
                    &mut routes[end],
                    &mut arena,
                    append_context(context, shape),
                    source.score + token_score,
                    BackNode {
                        previous: source.node,
                        start,
                        end,
                        word_id,
                    },
                );
            }
        }
    }

    let Some(best) = routes[n].values().max_by(|left, right| {
        left.score
            .total_cmp(&right.score)
            .then_with(|| path_ends(&arena, right.node).cmp(&path_ends(&arena, left.node)))
    }) else {
        return;
    };
    let mut path = Vec::new();
    let mut node = best.node;
    while let Some(index) = node {
        let item = arena[index];
        path.push(item);
        node = item.previous;
    }
    path.reverse();
    out.extend(path.into_iter().map(|item| Segment {
        byte_start: byte_base + boundaries[item.start],
        byte_end: byte_base + boundaries[item.end],
        kind: item.word_id.map_or(SegKind::Oov, SegKind::Dict),
    }));
}

/// Decode one normalized chunk and append segments in original byte coordinates.
pub fn cut_viterbi(
    dict: &Dict,
    model: &BmesModel,
    chunk: &str,
    byte_base: usize,
    out: &mut Vec<Segment>,
) {
    cut_viterbi_impl(dict, model, chunk, byte_base, out, false);
}

#[cfg(test)]
fn cut_viterbi_reference(
    dict: &Dict,
    model: &BmesModel,
    chunk: &str,
    byte_base: usize,
    out: &mut Vec<Segment>,
) {
    cut_viterbi_impl(dict, model, chunk, byte_base, out, true);
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{CharTable, DictModel, MIN_LOG};
    use daachorse::CharwiseDoubleArrayAhoCorasick;

    fn tiny_models(words: &[(&str, f64)]) -> (Dict, BmesModel) {
        let total: f64 = words.iter().map(|(_, frequency)| frequency).sum();
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(
            words
                .iter()
                .enumerate()
                .map(|(index, (word, _))| (*word, index as u32)),
        )
        .unwrap();
        let dict = Dict::from_model(DictModel {
            automaton_bytes: automaton.serialize(),
            tag_names: vec!["Na".into()],
            word_tags: vec![0; words.len()],
            word_log_probs: words
                .iter()
                .map(|(_, frequency)| (frequency / total).ln() as f32)
                .collect(),
            word_char_lens: words
                .iter()
                .map(|(word, _)| word.chars().count() as u8)
                .collect(),
            total_log: total.ln() as f32,
            variant_map: vec![],
        });
        let chars = CharTable {
            chars: vec!['乙', '甲'],
        };
        let mut trans1 = [[MIN_LOG; 4]; 4];
        trans1[STATE_B][STATE_M] = -0.5;
        trans1[STATE_B][STATE_E] = -0.5;
        trans1[STATE_E][STATE_B] = -0.5;
        trans1[STATE_E][STATE_S] = -0.5;
        trans1[STATE_S][STATE_B] = -0.5;
        trans1[STATE_S][STATE_S] = -0.5;
        trans1[STATE_M][STATE_M] = -0.5;
        trans1[STATE_M][STATE_E] = -0.5;
        let mut trans2 = [[[MIN_LOG; 4]; 4]; 4];
        trans2.fill(trans1);
        let model = BmesModel {
            chars,
            start: [-0.5, MIN_LOG, MIN_LOG, -0.5],
            trans1,
            trans2,
            emit1: vec![[-0.5; 4]; 2],
            emit2: vec![[[-0.5; 4]; 4]; 2],
            emit1_unknown: [-1.0; 4],
            emit2_unknown: [[-1.0; 4]; 4],
        };
        (dict, model)
    }

    #[test]
    fn single_character_dictionary_entries_are_ignored() {
        let (dict, model) = tiny_models(&[("甲", 10_000.0), ("甲乙", 1.0)]);
        let mut out = Vec::new();
        cut_viterbi(&dict, &model, "甲", 0, &mut out);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].kind, SegKind::Oov);
    }

    #[test]
    fn dictionary_span_replaces_internal_bmes_score() {
        let (dict, mut model) = tiny_models(&[("甲乙", 100.0)]);
        model.trans1[STATE_B][STATE_E] = -100.0;
        let mut out = Vec::new();
        cut_viterbi(&dict, &model, "甲乙", 0, &mut out);
        assert_eq!(out.len(), 1);
        assert!(matches!(out[0].kind, SegKind::Dict(_)));
    }

    #[test]
    fn optimized_scores_equal_the_quadratic_reference() {
        let (_, model) = tiny_models(&[("甲乙", 1.0)]);
        let rows: Vec<_> = "甲乙甲乙甲乙"
            .chars()
            .map(|character| model.chars.index_of(character))
            .collect();
        let mut prefix = vec![0.0; rows.len() + 1];
        for position in 0..rows.len() {
            prefix[position + 1] = prefix[position]
                + model.trans2[STATE_M][STATE_M][STATE_M] as f64
                + emit2(&model, rows[position], STATE_M, STATE_M);
        }
        for context in [
            Context::Start,
            Context::One(STATE_S as u8),
            Context::Pair(STATE_E as u8, STATE_S as u8),
        ] {
            for len in 1..=rows.len() {
                let optimized = bmes_token_score(&model, &rows, 0, len, context, &prefix);
                let reference = bmes_token_score_reference(&model, &rows, 0, len, context);
                assert_eq!(optimized, reference, "context={context:?}, len={len}");
            }
        }
    }

    #[test]
    fn optimized_and_reference_decoders_match_all_short_binary_sentences() {
        let (dict, model) = tiny_models(&[("甲乙", 5.0), ("乙甲", 3.0), ("甲乙甲", 2.0)]);
        for len in 1..=6 {
            for mask in 0..(1usize << len) {
                let text: String = (0..len)
                    .map(|bit| if mask & (1 << bit) == 0 { '甲' } else { '乙' })
                    .collect();
                let mut optimized = Vec::new();
                let mut reference = Vec::new();
                cut_viterbi(&dict, &model, &text, 0, &mut optimized);
                cut_viterbi_reference(&dict, &model, &text, 0, &mut reference);
                assert_eq!(optimized, reference, "text={text}");
            }
        }
    }

    #[test]
    fn exact_ties_choose_the_earlier_boundary() {
        let (dict, model) = tiny_models(&[("丙丁", 1.0)]);
        let mut out = Vec::new();
        cut_viterbi(&dict, &model, "甲乙", 0, &mut out);
        assert_eq!(
            out.iter()
                .map(|segment| segment.byte_end)
                .collect::<Vec<_>>(),
            vec![3, 6]
        );
    }
}
