//! TextRank 關鍵字抽取（jieba 相容參數：window=5、d=0.85、10 次迭代）。
//!
//! 流程：分詞＋詞性 → 依詞性與長度篩候選 → 滑動視窗共現建無向加權圖 →
//! PageRank（Gauss-Seidel 式就地更新）→ 專有名詞通道軟融合 → 取 top-k。
//! 純演算法、無額外模型資產；IDF 類統計方案（TF-IDF）另案處理。

use std::collections::{HashMap, HashSet};

use crate::Segmenter;

/// 一個關鍵字：詞（正規化後表面形）與正規化到 [0,1] 附近的權重。
#[derive(Clone, Debug, PartialEq)]
pub struct Keyword {
    pub word: String,
    pub weight: f32,
}

/// 關鍵字抽取設定。
///
/// 專有名詞通道目前以 CKIP Nb 為來源，對詞頻與首次出現位置分別計分；
/// 它只提供有界軟加分，不保證任何專有名詞一定進入結果。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct KeywordOptions {
    /// 是否啟用專有名詞通道。
    pub proper_noun_enabled: bool,
    /// 專有名詞分數的融合強度；執行時限制於 0..=1。
    pub proper_noun_weight: f32,
    /// 結果中專有名詞的軟性占比上限；執行時限制於 0..=1。
    /// 若一般候選不足以填滿 top-k，會放寬此上限以維持完整結果。
    pub proper_noun_max_ratio: f32,
}

/// TextRank 圖排名設定。預設值與 LingXi 既有關鍵字行為相同。
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TextRankOptions {
    pub window_size: usize,
    pub damping: f32,
    pub max_iterations: usize,
    /// 每輪最大權重變化低於此值時提早停止；None 表示固定跑滿迭代。
    pub tolerance: Option<f32>,
}

impl Default for TextRankOptions {
    fn default() -> Self {
        TextRankOptions {
            window_size: SPAN,
            damping: DAMPING,
            max_iterations: ITERATIONS,
            tolerance: None,
        }
    }
}

/// 完整關鍵字抽取設定；舊 `KeywordOptions` API 保持相容。
#[derive(Clone, Debug, PartialEq)]
pub struct KeywordExtractionOptions {
    pub rank: TextRankOptions,
    pub min_chars: usize,
    pub stopwords: Vec<String>,
    pub proper_noun: KeywordOptions,
}

impl Default for KeywordExtractionOptions {
    fn default() -> Self {
        KeywordExtractionOptions {
            rank: TextRankOptions::default(),
            min_chars: 2,
            stopwords: Vec::new(),
            proper_noun: KeywordOptions::default(),
        }
    }
}

/// 原文中的 byte 區間。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct TextSpan {
    pub byte_start: usize,
    pub byte_end: usize,
}

/// 一個由相鄰高排名關鍵詞組成的關鍵短語。
#[derive(Clone, Debug, PartialEq)]
pub struct Keyphrase {
    pub phrase: String,
    pub weight: f32,
    pub occurrences: usize,
    pub spans: Vec<TextSpan>,
}

/// 關鍵短語抽取設定。
#[derive(Clone, Debug, PartialEq)]
pub struct KeyphraseOptions {
    pub top_k: usize,
    pub keyword_count: usize,
    pub min_occurrences: usize,
    pub max_terms: usize,
    pub keywords: KeywordExtractionOptions,
}

impl Default for KeyphraseOptions {
    fn default() -> Self {
        KeyphraseOptions {
            top_k: 10,
            keyword_count: 40,
            min_occurrences: 1,
            max_terms: 4,
            keywords: KeywordExtractionOptions::default(),
        }
    }
}

impl Default for KeywordOptions {
    fn default() -> Self {
        KeywordOptions {
            proper_noun_enabled: true,
            proper_noun_weight: 0.25,
            proper_noun_max_ratio: 0.4,
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct CandidateMeta {
    term_frequency: usize,
    first_token: usize,
    proper_noun: bool,
}

/// 共現視窗大小：候選詞與其後 SPAN-1 個詞內的候選詞連邊（與 jieba 相同）。
const SPAN: usize = 5;
/// PageRank 阻尼係數。
const DAMPING: f32 = 0.85;
/// PageRank 迭代次數。
const ITERATIONS: usize = 10;

/// 預設候選詞性：CKIP 名詞、動詞與外文詞。
fn default_allow(tag: &str) -> bool {
    matches!(tag, "Na" | "Nb" | "Nc" | "Ncd" | "Nv" | "FW") || tag.starts_with('V')
}

impl Segmenter {
    /// TextRank 關鍵字抽取，預設詞性過濾（見 `default_allow`）。
    pub fn extract_keywords(&self, text: &str, top_k: usize) -> Vec<Keyword> {
        self.extract_keywords_with_options(text, top_k, None, KeywordOptions::default())
    }

    /// TextRank 關鍵字抽取；`allow_tags` 指定候選詞性白名單（None = 預設）。
    pub fn extract_keywords_with(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<&[&str]>,
    ) -> Vec<Keyword> {
        self.extract_keywords_with_options(text, top_k, allow_tags, KeywordOptions::default())
    }

    /// TextRank 關鍵字抽取，並可設定獨立的專有名詞通道。
    ///
    /// 專有名詞通道啟用時，即使自訂 allow_tags 未含 Nb，Nb 仍可作為
    /// 專有名詞候選；停用後則完全遵循一般詞性白名單。
    pub fn extract_keywords_with_options(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<&[&str]>,
        options: KeywordOptions,
    ) -> Vec<Keyword> {
        self.extract_keywords_configured(
            text,
            top_k,
            allow_tags,
            &KeywordExtractionOptions {
                proper_noun: options,
                ..KeywordExtractionOptions::default()
            },
        )
    }

    /// 可設定視窗、收斂、最短詞長與停用詞的 TextRank 關鍵字抽取。
    pub fn extract_keywords_configured(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<&[&str]>,
        options: &KeywordExtractionOptions,
    ) -> Vec<Keyword> {
        if top_k == 0 {
            return Vec::new();
        }
        // 在正規化文字上取詞，使節點識別與詞典一致（ASCII 小寫、異體字統一），
        // 「AI」與「ai」才會聚合成同一節點。
        let normalized = self.dict.normalize(text);
        let tokens = self.tokenize(&normalized);
        let stopwords: HashSet<String> = options
            .stopwords
            .iter()
            .map(|word| self.dict.normalize(word).into_owned())
            .collect();
        let allowed = |tag: &str| match allow_tags {
            Some(list) => list.contains(&tag),
            None => default_allow(tag),
        };

        // 候選詞 → 節點 id；每個 token 記錄其節點 id（非候選為 None）。
        let mut node_of: HashMap<&str, usize> = HashMap::new();
        let mut words: Vec<&str> = Vec::new();
        let mut metadata: Vec<CandidateMeta> = Vec::new();
        let cand: Vec<Option<usize>> = tokens
            .iter()
            .enumerate()
            .map(|(token_index, t)| {
                let w = &normalized[t.byte_start..t.byte_end];
                let tag = self.tag_name(t.tag);
                let proper_noun = tag == "Nb";
                let general_candidate = allowed(tag);
                let entity_candidate = options.proper_noun.proper_noun_enabled && proper_noun;
                if w.chars().count() < options.min_chars.max(1)
                    || stopwords.contains(w)
                    || (!general_candidate && !entity_candidate)
                {
                    return None;
                }
                let node = match node_of.get(w) {
                    Some(&node) => node,
                    None => {
                        let node = words.len();
                        node_of.insert(w, node);
                        words.push(w);
                        metadata.push(CandidateMeta {
                            term_frequency: 0,
                            first_token: token_index,
                            proper_noun: false,
                        });
                        node
                    }
                };
                metadata[node].term_frequency += 1;
                metadata[node].proper_noun |= proper_noun;
                Some(node)
            })
            .collect();
        let n = words.len();
        if n == 0 {
            return Vec::new();
        }

        // 共現計數 → 無向加權鄰接表（雙向各存一份；自環亦存兩份，與 jieba 一致）。
        let mut cooc: HashMap<(usize, usize), f32> = HashMap::new();
        for i in 0..cand.len() {
            let Some(a) = cand[i] else { continue };
            let window_size = options.rank.window_size.max(2);
            for &candidate in cand
                .iter()
                .take((i + window_size).min(cand.len()))
                .skip(i + 1)
            {
                let Some(b) = candidate else { continue };
                *cooc.entry((a, b)).or_default() += 1.0;
            }
        }
        let mut adj: Vec<Vec<(usize, f32)>> = vec![Vec::new(); n];
        for (&(a, b), &w) in &cooc {
            adj[a].push((b, w));
            adj[b].push((a, w));
        }
        let out_sum: Vec<f32> = adj
            .iter()
            .map(|es| es.iter().map(|(_, w)| w).sum())
            .collect();

        // PageRank：就地更新（同一輪內後算的節點看到前面節點的新值）。
        let mut ws = vec![1.0 / n as f32; n];
        let damping = finite_unit(options.rank.damping, DAMPING);
        let tolerance = options
            .rank
            .tolerance
            .filter(|value| value.is_finite() && *value > 0.0);
        for _ in 0..options.rank.max_iterations.max(1) {
            let mut max_delta = 0.0f32;
            for u in 0..n {
                let previous = ws[u];
                let s: f32 = adj[u]
                    .iter()
                    .filter(|&&(v, _)| out_sum[v] > 0.0)
                    .map(|&(v, w)| w / out_sum[v] * ws[v])
                    .sum();
                ws[u] = (1.0 - damping) + damping * s;
                max_delta = max_delta.max((ws[u] - previous).abs());
            }
            if tolerance.is_some_and(|value| max_delta < value) {
                break;
            }
        }

        // min-max 正規化（jieba 同款：min 除以 10 保留底部區分度）。
        let (mut lo, mut hi) = (f32::INFINITY, f32::NEG_INFINITY);
        for &w in &ws {
            lo = lo.min(w);
            hi = hi.max(w);
        }
        let denom = hi - lo / 10.0;
        if denom > 0.0 {
            for w in &mut ws {
                *w = (*w - lo / 10.0) / denom;
            }
        }

        // 專有名詞通道：TF 佔 70%、首次位置佔 30%。融合採飽和式加分，
        // fused = base + weight * (1 - base) * entity，故不降低原分且不超過 1。
        let entity_scores = proper_noun_scores(&metadata, tokens.len());
        let proper_noun_weight = finite_unit(options.proper_noun.proper_noun_weight, 0.25);
        if options.proper_noun.proper_noun_enabled && proper_noun_weight > 0.0 {
            for i in 0..n {
                ws[i] += proper_noun_weight * (1.0 - ws[i]) * entity_scores[i];
            }
        }

        // 權重降冪、同分依詞排序（輸出穩定），取 top-k。
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&a, &b| ws[b].total_cmp(&ws[a]).then_with(|| words[a].cmp(words[b])));
        let max_ratio = finite_unit(options.proper_noun.proper_noun_max_ratio, 0.4);
        select_with_proper_noun_cap(
            &order,
            &metadata,
            top_k,
            options.proper_noun.proper_noun_enabled,
            max_ratio,
        )
        .into_iter()
        .map(|i| Keyword {
            word: words[i].to_string(),
            weight: ws[i],
        })
        .collect()
    }

    /// 抽取相鄰高排名關鍵詞形成的關鍵短語。
    pub fn extract_keyphrases(&self, text: &str, top_k: usize) -> Vec<Keyphrase> {
        self.extract_keyphrases_with_options(
            text,
            &KeyphraseOptions {
                top_k,
                keyword_count: top_k.saturating_mul(4).max(20),
                ..KeyphraseOptions::default()
            },
        )
    }

    /// 可設定候選關鍵字數、最少出現次數及最大詞數的關鍵短語抽取。
    pub fn extract_keyphrases_with_options(
        &self,
        text: &str,
        options: &KeyphraseOptions,
    ) -> Vec<Keyphrase> {
        if options.top_k == 0 || options.max_terms < 2 {
            return Vec::new();
        }
        let keywords = self.extract_keywords_configured(
            text,
            options.keyword_count.max(options.top_k),
            None,
            &options.keywords,
        );
        let scores: HashMap<String, f32> = keywords
            .into_iter()
            .map(|keyword| (keyword.word, keyword.weight))
            .collect();
        if scores.len() < 2 {
            return Vec::new();
        }

        #[derive(Debug)]
        struct PhraseAccum {
            display: String,
            term_score: f32,
            term_count: usize,
            spans: Vec<TextSpan>,
        }

        let normalized = self.dict.normalize(text);
        let tokens = self.tokenize(&normalized);
        let mut phrases: HashMap<String, PhraseAccum> = HashMap::new();
        let mut run: Vec<(usize, usize, f32)> = Vec::new();

        let flush = |run: &mut Vec<(usize, usize, f32)>,
                     phrases: &mut HashMap<String, PhraseAccum>| {
            if run.len() < 2 {
                run.clear();
                return;
            }
            for chunk in run.chunks(options.max_terms.max(2)) {
                if chunk.len() < 2 {
                    continue;
                }
                let byte_start = chunk[0].0;
                let byte_end = chunk[chunk.len() - 1].1;
                let key = normalized[byte_start..byte_end].to_string();
                let term_score: f32 = chunk.iter().map(|item| item.2).sum();
                let entry = phrases.entry(key).or_insert_with(|| PhraseAccum {
                    display: text[byte_start..byte_end].to_string(),
                    term_score,
                    term_count: chunk.len(),
                    spans: Vec::new(),
                });
                entry.spans.push(TextSpan {
                    byte_start,
                    byte_end,
                });
            }
            run.clear();
        };

        for token in tokens {
            let word = &normalized[token.byte_start..token.byte_end];
            if let Some(&weight) = scores.get(word) {
                let contiguous = run
                    .last()
                    .is_none_or(|(_, byte_end, _)| *byte_end == token.byte_start);
                if !contiguous {
                    flush(&mut run, &mut phrases);
                }
                run.push((token.byte_start, token.byte_end, weight));
            } else {
                flush(&mut run, &mut phrases);
            }
        }
        flush(&mut run, &mut phrases);

        let min_occurrences = options.min_occurrences.max(1);
        let mut out: Vec<Keyphrase> = phrases
            .into_values()
            .filter(|phrase| phrase.spans.len() >= min_occurrences)
            .map(|phrase| {
                let occurrences = phrase.spans.len();
                let length_bonus = 1.0 + 0.1 * phrase.term_count.saturating_sub(1) as f32;
                let frequency_bonus = 1.0 + (occurrences as f32).ln_1p() * 0.1;
                Keyphrase {
                    phrase: phrase.display,
                    weight: phrase.term_score / phrase.term_count as f32
                        * length_bonus
                        * frequency_bonus,
                    occurrences,
                    spans: phrase.spans,
                }
            })
            .collect();
        out.sort_by(|a, b| {
            b.weight
                .total_cmp(&a.weight)
                .then_with(|| b.occurrences.cmp(&a.occurrences))
                .then_with(|| a.phrase.cmp(&b.phrase))
        });
        out.truncate(options.top_k);
        out
    }
}

fn finite_unit(value: f32, fallback: f32) -> f32 {
    if value.is_finite() {
        value.clamp(0.0, 1.0)
    } else {
        fallback
    }
}

fn proper_noun_scores(metadata: &[CandidateMeta], token_count: usize) -> Vec<f32> {
    let max_tf = metadata
        .iter()
        .filter(|meta| meta.proper_noun)
        .map(|meta| meta.term_frequency)
        .max()
        .unwrap_or(0);
    let tf_denom = (max_tf as f32).ln_1p();
    let position_denom = token_count.saturating_sub(1).max(1) as f32;
    metadata
        .iter()
        .map(|meta| {
            if !meta.proper_noun || max_tf == 0 {
                return 0.0;
            }
            let tf = (meta.term_frequency as f32).ln_1p() / tf_denom;
            let position = 1.0 - meta.first_token as f32 / position_denom;
            0.7 * tf + 0.3 * position.clamp(0.0, 1.0)
        })
        .collect()
}

fn select_with_proper_noun_cap(
    order: &[usize],
    metadata: &[CandidateMeta],
    top_k: usize,
    enabled: bool,
    max_ratio: f32,
) -> Vec<usize> {
    let wanted = top_k.min(order.len());
    if !enabled || wanted == 0 {
        return order.iter().copied().take(wanted).collect();
    }
    let max_proper_nouns = (wanted as f32 * max_ratio).ceil() as usize;
    let mut selected = Vec::with_capacity(wanted);
    let mut deferred = Vec::new();
    let mut proper_noun_count = 0usize;
    for &node in order {
        if selected.len() == wanted {
            break;
        }
        if metadata[node].proper_noun && proper_noun_count >= max_proper_nouns {
            deferred.push(node);
            continue;
        }
        proper_noun_count += usize::from(metadata[node].proper_noun);
        selected.push(node);
    }
    // 若一般候選不足，以原排名中的專有名詞補滿，不因軟性占比上限縮短結果。
    if selected.len() < wanted {
        selected.extend(deferred.into_iter().take(wanted - selected.len()));
    }
    // 占比選擇只決定入榜集合；輸出仍須遵守全域權重降冪順序。
    let mut rank = vec![usize::MAX; metadata.len()];
    for (index, &node) in order.iter().enumerate() {
        rank[node] = index;
    }
    selected.sort_unstable_by_key(|node| rank[*node]);
    selected
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(tf: usize, first: usize, proper_noun: bool) -> CandidateMeta {
        CandidateMeta {
            term_frequency: tf,
            first_token: first,
            proper_noun,
        }
    }

    #[test]
    fn proper_noun_channel_rewards_frequency_and_early_position() {
        let scores =
            proper_noun_scores(&[meta(3, 5, true), meta(1, 0, true), meta(9, 0, false)], 10);
        assert!(scores[0] > scores[1], "較高詞頻應勝過較晚位置");
        assert!(scores[1] > 0.0, "只出現一次的早期專有名詞仍應有分數");
        assert_eq!(scores[2], 0.0, "一般候選不得取得專有名詞分數");
    }

    #[test]
    fn proper_noun_cap_defers_entities_when_general_candidates_can_fill() {
        let metadata = [
            meta(1, 0, true),
            meta(1, 1, true),
            meta(1, 2, true),
            meta(1, 3, false),
            meta(1, 4, false),
        ];
        let selected = select_with_proper_noun_cap(&[0, 1, 2, 3, 4], &metadata, 4, true, 0.4);
        assert_eq!(selected, vec![0, 1, 3, 4]);
    }

    #[test]
    fn proper_noun_cap_relaxes_when_the_document_has_only_entities() {
        let metadata = [meta(1, 0, true), meta(1, 1, true), meta(1, 2, true)];
        let selected = select_with_proper_noun_cap(&[0, 1, 2], &metadata, 3, true, 0.4);
        assert_eq!(selected, vec![0, 1, 2]);
    }

    #[test]
    fn invalid_float_options_fall_back_and_valid_values_are_clamped() {
        assert_eq!(finite_unit(f32::NAN, 0.25), 0.25);
        assert_eq!(finite_unit(-1.0, 0.25), 0.0);
        assert_eq!(finite_unit(2.0, 0.25), 1.0);
    }
}
