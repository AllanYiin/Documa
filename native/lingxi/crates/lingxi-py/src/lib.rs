//! Python binding：`lingxi._core`。
//!
//! Python 層介面（見 python/lingxi/__init__.py 的薄包裝）：
//!   seg = lingxi.Segmenter()               # 預設載入 wheel 內附模型
//!   seg.cut("文字")                         # -> list[str]
//!   seg.tokenize("文字")                    # -> list[Token]，offset 為字元（code point）座標
//!   seg.cut_batch(texts)                   # rayon 平行，釋放 GIL
//!
//! byte offset → 字元 offset 的轉換在輸出時單趟完成，符合 Python 切片直覺。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// 帶詞性與字元位置的分詞結果。
#[pyclass(frozen, get_all)]
struct Token {
    /// 詞（原文切片的複本；跨 FFI 邊界必須複製）。
    word: String,
    /// 詞性名稱（統一詞性表）。
    tag: String,
    /// 起始字元（code point）位置。
    start: usize,
    /// 結束字元位置（exclusive）。
    end: usize,
}

/// 原文中的一句（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Sentence {
    text: String,
    start: usize,
    end: usize,
    index: usize,
}

/// 原文中的一個結構感知子句（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Clause {
    text: String,
    start: usize,
    end: usize,
    sentence_index: usize,
    clause_index: usize,
    list_item: bool,
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
struct SummarySignals {
    proper_noun_count: usize,
    model_proper_noun_count: usize,
    negation_count: usize,
    emphasis_count: usize,
    list_item: bool,
    object_name_count: usize,
    date_count: usize,
    number_count: usize,
    quantity_count: usize,
    money_count: usize,
    acronym_count: usize,
    spans: Vec<(String, String, usize, usize)>,
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
struct SummaryScore {
    relevance: f32,
    coverage_gain: f32,
    novelty: f32,
    signal: f32,
    final_score: f32,
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
struct SummaryBlock {
    index: usize,
    kind: String,
    byte_start: usize,
    byte_end: usize,
    depth: usize,
    decision: String,
    source_text: String,
    output_text: String,
    selected_spans: Vec<(String, usize, usize, bool)>,
    signals: SummarySignals,
    score: Option<SummaryScore>,
    children: Vec<SummaryBlock>,
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
struct SummaryBudget {
    requested_max_blocks: usize,
    selected_ranked_blocks: usize,
    preserved_blocks: usize,
    forced_negation_clauses: usize,
    actual_output_blocks: usize,
    overflow_reasons: Vec<String>,
}

#[pyclass(frozen, get_all)]
struct SummaryDocument {
    schema_version: u32,
    mode: String,
    text: String,
    blocks: Vec<SummaryBlock>,
    budget: SummaryBudget,
    input_chars: usize,
    output_chars: usize,
    reduction_percent: f32,
}

/// 關鍵短語及其原文位置（Python 字元座標）。
#[pyclass(frozen, get_all)]
struct Keyphrase {
    phrase: String,
    weight: f32,
    occurrences: usize,
    spans: Vec<(usize, usize)>,
}

#[pymethods]
impl Token {
    fn __repr__(&self) -> String {
        format!(
            "Token({:?}, {:?}, {}, {})",
            self.word, self.tag, self.start, self.end
        )
    }
}

/// 帶詞性與可選情感的字元位置結果。
#[pyclass(frozen, get_all)]
struct AnnotatedToken {
    word: String,
    tag: String,
    start: usize,
    end: usize,
    polarity: Option<String>,
    emotions: Vec<String>,
    context_dependent: bool,
    semantic_flags: Vec<String>,
    appraisals: Vec<String>,
    source: Option<String>,
    domain: Option<String>,
    priority: Option<i8>,
    affect_source: Option<String>,
}

/// 分詞器。執行緒安全，可在多執行緒間共享。
#[pyclass(frozen)]
struct Segmenter {
    inner: lingxi_core::Segmenter,
}

#[pymethods]
impl Segmenter {
    /// 從資產目錄建立（目錄需含 dict.bin / hmm_bmes.bin / hmm_pos.bin）。
    /// `user_dict` 為 jieba 格式詞條行（`詞 [頻率] [詞性]`）；檔案讀取由
    /// Python 層包裝（見 __init__.py 的 load()）。
    #[new]
    #[pyo3(signature = (asset_dir, user_dict=None, lexicons=None))]
    fn new(
        asset_dir: &str,
        user_dict: Option<Vec<String>>,
        lexicons: Option<Vec<String>>,
    ) -> PyResult<Self> {
        let entries = user_dict
            .map(|lines| lingxi_core::parse_user_dict(&lines.join("\n")))
            .unwrap_or_default();
        let custom_lexicons = lexicons
            .unwrap_or_default()
            .iter()
            .map(|text| lingxi_core::parse_custom_lexicon(text))
            .collect::<Result<Vec<_>, _>>()
            .map_err(PyValueError::new_err)?;
        lingxi_core::Segmenter::from_asset_dir_with_user_dict_and_options(
            asset_dir,
            &entries,
            lingxi_core::SegmenterOptions { custom_lexicons },
        )
        .map(|inner| Segmenter { inner })
        .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// TextRank 關鍵字抽取 → [(詞, 權重)]，權重降冪。
    /// `allow_tags` 指定候選詞性白名單；預設為名詞類/動詞/英文詞。
    #[pyo3(signature = (
        text,
        top_k=20,
        allow_tags=None,
        proper_noun_enabled=true,
        proper_noun_weight=0.25,
        proper_noun_max_ratio=0.4,
        window_size=5,
        damping=0.85,
        max_iterations=10,
        tolerance=None,
        min_chars=2,
        stopwords=None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn extract_keywords(
        &self,
        text: &str,
        top_k: usize,
        allow_tags: Option<Vec<String>>,
        proper_noun_enabled: bool,
        proper_noun_weight: f32,
        proper_noun_max_ratio: f32,
        window_size: usize,
        damping: f32,
        max_iterations: usize,
        tolerance: Option<f32>,
        min_chars: usize,
        stopwords: Option<Vec<String>>,
    ) -> Vec<(String, f32)> {
        let tag_refs: Option<Vec<&str>> = allow_tags
            .as_ref()
            .map(|v| v.iter().map(String::as_str).collect());
        self.inner
            .extract_keywords_configured(
                text,
                top_k,
                tag_refs.as_deref(),
                &lingxi_core::KeywordExtractionOptions {
                    rank: lingxi_core::TextRankOptions {
                        window_size,
                        damping,
                        max_iterations,
                        tolerance,
                    },
                    min_chars,
                    stopwords: stopwords.unwrap_or_default(),
                    proper_noun: lingxi_core::KeywordOptions {
                        proper_noun_enabled,
                        proper_noun_weight,
                        proper_noun_max_ratio,
                    },
                },
            )
            .into_iter()
            .map(|k| (k.word, k.weight))
            .collect()
    }

    /// 中文斷句，位置為 Python code point offset。
    #[pyo3(signature = (text, semicolon_boundary=false))]
    fn split_sentences(&self, text: &str, semicolon_boundary: bool) -> Vec<Sentence> {
        self.inner
            .split_sentences_with_options(
                text,
                lingxi_core::SentenceSplitOptions { semicolon_boundary },
            )
            .into_iter()
            .map(|sentence| Sentence {
                start: byte_to_char(text, sentence.byte_start),
                end: byte_to_char(text, sentence.byte_end),
                index: sentence.sentence_index,
                text: sentence.text,
            })
            .collect()
    }

    /// 結構感知子句抽取；強調、括號、引號與行內程式碼內容不會被拆開。
    #[pyo3(signature = (text, comma_boundary=true, semicolon_boundary=true, colon_boundary=true))]
    fn split_clauses(
        &self,
        text: &str,
        comma_boundary: bool,
        semicolon_boundary: bool,
        colon_boundary: bool,
    ) -> Vec<Clause> {
        self.inner
            .split_clauses_with_options(
                text,
                lingxi_core::ClauseSplitOptions {
                    comma_boundary,
                    semicolon_boundary,
                    colon_boundary,
                },
            )
            .into_iter()
            .map(|clause| Clause {
                start: byte_to_char(text, clause.byte_start),
                end: byte_to_char(text, clause.byte_end),
                sentence_index: clause.sentence_index,
                clause_index: clause.clause_index,
                list_item: clause.list_item,
                text: clause.text,
            })
            .collect()
    }

    /// schema v2 結構感知摘要。
    #[pyo3(signature = (
        text,
        max_blocks=3,
        min_sentence_chars=8,
        min_token_chars=1,
        stopwords=None,
        similarity="bm25",
        preserve_order=true,
        redundancy_threshold=Some(0.8),
        min_explainability=Some(0.35),
        comma_boundary=false,
        semicolon_boundary=false,
        colon_boundary=false,
        long_block_min_clauses=3,
        long_block_min_chars=240,
        long_block_min_words=60,
        max_clauses_per_long_block=2,
        max_clauses_per_long_list_item=2
    ))]
    #[allow(clippy::too_many_arguments)]
    fn extract_summary(
        &self,
        text: &str,
        max_blocks: usize,
        min_sentence_chars: usize,
        min_token_chars: usize,
        stopwords: Option<Vec<String>>,
        similarity: &str,
        preserve_order: bool,
        redundancy_threshold: Option<f32>,
        min_explainability: Option<f32>,
        comma_boundary: bool,
        semicolon_boundary: bool,
        colon_boundary: bool,
        long_block_min_clauses: usize,
        long_block_min_chars: usize,
        long_block_min_words: usize,
        max_clauses_per_long_block: usize,
        max_clauses_per_long_list_item: usize,
    ) -> PyResult<SummaryDocument> {
        let similarity = match similarity {
            "bm25" => lingxi_core::SentenceSimilarity::Bm25,
            "lexical" | "overlap" => lingxi_core::SentenceSimilarity::LexicalOverlap,
            other => {
                return Err(PyValueError::new_err(format!(
                    "未知相似度 {other:?}（可用 bm25|lexical）"
                )))
            }
        };
        let document = self.inner.extract_summary_with_options(
            text,
            max_blocks,
            &lingxi_core::SummaryOptions {
                min_sentence_chars,
                min_token_chars,
                stopwords: stopwords.unwrap_or_default(),
                similarity,
                redundancy_threshold,
                min_explainability,
                preserve_original_order: preserve_order,
                comma_boundary,
                semicolon_boundary,
                colon_boundary,
                long_block_min_clauses,
                long_block_min_chars,
                long_block_min_words,
                max_clauses_per_long_block,
                max_clauses_per_long_list_item,
                ..lingxi_core::SummaryOptions::default()
            },
        );
        Ok(to_python_summary(document))
    }

    /// 由相鄰高排名關鍵詞組成關鍵短語。
    #[pyo3(signature = (
        text,
        top_k=10,
        keyword_count=None,
        min_occurrences=1,
        max_terms=4,
        stopwords=None
    ))]
    fn extract_keyphrases(
        &self,
        text: &str,
        top_k: usize,
        keyword_count: Option<usize>,
        min_occurrences: usize,
        max_terms: usize,
        stopwords: Option<Vec<String>>,
    ) -> Vec<Keyphrase> {
        self.inner
            .extract_keyphrases_with_options(
                text,
                &lingxi_core::KeyphraseOptions {
                    top_k,
                    keyword_count: keyword_count.unwrap_or_else(|| top_k.saturating_mul(4).max(20)),
                    min_occurrences,
                    max_terms,
                    keywords: lingxi_core::KeywordExtractionOptions {
                        stopwords: stopwords.unwrap_or_default(),
                        ..lingxi_core::KeywordExtractionOptions::default()
                    },
                },
            )
            .into_iter()
            .map(|phrase| Keyphrase {
                phrase: phrase.phrase,
                weight: phrase.weight,
                occurrences: phrase.occurrences,
                spans: phrase
                    .spans
                    .into_iter()
                    .map(|span| {
                        (
                            byte_to_char(text, span.byte_start),
                            byte_to_char(text, span.byte_end),
                        )
                    })
                    .collect(),
            })
            .collect()
    }

    /// 分詞 → 詞列表。
    fn cut(&self, text: &str) -> Vec<String> {
        self.inner
            .cut(text)
            .into_iter()
            .map(str::to_string)
            .collect()
    }

    /// 分詞＋詞性 → Token 列表（start/end 為字元座標）。
    fn tokenize(&self, text: &str) -> Vec<Token> {
        tokens_of(&self.inner, text)
    }

    /// 分詞＋詞性＋詞級情感。
    fn annotate(&self, text: &str) -> Vec<AnnotatedToken> {
        annotated_tokens_of(&self.inner, text)
    }

    /// 批次分詞：釋放 GIL 並以 rayon 平行，輸出順序與輸入一致。
    fn cut_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<String>> {
        py.allow_threads(|| {
            texts
                .par_iter()
                .map(|t| self.inner.cut(t).into_iter().map(str::to_string).collect())
                .collect()
        })
    }

    /// 批次分詞＋詞性：釋放 GIL 並以 rayon 平行。
    fn tokenize_batch(&self, py: Python<'_>, texts: Vec<String>) -> Vec<Vec<Token>> {
        py.allow_threads(|| {
            texts
                .par_iter()
                .map(|t| tokens_of(&self.inner, t))
                .collect()
        })
    }
}

/// 分詞並轉為 Python Token（byte offset → 字元 offset 單趟轉換）。
fn tokens_of(seg: &lingxi_core::Segmenter, text: &str) -> Vec<Token> {
    let raw = seg.tokenize(text);
    let mut out = Vec::with_capacity(raw.len());
    // tokens 依 byte 位置遞增且無縫覆蓋，游標單趟前進即可換算字元位置。
    let mut cursor_byte = 0usize;
    let mut cursor_char = 0usize;
    for t in raw {
        cursor_char += text[cursor_byte..t.byte_start].chars().count();
        let word = &text[t.byte_start..t.byte_end];
        let char_len = word.chars().count();
        out.push(Token {
            word: word.to_string(),
            tag: seg.tag_name(t.tag).to_string(),
            start: cursor_char,
            end: cursor_char + char_len,
        });
        cursor_byte = t.byte_end;
        cursor_char += char_len;
    }
    out
}

fn byte_to_char(text: &str, byte: usize) -> usize {
    text[..byte].chars().count()
}

fn to_python_summary(document: lingxi_core::SummaryDocument) -> SummaryDocument {
    let blocks = document.blocks.into_iter().map(to_python_block).collect();
    SummaryDocument {
        schema_version: document.schema_version,
        mode: document.mode,
        text: document.text,
        blocks,
        budget: SummaryBudget {
            requested_max_blocks: document.budget.requested_max_blocks,
            selected_ranked_blocks: document.budget.selected_ranked_blocks,
            preserved_blocks: document.budget.preserved_blocks,
            forced_negation_clauses: document.budget.forced_negation_clauses,
            actual_output_blocks: document.budget.actual_output_blocks,
            overflow_reasons: document.budget.overflow_reasons,
        },
        input_chars: document.input_chars,
        output_chars: document.output_chars,
        reduction_percent: document.reduction_percent,
    }
}

fn to_python_block(block: lingxi_core::SummaryBlock) -> SummaryBlock {
    SummaryBlock {
        index: block.index,
        kind: summary_block_kind_name(block.kind).to_string(),
        byte_start: block.byte_start,
        byte_end: block.byte_end,
        depth: block.depth,
        decision: summary_decision_name(block.decision).to_string(),
        source_text: block.source_text,
        output_text: block.output_text,
        selected_spans: block
            .selected_spans
            .into_iter()
            .map(|span| {
                (
                    span.text,
                    span.byte_start,
                    span.byte_end,
                    span.forced_by_negation,
                )
            })
            .collect(),
        signals: SummarySignals {
            proper_noun_count: block.signals.proper_noun_count,
            model_proper_noun_count: block.signals.model_proper_noun_count,
            negation_count: block.signals.negation_count,
            emphasis_count: block.signals.emphasis_count,
            list_item: block.signals.list_item,
            object_name_count: block.signals.object_name_count,
            date_count: block.signals.date_count,
            number_count: block.signals.number_count,
            quantity_count: block.signals.quantity_count,
            money_count: block.signals.money_count,
            acronym_count: block.signals.acronym_count,
            spans: block
                .signals
                .spans
                .into_iter()
                .map(|span| {
                    (
                        signal_kind_name(span.kind).to_string(),
                        span.text,
                        span.byte_start,
                        span.byte_end,
                    )
                })
                .collect(),
        },
        score: block.score.map(|score| SummaryScore {
            relevance: score.relevance,
            coverage_gain: score.coverage_gain,
            novelty: score.novelty,
            signal: score.signal,
            final_score: score.final_score,
        }),
        children: block.children.into_iter().map(to_python_block).collect(),
    }
}

fn summary_block_kind_name(value: lingxi_core::SummaryBlockKind) -> &'static str {
    match value {
        lingxi_core::SummaryBlockKind::Paragraph => "paragraph",
        lingxi_core::SummaryBlockKind::Heading => "heading",
        lingxi_core::SummaryBlockKind::FencedCode => "fenced-code",
        lingxi_core::SummaryBlockKind::IndentedCode => "indented-code",
        lingxi_core::SummaryBlockKind::OrderedListItem => "ordered-list-item",
        lingxi_core::SummaryBlockKind::UnorderedListItem => "unordered-list-item",
        lingxi_core::SummaryBlockKind::Blockquote => "blockquote",
        lingxi_core::SummaryBlockKind::Table => "table",
        lingxi_core::SummaryBlockKind::Html => "html",
        lingxi_core::SummaryBlockKind::ThematicBreak => "thematic-break",
    }
}

fn summary_decision_name(value: lingxi_core::SummaryDecision) -> &'static str {
    match value {
        lingxi_core::SummaryDecision::PreserveExact => "preserve_exact",
        lingxi_core::SummaryDecision::SelectExact => "select_exact",
        lingxi_core::SummaryDecision::SummarizeWithin => "summarize_within",
        lingxi_core::SummaryDecision::ContextOnly => "context_only",
        lingxi_core::SummaryDecision::Omit => "omit",
    }
}

fn signal_kind_name(value: lingxi_core::SignalKind) -> &'static str {
    match value {
        lingxi_core::SignalKind::ProperNoun => "proper_noun",
        lingxi_core::SignalKind::Negation => "negation",
        lingxi_core::SignalKind::Emphasis => "emphasis",
        lingxi_core::SignalKind::ListItem => "list_item",
        lingxi_core::SignalKind::ObjectName => "object_name",
        lingxi_core::SignalKind::Date => "date",
        lingxi_core::SignalKind::Number => "number",
        lingxi_core::SignalKind::Quantity => "quantity",
        lingxi_core::SignalKind::Money => "money",
        lingxi_core::SignalKind::Acronym => "acronym",
    }
}

fn polarity_name(value: lingxi_core::Polarity) -> String {
    match value {
        lingxi_core::Polarity::Positive => "positive",
        lingxi_core::Polarity::Negative => "negative",
        lingxi_core::Polarity::Neutral => "neutral",
        lingxi_core::Polarity::Mixed => "mixed",
        lingxi_core::Polarity::Contextual => "contextual",
    }
    .into()
}

fn annotated_tokens_of(seg: &lingxi_core::Segmenter, text: &str) -> Vec<AnnotatedToken> {
    let raw = seg.annotate(text);
    let mut out = Vec::with_capacity(raw.len());
    let mut cursor_byte = 0usize;
    let mut cursor_char = 0usize;
    for item in raw {
        let token = item.token;
        cursor_char += text[cursor_byte..token.byte_start].chars().count();
        let word = &text[token.byte_start..token.byte_end];
        let char_len = word.chars().count();
        let (polarity, emotions, context_dependent, semantic_flags, appraisals, affect_source) =
            match item.affect {
                Some(affect) => (
                    Some(polarity_name(affect.polarity)),
                    affect.emotions,
                    affect.context_dependent,
                    affect.semantic_flags,
                    affect.appraisals,
                    affect.source,
                ),
                None => (None, Vec::new(), false, Vec::new(), Vec::new(), None),
            };
        let (source, domain, priority) = match item.source {
            Some(source) => (Some(source.id), Some(source.domain), Some(source.priority)),
            None => (None, None, None),
        };
        out.push(AnnotatedToken {
            word: word.to_string(),
            tag: seg.tag_name(token.tag).to_string(),
            start: cursor_char,
            end: cursor_char + char_len,
            polarity,
            emotions,
            context_dependent,
            semantic_flags,
            appraisals,
            source,
            domain,
            priority,
            affect_source,
        });
        cursor_byte = token.byte_end;
        cursor_char += char_len;
    }
    out
}

/// Python 模組進入點。
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<Segmenter>()?;
    m.add_class::<Token>()?;
    m.add_class::<AnnotatedToken>()?;
    m.add_class::<Sentence>()?;
    m.add_class::<Clause>()?;
    m.add_class::<SummarySignals>()?;
    m.add_class::<SummaryScore>()?;
    m.add_class::<SummaryBlock>()?;
    m.add_class::<SummaryBudget>()?;
    m.add_class::<SummaryDocument>()?;
    m.add_class::<Keyphrase>()?;
    Ok(())
}
