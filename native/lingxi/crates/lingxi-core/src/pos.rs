//! Fixed-boundary, second-order POS Viterbi.
//!
//! Segmentation is complete before this module runs. Known words use the full
//! lexical P(tag|word) distribution; OOV words use the character joint-state
//! (BMES x POS) HMM. Both paths retain sentence-start and cross-word first-/
//! second-order transitions, so POS cannot move a word boundary.

use std::collections::HashMap;

use daachorse::CharwiseDoubleArrayAhoCorasick;

use crate::model::{PosModel, MIN_LOG, STATE_B, STATE_E, STATE_M, STATE_S};

#[derive(Clone, Copy, Debug, Hash, PartialEq, Eq, PartialOrd, Ord)]
enum Context {
    Start,
    One(u16),
    Pair(u16, u16),
}

#[derive(Clone, Copy)]
struct Route {
    score: f64,
    node: Option<usize>,
}

#[derive(Clone, Copy)]
struct BackNode {
    previous: Option<usize>,
    tag: u8,
}

pub struct PosTagger {
    model: PosModel,
    lexicon: CharwiseDoubleArrayAhoCorasick<u32>,
    states_by_tag: Vec<[u16; 4]>,
}

impl PosTagger {
    pub fn new(model: PosModel) -> Result<Self, String> {
        let (lexicon, rest) = unsafe {
            CharwiseDoubleArrayAhoCorasick::<u32>::deserialize_unchecked(
                &model.lexicon_automaton_bytes,
            )
        };
        if !rest.is_empty() {
            return Err("POS 詞彙自動機含尾隨資料".into());
        }
        if model.lexicon_offsets.len() < 2
            || model.lexicon_offsets.last().copied().unwrap_or(0) as usize
                != model.lexicon_tags.len()
            || model.lexicon_tags.len() != model.lexicon_logps.len()
        {
            return Err("POS 詞彙 CSR 結構不合法".into());
        }
        let tag_count = model.tag_names.len();
        let mut states_by_tag = vec![[u16::MAX; 4]; tag_count];
        for state in 0..model.state_names.len() {
            let tag = model.state_tags[state] as usize;
            let bmes = model.state_bmes[state] as usize;
            if tag >= tag_count || bmes >= 4 || state > u16::MAX as usize {
                return Err("POS state topology 不合法".into());
            }
            if states_by_tag[tag][bmes] != u16::MAX {
                return Err(format!(
                    "POS tag {:?} 的 BMES 狀態重複",
                    model.tag_names[tag]
                ));
            }
            states_by_tag[tag][bmes] = state as u16;
        }
        if states_by_tag.iter().any(|row| row.contains(&u16::MAX)) {
            return Err("POS state topology 未包含每個 tag 的完整 BMES".into());
        }
        let n = model.state_names.len();
        if model.start.len() != n
            || model.trans1.len() != n * n
            || model.trans2.len() != n * n * n
            || model.emit_unknown.len() != n
        {
            return Err("POS dense matrix 維度不合法".into());
        }
        Ok(Self {
            model,
            lexicon,
            states_by_tag,
        })
    }

    pub fn tag_names(&self) -> &[String] {
        &self.model.tag_names
    }

    fn lexicon_row(&self, word: &str) -> Option<(&[u8], &[f32])> {
        let found = self
            .lexicon
            .find_overlapping_iter(word)
            .find(|item| item.start() == 0 && item.end() == word.len())?;
        let id = found.value() as usize;
        let lo = *self.model.lexicon_offsets.get(id)? as usize;
        let hi = *self.model.lexicon_offsets.get(id + 1)? as usize;
        Some((
            &self.model.lexicon_tags[lo..hi],
            &self.model.lexicon_logps[lo..hi],
        ))
    }

    fn emit(&self, character: char, state: u16) -> f64 {
        if let Some((states, logps)) = self.model.emit_row(character) {
            if let Ok(index) = states.binary_search(&state) {
                return logps[index] as f64;
            }
        }
        self.model.emit_unknown[state as usize] as f64
    }

    fn transition(&self, context: Context, state: u16) -> Option<f64> {
        let n = self.model.state_names.len();
        let value = match context {
            Context::Start => self.model.start[state as usize],
            Context::One(previous) => self.model.trans1[previous as usize * n + state as usize],
            Context::Pair(previous2, previous1) => {
                self.model.trans2
                    [(previous2 as usize * n + previous1 as usize) * n + state as usize]
            }
        };
        (value > MIN_LOG).then_some(value as f64)
    }

    fn append(context: Context, state: u16) -> Context {
        match context {
            Context::Start => Context::One(state),
            Context::One(previous) | Context::Pair(_, previous) => Context::Pair(previous, state),
        }
    }

    fn states_for(&self, tag: u8, len: usize) -> Vec<u16> {
        let row = self.states_by_tag[tag as usize];
        if len == 1 {
            return vec![row[STATE_S]];
        }
        let mut states = Vec::with_capacity(len);
        states.push(row[STATE_B]);
        states.extend(std::iter::repeat_n(row[STATE_M], len.saturating_sub(2)));
        states.push(row[STATE_E]);
        states
    }

    fn score_token(
        &self,
        chars: &[char],
        tag: u8,
        lexical_logp: Option<f32>,
        initial: Context,
    ) -> Option<(f64, Context)> {
        let states = self.states_for(tag, chars.len());
        let first = *states.first()?;
        let mut score = self.transition(initial, first)?;
        let mut context = Self::append(initial, first);
        if let Some(logp) = lexical_logp {
            score += logp as f64;
            for &state in &states[1..] {
                context = Self::append(context, state);
            }
            return Some((score, context));
        }
        score += self.emit(chars[0], first);
        for (&character, &state) in chars[1..].iter().zip(&states[1..]) {
            score += self.transition(context, state)? + self.emit(character, state);
            context = Self::append(context, state);
        }
        Some((score, context))
    }

    /// Tag one contiguous Han run. `fallback_tags` supplies the explicit POS of
    /// runtime dictionary entries when no canonical lexical distribution exists.
    pub fn tag_run(&self, words: &[&str], fallback_tags: &[Option<u8>]) -> Vec<u8> {
        if words.is_empty() {
            return Vec::new();
        }
        let mut routes = HashMap::new();
        routes.insert(
            Context::Start,
            Route {
                score: 0.0,
                node: None,
            },
        );
        let mut arena = Vec::new();

        for (index, word) in words.iter().enumerate() {
            let chars: Vec<char> = word.chars().collect();
            if chars.is_empty() {
                return vec![0; words.len()];
            }
            let lexical = self.lexicon_row(word);
            let candidates: Vec<(u8, Option<f32>)> =
                if let Some(tag) = fallback_tags.get(index).copied().flatten() {
                    vec![(tag, Some(0.0))]
                } else if let Some((tags, logps)) = lexical {
                    tags.iter()
                        .zip(logps)
                        .map(|(&tag, &logp)| (tag, Some(logp)))
                        .collect()
                } else {
                    (0..self.model.tag_names.len() as u8)
                        .map(|tag| (tag, None))
                        .collect()
                };
            let mut sources: Vec<_> = routes
                .iter()
                .map(|(&context, &route)| (context, route))
                .collect();
            sources.sort_by_key(|(context, _)| *context);
            let mut next: HashMap<Context, Route> = HashMap::new();
            for (context, route) in sources {
                for &(tag, lexical_logp) in &candidates {
                    let Some((inside, next_context)) =
                        self.score_token(&chars, tag, lexical_logp, context)
                    else {
                        continue;
                    };
                    let score = route.score + inside;
                    if next
                        .get(&next_context)
                        .is_some_and(|current| score <= current.score)
                    {
                        continue;
                    }
                    let node = arena.len();
                    arena.push(BackNode {
                        previous: route.node,
                        tag,
                    });
                    next.insert(
                        next_context,
                        Route {
                            score,
                            node: Some(node),
                        },
                    );
                }
            }
            routes = next;
        }

        let Some(best) = routes
            .iter()
            .max_by(|left, right| {
                left.1
                    .score
                    .total_cmp(&right.1.score)
                    .then_with(|| right.0.cmp(left.0))
            })
            .map(|(_, route)| *route)
        else {
            return vec![0; words.len()];
        };
        let mut tags = Vec::with_capacity(words.len());
        let mut node = best.node;
        while let Some(index) = node {
            let item = arena[index];
            tags.push(item.tag);
            node = item.previous;
        }
        tags.reverse();
        tags
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::CharTable;

    #[test]
    fn fixed_boundaries_return_one_tag_per_word() {
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values([("甲", 0)]).unwrap();
        let model = PosModel {
            state_names: vec!["B-n".into(), "E-n".into(), "M-n".into(), "S-n".into()],
            state_bmes: vec![STATE_B as u8, STATE_E as u8, STATE_M as u8, STATE_S as u8],
            state_tags: vec![0; 4],
            tag_names: vec!["n".into()],
            start: vec![0.0; 4],
            trans1: vec![0.0; 16],
            trans2: vec![0.0; 64],
            chars: CharTable { chars: vec!['甲'] },
            emit_offsets: vec![0, 0],
            emit_states: vec![],
            emit_logps: vec![],
            emit_unknown: vec![0.0; 4],
            lexicon_automaton_bytes: automaton.serialize(),
            lexicon_offsets: vec![0, 1],
            lexicon_tags: vec![0],
            lexicon_logps: vec![0.0],
        };
        let tagger = PosTagger::new(model).unwrap();
        assert_eq!(tagger.tag_run(&["甲", "乙丙"], &[None, None]), vec![0, 0]);
    }

    #[test]
    fn lexical_tags_follow_cross_word_second_order_context() {
        let automaton =
            CharwiseDoubleArrayAhoCorasick::<u32>::with_values([("甲", 0), ("乙", 1), ("丙", 2)])
                .unwrap();
        let state_names = ["B-n", "E-n", "M-n", "S-n", "B-v", "E-v", "M-v", "S-v"]
            .into_iter()
            .map(str::to_string)
            .collect::<Vec<_>>();
        let state_bmes = vec![
            STATE_B as u8,
            STATE_E as u8,
            STATE_M as u8,
            STATE_S as u8,
            STATE_B as u8,
            STATE_E as u8,
            STATE_M as u8,
            STATE_S as u8,
        ];
        let state_tags = vec![0, 0, 0, 0, 1, 1, 1, 1];
        let n = state_names.len();
        let mut start = vec![MIN_LOG; n];
        start[3] = 0.0;
        let mut trans1 = vec![MIN_LOG; n * n];
        trans1[3 * n + 7] = 0.0;
        let mut trans2 = vec![MIN_LOG; n * n * n];
        trans2[(3 * n + 7) * n + 3] = 0.0;
        let model = PosModel {
            state_names,
            state_bmes,
            state_tags,
            tag_names: vec!["n".into(), "v".into()],
            start,
            trans1,
            trans2,
            chars: CharTable { chars: vec![] },
            emit_offsets: vec![0],
            emit_states: vec![],
            emit_logps: vec![],
            emit_unknown: vec![0.0; n],
            lexicon_automaton_bytes: automaton.serialize(),
            lexicon_offsets: vec![0, 2, 4, 6],
            lexicon_tags: vec![0, 1, 0, 1, 0, 1],
            lexicon_logps: vec![(0.5f32).ln(); 6],
        };
        let tagger = PosTagger::new(model).unwrap();
        assert_eq!(
            tagger.tag_run(&["甲", "乙", "丙"], &[None; 3]),
            vec![0, 1, 0]
        );
    }
}
