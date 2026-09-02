//! lingxi-core：繁體中文分詞引擎核心。
//!
//! 管線：pre hooks → 預切塊 → 詞典 DAG+DP → 二階 BMES HMM（未登入詞）→
//! POS Viterbi（OOV 詞性）→ post hooks。
//! 本 crate 只含演算法與模型載入，平行化與 I/O 由上層（CLI / bindings）負責。

pub mod affect;
pub mod chunk;
pub mod clause;
pub mod custom_lexicon;
pub mod dag;
pub mod dict;
pub mod keyword;
pub mod model;
pub mod pos;
pub mod rules;
pub mod segment;
pub mod sentence;
pub mod summary;
pub mod userdict;

use std::path::Path;

use affect::AffectStore;
pub use affect::{
    build_affect_model, parse_affect_lexicon, parse_taxonomy, AffectAnnotation, AffectInput,
    AffectLexiconFile, AffectModel, AffectModelEntry, AffectSourceEntry, AffectStats, EmotionLabel,
    EmotionTaxonomy, Polarity,
};
use chunk::ChunkKind;
pub use clause::{split_clauses, split_clauses_with_options, ClauseSpan, ClauseSplitOptions};
pub use custom_lexicon::{
    parse_custom_lexicon, CustomLexiconEntry, CustomLexiconSpec, SegmenterOptions,
};
use dict::Dict;
pub use keyword::{
    Keyphrase, KeyphraseOptions, Keyword, KeywordExtractionOptions, KeywordOptions,
    TextRankOptions, TextSpan,
};
pub use rules::{known_rules, RuleAction, RuleBucket, RuleInfo, RuleStatus, RuleTrace};
pub use segment::{SegKind, Segment};
pub use sentence::{
    split_sentences, split_sentences_with_options, SentenceSpan, SentenceSplitOptions,
};
pub use summary::{
    SelectedSpan, SentenceSimilarity, SignalKind, SignalSpan, SummaryBlock, SummaryBlockKind,
    SummaryBudget, SummaryDecision, SummaryDocument, SummaryOptions, SummaryScore, SummarySignals,
};
pub use userdict::{parse_user_dict, UserDictEntry};

/// 全量模型之外、由回歸測試確認的少量穩定邊界覆寫。
const CURATED_DICTIONARY: &str = include_str!("curated_dict.txt");

/// 現行模型仍可能以更長詞跨過的少量人工核准詞界。
///
/// 這些詞先成為 anchor，再讓兩側各自走 DAG；頻率只負責 POS fallback，
/// 不再依模型總詞頻碰運氣。混合 ASCII/Han 詞仍沿用相同 anchor 管線。
const CURATED_BOUNDARY_ANCHORS: &[&str] = &["行政院", "生命", "擺攤"];

/// 帶詞性的分詞結果：原始輸入的 byte 區間 + 統一詞性表的 tag id。
/// 詞字串由呼叫端以 `&text[byte_start..byte_end]` 取得（零拷貝），
/// 詞性名稱以 `Segmenter::tag_name(tag)` 解析。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Token {
    pub byte_start: usize,
    pub byte_end: usize,
    pub tag: u8,
}

/// 結構化自訂辭典的命中來源。
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LexiconSource {
    pub id: String,
    pub domain: String,
    pub priority: i8,
}

/// 不改變既有 Token 的可選詞級情感標註結果。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AnnotatedToken {
    pub token: Token,
    pub affect: Option<AffectAnnotation>,
    pub source: Option<LexiconSource>,
}

/// 非中文詞段的內建詞性 id（統一詞性表索引）。
struct BuiltinTags {
    url: u8,
    email: u8,
    eng: u8,
    num: u8,   // "m"
    time: u8,  // "t"
    punct: u8, // "w"
    other: u8, // "x"（空白與其他符號共用）
    unknown: u8,
}

struct PipelineScratch {
    chunks: Vec<chunk::Chunk>,
    segments: Vec<Segment>,
}

/// 分詞器：載入一次、多執行緒共享（`Send + Sync`，內部無可變狀態）。
pub struct Segmenter {
    dict: Dict,
    bmes: model::BmesModel,
    pos: pos::PosTagger,
    /// 統一詞性名稱表：合併詞典詞性、POS 模型詞性與內建詞性。
    tags: Vec<String>,
    /// 詞典 tag id → 統一 tag id。
    dict_tag_map: Vec<u8>,
    /// POS 模型 tag id → 統一 tag id。
    pos_tag_map: Vec<u8>,
    /// 詞典 tag id → POS 模型 tag id（runtime 詞典 lexical fallback）。
    dict_to_pos: Vec<Option<u8>>,
    builtin: BuiltinTags,
    affect: AffectStore,
}

/// 模型載入錯誤。
#[derive(Debug)]
pub enum LoadError {
    Io(std::io::Error),
    Asset(model::AssetError),
    UserDict(String),
    CustomLexicon(String),
    Affect(String),
    TooManyTags(usize),
    InvalidOptions(String),
}

impl std::fmt::Display for LoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LoadError::Io(e) => write!(f, "讀取模型檔失敗: {e}"),
            LoadError::Asset(e) => write!(f, "{e}"),
            LoadError::UserDict(e) => write!(f, "載入 legacy 自訂詞典失敗: {e}"),
            LoadError::CustomLexicon(e) => write!(f, "載入多領域自訂辭典失敗: {e}"),
            LoadError::Affect(e) => write!(f, "載入情感詞典失敗: {e}"),
            LoadError::TooManyTags(n) => {
                write!(f, "統一詞性表共有 {n} 種，超過 u8 可表示的 256 種")
            }
            LoadError::InvalidOptions(message) => write!(f, "無效的分詞器設定: {message}"),
        }
    }
}

impl std::error::Error for LoadError {}

/// 讀取並解碼單一資產檔。
fn load_asset<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, LoadError> {
    let bytes = std::fs::read(path).map_err(LoadError::Io)?;
    model::decode_asset(&bytes).map_err(LoadError::Asset)
}

fn load_pos_asset(path: &Path) -> Result<model::PosModel, LoadError> {
    let bytes = std::fs::read(path).map_err(LoadError::Io)?;
    model::decode_pos_asset(&bytes).map_err(LoadError::Asset)
}

fn load_optional_asset<T: serde::de::DeserializeOwned>(
    path: &Path,
) -> Result<Option<T>, LoadError> {
    match std::fs::read(path) {
        Ok(bytes) => model::decode_asset(&bytes)
            .map(Some)
            .map_err(LoadError::Asset),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(LoadError::Io(error)),
    }
}

impl Segmenter {
    /// 從資產目錄載入；affect.bin 缺少時退化為無情感標註。
    pub fn from_asset_dir(dir: impl AsRef<Path>) -> Result<Self, LoadError> {
        Self::from_asset_dir_with_options(dir, SegmenterOptions::default())
    }

    pub fn from_asset_dir_with_user_dict(
        dir: impl AsRef<Path>,
        user_entries: &[UserDictEntry],
    ) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        Self::from_models_with_all(
            load_asset(&dir.join("dict.bin"))?,
            load_asset(&dir.join("hmm_bmes.bin"))?,
            load_pos_asset(&dir.join("hmm_pos.bin"))?,
            load_optional_asset(&dir.join("affect.bin"))?,
            user_entries,
            SegmenterOptions::default(),
        )
    }

    pub fn from_asset_dir_with_user_dict_and_options(
        dir: impl AsRef<Path>,
        user_entries: &[UserDictEntry],
        options: SegmenterOptions,
    ) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        Self::from_models_with_all(
            load_asset(&dir.join("dict.bin"))?,
            load_asset(&dir.join("hmm_bmes.bin"))?,
            load_pos_asset(&dir.join("hmm_pos.bin"))?,
            load_optional_asset(&dir.join("affect.bin"))?,
            user_entries,
            options,
        )
    }

    pub fn from_asset_dir_with_options(
        dir: impl AsRef<Path>,
        options: SegmenterOptions,
    ) -> Result<Self, LoadError> {
        let dir = dir.as_ref();
        Self::from_models_with_all(
            load_asset(&dir.join("dict.bin"))?,
            load_asset(&dir.join("hmm_bmes.bin"))?,
            load_pos_asset(&dir.join("hmm_pos.bin"))?,
            load_optional_asset(&dir.join("affect.bin"))?,
            &[],
            options,
        )
    }

    pub fn from_models(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
    ) -> Result<Self, LoadError> {
        Self::from_models_with_all(
            dict_model,
            bmes,
            pos,
            None,
            &[],
            SegmenterOptions::default(),
        )
    }

    pub fn from_models_with_user_dict(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
        user_entries: &[UserDictEntry],
    ) -> Result<Self, LoadError> {
        Self::from_models_with_all(
            dict_model,
            bmes,
            pos,
            None,
            user_entries,
            SegmenterOptions::default(),
        )
    }

    pub fn from_models_with_options(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
        affect_model: Option<AffectModel>,
        options: SegmenterOptions,
    ) -> Result<Self, LoadError> {
        Self::from_models_with_all(dict_model, bmes, pos, affect_model, &[], options)
    }

    fn from_models_with_all(
        dict_model: model::DictModel,
        bmes: model::BmesModel,
        pos: model::PosModel,
        affect_model: Option<AffectModel>,
        user_entries: &[UserDictEntry],
        options: SegmenterOptions,
    ) -> Result<Self, LoadError> {
        let mut dict = Dict::from_model(dict_model);
        let mut dictionary_entries = parse_user_dict(CURATED_DICTIONARY);
        dictionary_entries.extend_from_slice(user_entries);
        dict.install_user_dict(&dictionary_entries)
            .map_err(LoadError::UserDict)?;
        dict.install_custom_lexicons(&options.custom_lexicons)
            .map_err(LoadError::CustomLexicon)?;

        let affect =
            AffectStore::from_model_and_custom(affect_model, &options.custom_lexicons, |word| {
                dict.normalize(word).into_owned()
            })
            .map_err(LoadError::Affect)?;

        let mut tags: Vec<String> = Vec::new();
        let dict_tag_map: Vec<u8> = dict
            .tag_names
            .iter()
            .map(|tag| intern_tag(tag, &mut tags))
            .collect::<Result<_, _>>()?;
        let dict_to_pos: Vec<Option<u8>> = dict
            .tag_names
            .iter()
            .map(|tag| {
                pos.tag_names
                    .iter()
                    .position(|candidate| candidate == tag)
                    .map(|id| id as u8)
            })
            .collect();
        let pos_tag_map: Vec<u8> = pos
            .tag_names
            .iter()
            .map(|tag| intern_tag(tag, &mut tags))
            .collect::<Result<_, _>>()?;
        let builtin = BuiltinTags {
            url: intern_tag("FW", &mut tags)?,
            email: intern_tag("FW", &mut tags)?,
            eng: intern_tag("FW", &mut tags)?,
            num: intern_tag("Neu", &mut tags)?,
            time: intern_tag("Nd", &mut tags)?,
            punct: intern_tag("PUNCTUATIONCATEGORY", &mut tags)?,
            other: intern_tag("FW", &mut tags)?,
            unknown: intern_tag("FW", &mut tags)?,
        };
        let pos = pos::PosTagger::new(pos).map_err(LoadError::InvalidOptions)?;
        Ok(Segmenter {
            dict,
            bmes,
            pos,
            tags,
            dict_tag_map,
            pos_tag_map,
            dict_to_pos,
            builtin,
            affect,
        })
    }

    /// 統一詞性表：tag id → 名稱。
    pub fn tag_name(&self, tag: u8) -> &str {
        &self.tags[tag as usize]
    }

    /// 同 tag_name，id 越界回傳 None（FFI 層列舉詞性表用）。
    pub fn try_tag_name(&self, tag: u8) -> Option<&str> {
        self.tags.get(tag as usize).map(String::as_str)
    }

    /// 分詞：回傳借用輸入的詞切片序列（零拷貝）。
    /// 空白、標點等一律保留為獨立詞段，由呼叫端自行過濾。
    pub fn cut<'a>(&self, text: &'a str) -> Vec<&'a str> {
        self.cut_segments(text)
            .into_iter()
            .map(|s| &text[s.byte_start..s.byte_end])
            .collect()
    }

    /// 分詞並回傳實際觸發的規則 trace。
    pub fn cut_with_trace<'a>(&self, text: &'a str) -> (Vec<&'a str>, Vec<RuleTrace>) {
        let (segments, trace) = self.cut_segments_with_trace(text);
        (
            segments
                .into_iter()
                .map(|s| &text[s.byte_start..s.byte_end])
                .collect(),
            trace,
        )
    }

    /// 分詞：回傳帶 byte 區間與種類的詞段。
    pub fn cut_segments(&self, text: &str) -> Vec<Segment> {
        self.cut_segments_with_trace(text).0
    }

    /// 分詞並回傳帶 byte 區間的規則 trace。
    pub fn cut_segments_with_trace(&self, text: &str) -> (Vec<Segment>, Vec<RuleTrace>) {
        let normalized = self.dict.normalize(text);
        self.cut_normalized_with_trace(&normalized)
    }

    /// 分詞＋詞性標註。
    pub fn tokenize(&self, text: &str) -> Vec<Token> {
        self.tokenize_with_trace(text).0
    }

    /// 分詞＋詞性＋可選詞級情感；不進行句級或上下文情緒推論。
    pub fn annotate(&self, text: &str) -> Vec<AnnotatedToken> {
        let normalized = self.dict.normalize(text);
        let segments = self.cut_normalized_with_trace(&normalized).0;
        let tags = self.tag_segments(&normalized, &segments);
        segments
            .into_iter()
            .zip(tags)
            .map(|(segment, tag)| {
                let token = Token {
                    byte_start: segment.byte_start,
                    byte_end: segment.byte_end,
                    tag,
                };
                let word = &normalized[token.byte_start..token.byte_end];
                let source = match segment.kind {
                    SegKind::Dict(word_id) => {
                        self.dict
                            .custom_source(word_id)
                            .map(|(id, domain, priority)| LexiconSource {
                                id: id.to_owned(),
                                domain: domain.to_owned(),
                                priority,
                            })
                    }
                    _ => None,
                };
                AnnotatedToken {
                    token,
                    affect: self.affect.get(word).cloned(),
                    source,
                }
            })
            .collect()
    }

    /// 直接查詢單一完整詞形的情感資料。
    pub fn affect_for_word(&self, word: &str) -> Option<&AffectAnnotation> {
        let normalized = self.dict.normalize(word);
        self.affect.get(&normalized)
    }

    /// 已載入的詞級情感資產統計；不包含待分析原文。
    pub fn affect_stats(&self) -> &AffectStats {
        self.affect.stats()
    }

    /// 分詞＋詞性標註，並回傳實際觸發的規則 trace。
    pub fn tokenize_with_trace(&self, text: &str) -> (Vec<Token>, Vec<RuleTrace>) {
        let normalized = self.dict.normalize(text);
        let (segments, trace) = self.cut_normalized_with_trace(&normalized);
        let tags = self.tag_segments(&normalized, &segments);
        let tokens = segments
            .into_iter()
            .zip(tags)
            .map(|(segment, tag)| Token {
                byte_start: segment.byte_start,
                byte_end: segment.byte_end,
                tag,
            })
            .collect();
        (tokens, trace)
    }

    /// 主管線（輸入須已正規化）。
    fn cut_normalized_with_trace(&self, normalized: &str) -> (Vec<Segment>, Vec<RuleTrace>) {
        let mut out = Vec::with_capacity(normalized.len() / 4);
        let mut scratch = PipelineScratch {
            chunks: Vec::new(),
            segments: Vec::new(),
        };
        let mut trace = Vec::new();

        // 先保護跨 ASCII/Han 詞與少量人工核准詞界，其餘區段再走 pre hooks。
        let anchors = self.protected_dict_anchors(normalized);
        let mut cursor = 0usize;
        for anchor in anchors {
            self.cut_range(
                normalized,
                cursor,
                anchor.byte_start,
                &mut scratch,
                &mut out,
                &mut trace,
            );
            trace.push(RuleTrace {
                rule_id: rules::PRE_MIXED_DICT_ANCHOR,
                bucket: RuleBucket::Pre,
                action: RuleAction::Protect,
                byte_start: anchor.byte_start,
                byte_end: anchor.byte_end,
            });
            out.push(anchor);
            cursor = anchor.byte_end;
        }
        self.cut_range(
            normalized,
            cursor,
            normalized.len(),
            &mut scratch,
            &mut out,
            &mut trace,
        );

        // Post hooks are surface-form/deterministic only. POS runs after these
        // boundaries are final and therefore cannot participate in segmentation.
        let post_tags = vec![rules::PostTag::Other; out.len()];
        rules::apply_post_hooks(normalized, &mut out, &post_tags, &mut trace);
        (out, trace)
    }

    /// 找出跨 ASCII/Han 或人工核准的詞典詞，採左至右最長匹配並以機率破平手。
    fn protected_dict_anchors(&self, normalized: &str) -> Vec<Segment> {
        let mut candidates: Vec<(usize, usize, u32, f32)> = self
            .dict
            .matches(normalized)
            .filter_map(|m| {
                let word = &normalized[m.byte_start..m.byte_end];
                let has_ascii = word.chars().any(|c| c.is_ascii_alphanumeric());
                let has_han = word.chars().any(chunk::is_han_char);
                ((has_ascii && has_han) || CURATED_BOUNDARY_ANCHORS.contains(&word)).then(|| {
                    (
                        m.byte_start,
                        m.byte_end,
                        m.word_id,
                        m.custom_bias
                            .map(|value| value as f32)
                            .unwrap_or_else(|| self.dict.log_prob(m.word_id)),
                    )
                })
            })
            .collect();
        candidates.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| b.1.cmp(&a.1))
                .then_with(|| b.3.total_cmp(&a.3))
        });

        let mut out = Vec::new();
        let mut cursor = 0usize;
        for (start, end, word_id, _) in candidates {
            if start < cursor {
                continue;
            }
            out.push(Segment {
                byte_start: start,
                byte_end: end,
                kind: SegKind::Dict(word_id),
            });
            cursor = end;
        }
        out
    }

    /// 對不含混合詞典保護區間的子範圍執行原本主管線。
    fn cut_range(
        &self,
        normalized: &str,
        start: usize,
        end: usize,
        scratch: &mut PipelineScratch,
        out: &mut Vec<Segment>,
        trace: &mut Vec<RuleTrace>,
    ) {
        if start >= end {
            return;
        }
        scratch.chunks.clear();
        let trace_start = trace.len();
        chunk::split_with_trace(&normalized[start..end], &mut scratch.chunks, trace);
        for item in &mut trace[trace_start..] {
            item.byte_start += start;
            item.byte_end += start;
        }
        for ch in scratch.chunks.iter() {
            let byte_start = start + ch.byte_start;
            let byte_end = start + ch.byte_end;
            match ch.kind {
                ChunkKind::Han => {
                    self.cut_han_chunk(
                        normalized,
                        byte_start,
                        byte_end,
                        &mut scratch.segments,
                        out,
                    );
                }
                kind => out.push(Segment {
                    byte_start,
                    byte_end,
                    kind: direct_kind(kind),
                }),
            }
        }
    }

    fn cut_han_chunk(
        &self,
        normalized: &str,
        byte_start: usize,
        byte_end: usize,
        _scratch_segments: &mut Vec<Segment>,
        out: &mut Vec<Segment>,
    ) {
        dag::cut_viterbi(
            &self.dict,
            &self.bmes,
            &normalized[byte_start..byte_end],
            byte_start,
            out,
        );
    }

    /// 在固定詞界上執行 POS；非 Han 保護區段維持確定性詞性。
    fn tag_segments(&self, normalized: &str, segments: &[Segment]) -> Vec<u8> {
        let mut tags = vec![self.builtin.unknown; segments.len()];
        let mut cursor = 0usize;
        while cursor < segments.len() {
            if let Some(tag) = self.fixed_tag(&segments[cursor]) {
                tags[cursor] = tag;
                cursor += 1;
                continue;
            }
            let start = cursor;
            while cursor < segments.len() && self.fixed_tag(&segments[cursor]).is_none() {
                cursor += 1;
            }
            let run = &segments[start..cursor];
            let words: Vec<&str> = run
                .iter()
                .map(|segment| &normalized[segment.byte_start..segment.byte_end])
                .collect();
            let fallback: Vec<Option<u8>> = run
                .iter()
                .map(|segment| match segment.kind {
                    SegKind::Dict(id) if self.dict.is_user(id) => {
                        self.dict_to_pos[self.dict.tag(id) as usize]
                    }
                    _ => None,
                })
                .collect();
            for (offset, tag) in self.pos.tag_run(&words, &fallback).into_iter().enumerate() {
                tags[start + offset] = match run[offset].kind {
                    SegKind::Dict(id) if self.dict_to_pos[self.dict.tag(id) as usize].is_none() => {
                        self.dict_tag_map[self.dict.tag(id) as usize]
                    }
                    _ => self.pos_tag_map[tag as usize],
                };
            }
        }
        tags
    }

    fn fixed_tag(&self, segment: &Segment) -> Option<u8> {
        match segment.kind {
            SegKind::Dict(_) | SegKind::Oov => None,
            SegKind::Url => Some(self.builtin.url),
            SegKind::Email => Some(self.builtin.email),
            SegKind::Eng => Some(self.builtin.eng),
            SegKind::Num => Some(self.builtin.num),
            SegKind::Time => Some(self.builtin.time),
            SegKind::Punct => Some(self.builtin.punct),
            SegKind::Space | SegKind::Other => Some(self.builtin.other),
        }
    }
}

fn intern_tag(name: &str, tags: &mut Vec<String>) -> Result<u8, LoadError> {
    if let Some(i) = tags.iter().position(|t| t == name) {
        return Ok(i as u8);
    }
    if tags.len() > u8::MAX as usize {
        return Err(LoadError::TooManyTags(tags.len() + 1));
    }
    tags.push(name.to_string());
    Ok((tags.len() - 1) as u8)
}

/// 預切塊種類 → 詞段種類（Han 不在此列，另走 DAG）。
fn direct_kind(kind: ChunkKind) -> SegKind {
    match kind {
        ChunkKind::Url => SegKind::Url,
        ChunkKind::Email => SegKind::Email,
        ChunkKind::Eng => SegKind::Eng,
        ChunkKind::Num => SegKind::Num,
        ChunkKind::Time => SegKind::Time,
        ChunkKind::Punct => SegKind::Punct,
        ChunkKind::Space => SegKind::Space,
        ChunkKind::Other => SegKind::Other,
        ChunkKind::Han => unreachable!("Han chunk 走 DAG 分支"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unified_tag_table_rejects_more_than_256_names() {
        let mut tags = Vec::new();
        for i in 0..=u8::MAX {
            assert!(matches!(intern_tag(&format!("tag-{i}"), &mut tags), Ok(id) if id == i));
        }
        assert!(matches!(
            intern_tag("overflow", &mut tags),
            Err(LoadError::TooManyTags(257))
        ));
    }
}
