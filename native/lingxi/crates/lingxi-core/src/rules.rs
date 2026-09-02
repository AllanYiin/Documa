//! 實驗性規則 registry。
//!
//! 規則分成兩桶：pre hooks 在一般預切塊前保護不可拆的 byte span；
//! post hooks 在 DAG/HMM/POS 產出後依相鄰詞段與粗粒度詞性修正邊界。
//! 這一層刻意先追求可追蹤、可停用與有測試，不把零散案例直接塞回主管線。

use std::sync::LazyLock;

use regex::Regex;

use crate::chunk::ChunkKind;
use crate::segment::{SegKind, Segment};

pub const PRE_EMAIL: &str = "pre.email";
pub const PRE_URL: &str = "pre.url";
pub const PRE_MODEL_IDENTIFIER: &str = "pre.model_identifier";
pub const PRE_CHINESE_TIME: &str = "pre.chinese_time";
pub const PRE_CHINESE_QUANTITY: &str = "pre.chinese_quantity";
pub const PRE_NUMERIC_TIME: &str = "pre.numeric_time";
pub const PRE_ORDINAL_PERIOD: &str = "pre.ordinal_period";
pub const PRE_NUMBER: &str = "pre.number";
pub const PRE_MIXED_DICT_ANCHOR: &str = "pre.mixed_dict_anchor";
pub const POST_CHINESE_NUMERIC_TIME: &str = "post.chinese_numeric_time";
pub const POST_NUMERAL_MEASURE: &str = "post.numeral_measure";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuleBucket {
    Pre,
    Post,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuleStatus {
    Stable,
    Experimental,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuleAction {
    Protect,
    Merge,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RuleInfo {
    pub id: &'static str,
    pub bucket: RuleBucket,
    pub status: RuleStatus,
    pub description: &'static str,
    pub examples: &'static [&'static str],
    pub counter_examples: &'static [&'static str],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RuleTrace {
    pub rule_id: &'static str,
    pub bucket: RuleBucket,
    pub action: RuleAction,
    pub byte_start: usize,
    pub byte_end: usize,
}

static KNOWN_RULES: &[RuleInfo] = &[
    RuleInfo {
        id: PRE_EMAIL,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護 Email，不讓網域、標點與帳號被拆開",
        examples: &["service@example.com"],
        counter_examples: &["example.com"],
    },
    RuleInfo {
        id: PRE_URL,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護有 scheme 的 URL 與裸網域",
        examples: &["https://www.ptt.cc/bbs", "example.technology"],
        counter_examples: &["a.b"],
    },
    RuleInfo {
        id: PRE_MODEL_IDENTIFIER,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Experimental,
        description: "保護以連字號連接的英數型號或代碼",
        examples: &["S-400", "F-35", "COVID-19"],
        counter_examples: &["A - B", "-400", "123-456"],
    },
    RuleInfo {
        id: PRE_CHINESE_TIME,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護中文數字接明確期間單位，避免單位與後字黏連",
        examples: &["兩個月後 → 兩個月/後", "三年", "十天"],
        counter_examples: &["第二年", "唯一年度"],
    },
    RuleInfo {
        id: PRE_CHINESE_QUANTITY,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護中文數字接個、人、隻，避免詞典詞跨過數量邊界",
        examples: &["三個人 → 三個/人", "三人", "三隻"],
        counter_examples: &["第三人稱", "個人"],
    },
    RuleInfo {
        id: PRE_NUMERIC_TIME,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "ASCII 數值接年月日號，含可選的「個」",
        examples: &["2019年", "7月", "3個月"],
        counter_examples: &["第二年", "第2季"],
    },
    RuleInfo {
        id: PRE_ORDINAL_PERIOD,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Experimental,
        description: "保護「第 + ASCII 數字 + 期間單位」",
        examples: &["第2季", "第10期"],
        counter_examples: &["第二季", "第2名"],
    },
    RuleInfo {
        id: PRE_NUMBER,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護小數、千分位、中文數量級與百分比",
        examples: &["1,000", "57.16%", "1200億"],
        counter_examples: &["abc123", "123abc"],
    },
    RuleInfo {
        id: PRE_MIXED_DICT_ANCHOR,
        bucket: RuleBucket::Pre,
        status: RuleStatus::Stable,
        description: "保護詞典內跨 ASCII/Han 邊界的完整詞",
        examples: &["AV女優", "3D列印", "90後"],
        counter_examples: &["任意未登入英中混合字串"],
    },
    RuleInfo {
        id: POST_CHINESE_NUMERIC_TIME,
        bucket: RuleBucket::Post,
        status: RuleStatus::Experimental,
        description: "數詞接明確期間單位時合併為時間詞",
        examples: &["三/m + 年/q → 三年/t", "第十/m + 季/t → 第十季/t"],
        counter_examples: &["一起", "十分", "唯一年度"],
    },
    RuleInfo {
        id: POST_NUMERAL_MEASURE,
        bucket: RuleBucket::Post,
        status: RuleStatus::Experimental,
        description: "相鄰且連續的數詞 m 與量詞「個」合併並標為數詞",
        examples: &["一/m + 個/q → 一個/m"],
        counter_examples: &["一起", "十分", "1200億/m + 美元/q"],
    },
];

pub fn known_rules() -> &'static [RuleInfo] {
    KNOWN_RULES
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct PreMatch {
    pub byte_start: usize,
    pub byte_end: usize,
    pub kind: ChunkKind,
    pub rule_id: &'static str,
    priority: u16,
}

type PreMatcher = fn(&str, &PreHook, &mut Vec<PreMatch>);

struct PreHook {
    id: &'static str,
    priority: u16,
    kind: ChunkKind,
    matcher: PreMatcher,
}

static EMAIL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?i)[a-z0-9_\-.]+@(\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.|([a-z0-9\-]+\.)+)([a-z][a-z0-9\-]{1,62}|[0-9]{1,3})\]?",
    )
    .unwrap()
});

static URL_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?i)(https?://|ftps?://)?",
        r"(([0-9a-z_!~*'().&=+$%\-]+: )?[0-9a-z_!~*'().&=+$%\-]+@)?",
        r"(([0-9]{1,3}\.){3}[0-9]{1,3}",
        r"|([0-9a-z_!~*'()\-]+\.)*([0-9a-z][0-9a-z\-]{0,61})?[0-9a-z]\.[a-z][a-z0-9\-]{1,62})",
        r"(:[0-9]{1,4})?",
        r"(/[0-9a-z_!~*'().;?:@&=+$,%#\-]+)*/?",
    ))
    .unwrap()
});

static MODEL_IDENTIFIER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)[a-z][a-z0-9]*(?:-[a-z0-9]+)+").unwrap());
static CHINESE_TIME_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"[〇零一二兩三四五六七八九十百千萬億兆壹貳參肆伍陸柒捌玖拾佰仟]+(?:個)?(?:星期|禮拜|年|月|週|周|天|日|季)",
    )
    .unwrap()
});
static CHINESE_QUANTITY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[〇零一二兩三四五六七八九十百千萬億兆壹貳參肆伍陸柒捌玖拾佰仟]+[個人隻]").unwrap()
});
static NUMERIC_TIME_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[0-9]+(?:[.,/][0-9]+)*(?:個)?[年月日號]").unwrap());
static ORDINAL_PERIOD_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"第[0-9]+[年季月週周天日期]").unwrap());
static NUMBER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"[0-9]+(?:[.,/][0-9]+)*(?:[十百千萬億兆])?(?:[%％])?").unwrap());

static PRE_HOOKS: &[PreHook] = &[
    PreHook {
        id: PRE_EMAIL,
        priority: 600,
        kind: ChunkKind::Email,
        matcher: match_email,
    },
    PreHook {
        id: PRE_URL,
        priority: 500,
        kind: ChunkKind::Url,
        matcher: match_url,
    },
    PreHook {
        id: PRE_MODEL_IDENTIFIER,
        priority: 400,
        kind: ChunkKind::Eng,
        matcher: match_model_identifier,
    },
    PreHook {
        id: PRE_ORDINAL_PERIOD,
        priority: 350,
        kind: ChunkKind::Time,
        matcher: match_ordinal_period,
    },
    PreHook {
        id: PRE_CHINESE_TIME,
        priority: 360,
        kind: ChunkKind::Time,
        matcher: match_chinese_time,
    },
    PreHook {
        id: PRE_CHINESE_QUANTITY,
        priority: 340,
        kind: ChunkKind::Num,
        matcher: match_chinese_quantity,
    },
    PreHook {
        id: PRE_NUMERIC_TIME,
        priority: 300,
        kind: ChunkKind::Time,
        matcher: match_numeric_time,
    },
    PreHook {
        id: PRE_NUMBER,
        priority: 100,
        kind: ChunkKind::Num,
        matcher: match_number,
    },
];

fn push_regex_matches(re: &Regex, text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    out.extend(re.find_iter(text).map(|m| PreMatch {
        byte_start: m.start(),
        byte_end: m.end(),
        kind: hook.kind,
        rule_id: hook.id,
        priority: hook.priority,
    }));
}

fn match_email(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    push_regex_matches(&EMAIL_RE, text, hook, out);
}

fn match_url(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    let before = out.len();
    push_regex_matches(&URL_RE, text, hook, out);
    out[before..].iter_mut().for_each(|m| {
        if m.byte_end - m.byte_start < 4 {
            m.priority = 0;
        }
    });
    out.retain(|m| m.rule_id != hook.id || m.priority > 0);
}

fn match_model_identifier(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    push_regex_matches(&MODEL_IDENTIFIER_RE, text, hook, out);
}

fn match_ordinal_period(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    push_regex_matches(&ORDINAL_PERIOD_RE, text, hook, out);
}

fn match_chinese_time(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    let before = out.len();
    push_regex_matches(&CHINESE_TIME_RE, text, hook, out);
    out[before..].iter_mut().for_each(|m| {
        if text[..m.byte_start].ends_with('第') {
            m.priority = 0;
        }
    });
    out.retain(|m| m.rule_id != hook.id || m.priority > 0);
}
fn match_chinese_quantity(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    let before = out.len();
    push_regex_matches(&CHINESE_QUANTITY_RE, text, hook, out);
    out[before..].iter_mut().for_each(|m| {
        if text[..m.byte_start].ends_with('第') {
            m.priority = 0;
        }
    });
    out.retain(|m| m.rule_id != hook.id || m.priority > 0);
}

fn match_numeric_time(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    let before = out.len();
    push_regex_matches(&NUMERIC_TIME_RE, text, hook, out);
    out[before..].iter_mut().for_each(|m| {
        if text[..m.byte_start]
            .chars()
            .next_back()
            .is_some_and(is_plain_alnum)
        {
            m.priority = 0;
        }
    });
    out.retain(|m| m.rule_id != hook.id || m.priority > 0);
}

fn match_number(text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    push_numeric_matches(&NUMBER_RE, text, hook, out);
}

fn push_numeric_matches(re: &Regex, text: &str, hook: &PreHook, out: &mut Vec<PreMatch>) {
    let before = out.len();
    push_regex_matches(re, text, hook, out);
    out[before..].iter_mut().for_each(|m| {
        if embedded_in_alnum(text, m.byte_start, m.byte_end) {
            m.priority = 0;
        }
    });
    out.retain(|m| m.rule_id != hook.id || m.priority > 0);
}

fn embedded_in_alnum(text: &str, start: usize, end: usize) -> bool {
    text[..start]
        .chars()
        .next_back()
        .is_some_and(is_plain_alnum)
        || text[end..].chars().next().is_some_and(is_plain_alnum)
}

fn is_plain_alnum(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '&' | '/' | '_')
}

pub(crate) fn collect_pre_matches(text: &str) -> Vec<PreMatch> {
    let mut candidates = Vec::new();
    for hook in PRE_HOOKS {
        (hook.matcher)(text, hook, &mut candidates);
    }
    candidates.sort_by(|a, b| {
        a.byte_start
            .cmp(&b.byte_start)
            .then_with(|| b.priority.cmp(&a.priority))
            .then_with(|| b.byte_end.cmp(&a.byte_end))
    });

    let mut selected = Vec::new();
    let mut cursor = 0usize;
    for candidate in candidates {
        if candidate.byte_start < cursor {
            continue;
        }
        cursor = candidate.byte_end;
        selected.push(candidate);
    }
    selected
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PostTag {
    Numeral,
    Measure,
    Other,
}

#[derive(Clone, Copy, Debug)]
struct PostMatch {
    token_start: usize,
    token_end: usize,
    kind: SegKind,
    rule_id: &'static str,
    priority: u16,
}

type PostMatcher = fn(&str, &[Segment], &[PostTag], &PostHook, &mut Vec<PostMatch>);

struct PostHook {
    id: &'static str,
    priority: u16,
    matcher: PostMatcher,
}

static POST_HOOKS: &[PostHook] = &[
    PostHook {
        id: POST_CHINESE_NUMERIC_TIME,
        priority: 300,
        matcher: match_chinese_numeric_time,
    },
    PostHook {
        id: POST_NUMERAL_MEASURE,
        priority: 200,
        matcher: match_numeral_measure,
    },
];

fn segment_text<'a>(text: &'a str, segment: &Segment) -> &'a str {
    &text[segment.byte_start..segment.byte_end]
}

fn is_chinese_numeral(c: char) -> bool {
    matches!(
        c,
        '〇' | '零'
            | '一'
            | '二'
            | '三'
            | '四'
            | '五'
            | '六'
            | '七'
            | '八'
            | '九'
            | '十'
            | '百'
            | '千'
            | '萬'
            | '億'
            | '兆'
            | '兩'
            | '壹'
            | '貳'
            | '參'
            | '肆'
            | '伍'
            | '陸'
            | '柒'
            | '捌'
            | '玖'
            | '拾'
            | '佰'
            | '仟'
    )
}

fn parse_chinese_numeral(text: &str) -> Option<bool> {
    let text = text.strip_prefix('第').unwrap_or(text);
    let (core, has_ge) = match text.strip_suffix('個') {
        Some(core) => (core, true),
        None => (text, false),
    };
    (!core.is_empty() && core.chars().all(is_chinese_numeral)).then_some(has_ge)
}

fn is_period_unit(text: &str) -> bool {
    matches!(
        text,
        "年" | "季" | "月" | "週" | "周" | "天" | "日" | "號" | "期" | "星期" | "禮拜"
    )
}

fn is_ge_period_unit(text: &str) -> bool {
    matches!(text, "年" | "月" | "星期" | "禮拜")
}

fn match_chinese_numeric_time(
    text: &str,
    segments: &[Segment],
    _tags: &[PostTag],
    hook: &PostHook,
    out: &mut Vec<PostMatch>,
) {
    for i in 0..segments.len() {
        let numeral = segment_text(text, &segments[i]);
        let Some(has_ge) = parse_chinese_numeral(numeral) else {
            continue;
        };

        if i + 1 < segments.len() && segments[i].byte_end == segments[i + 1].byte_start {
            let unit = segment_text(text, &segments[i + 1]);
            if (has_ge && is_ge_period_unit(unit)) || (!has_ge && is_period_unit(unit)) {
                out.push(PostMatch {
                    token_start: i,
                    token_end: i + 2,
                    kind: SegKind::Time,
                    rule_id: hook.id,
                    priority: hook.priority,
                });
                continue;
            }
        }

        if !has_ge
            && i + 2 < segments.len()
            && segments[i].byte_end == segments[i + 1].byte_start
            && segments[i + 1].byte_end == segments[i + 2].byte_start
            && segment_text(text, &segments[i + 1]) == "個"
            && is_ge_period_unit(segment_text(text, &segments[i + 2]))
        {
            out.push(PostMatch {
                token_start: i,
                token_end: i + 3,
                kind: SegKind::Time,
                rule_id: hook.id,
                priority: hook.priority,
            });
        }
    }
}

fn match_numeral_measure(
    text: &str,
    segments: &[Segment],
    tags: &[PostTag],
    hook: &PostHook,
    out: &mut Vec<PostMatch>,
) {
    for i in 0..segments.len().saturating_sub(1) {
        if tags[i] == PostTag::Numeral
            && tags[i + 1] == PostTag::Measure
            && segments[i].byte_end == segments[i + 1].byte_start
            && &text[segments[i + 1].byte_start..segments[i + 1].byte_end] == "個"
        {
            out.push(PostMatch {
                token_start: i,
                token_end: i + 2,
                kind: SegKind::Num,
                rule_id: hook.id,
                priority: hook.priority,
            });
        }
    }
}

pub(crate) fn apply_post_hooks(
    text: &str,
    segments: &mut Vec<Segment>,
    tags: &[PostTag],
    trace: &mut Vec<RuleTrace>,
) {
    debug_assert_eq!(segments.len(), tags.len());
    let mut candidates = Vec::new();
    for hook in POST_HOOKS {
        (hook.matcher)(text, segments, tags, hook, &mut candidates);
    }
    candidates.sort_by(|a, b| {
        a.token_start
            .cmp(&b.token_start)
            .then_with(|| b.priority.cmp(&a.priority))
            .then_with(|| b.token_end.cmp(&a.token_end))
    });

    let mut selected = Vec::new();
    let mut token_cursor = 0usize;
    for candidate in candidates {
        if candidate.token_start < token_cursor {
            continue;
        }
        token_cursor = candidate.token_end;
        selected.push(candidate);
    }
    if selected.is_empty() {
        return;
    }

    let mut repaired = Vec::with_capacity(segments.len());
    let mut segment_cursor = 0usize;
    for edit in selected {
        repaired.extend_from_slice(&segments[segment_cursor..edit.token_start]);
        let first = segments[edit.token_start];
        let last = segments[edit.token_end - 1];
        repaired.push(Segment {
            byte_start: first.byte_start,
            byte_end: last.byte_end,
            kind: edit.kind,
        });
        trace.push(RuleTrace {
            rule_id: edit.rule_id,
            bucket: RuleBucket::Post,
            action: RuleAction::Merge,
            byte_start: first.byte_start,
            byte_end: last.byte_end,
        });
        segment_cursor = edit.token_end;
    }
    repaired.extend_from_slice(&segments[segment_cursor..]);
    *segments = repaired;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texts(text: &str) -> Vec<(&'static str, ChunkKind, String)> {
        collect_pre_matches(text)
            .into_iter()
            .map(|m| {
                (
                    m.rule_id,
                    m.kind,
                    text[m.byte_start..m.byte_end].to_string(),
                )
            })
            .collect()
    }

    #[test]
    fn model_identifiers_are_protected_without_swallowing_plain_hyphens() {
        let matched = texts("購買S-400與F-35，但A - B、-400及123-456不算型號");
        assert!(matched.contains(&(PRE_MODEL_IDENTIFIER, ChunkKind::Eng, "S-400".into())));
        assert!(matched.contains(&(PRE_MODEL_IDENTIFIER, ChunkKind::Eng, "F-35".into())));
        assert!(!matched.iter().any(|(_, _, text)| text == "A - B"));
        assert!(!matched.iter().any(|(_, _, text)| text == "-400"));
        assert!(!matched.iter().any(|(_, _, text)| text == "123-456"));
    }

    #[test]
    fn chinese_quantities_protect_the_numeral_measure_boundary() {
        let matched = texts("三隻貓、三個人、三人組");
        for expected in ["三隻", "三個", "三人"] {
            assert!(matched.contains(&(PRE_CHINESE_QUANTITY, ChunkKind::Num, expected.into())));
        }
        assert!(texts("第三人稱").is_empty());
    }

    #[test]
    fn chinese_time_protects_the_complete_period_expression() {
        let matched = texts("兩個月後、三年內、十天前");
        for expected in ["兩個月", "三年", "十天"] {
            assert!(matched.contains(&(PRE_CHINESE_TIME, ChunkKind::Time, expected.into())));
        }
        assert!(!texts("第二年")
            .iter()
            .any(|(rule, _, _)| *rule == PRE_CHINESE_TIME));
    }
    #[test]
    fn known_numeric_and_time_rules_keep_previous_boundaries() {
        let matched = texts("2019年7月花1,000元，市值1200億，漲57.16%");
        for expected in ["2019年", "7月"] {
            assert!(matched.contains(&(PRE_NUMERIC_TIME, ChunkKind::Time, expected.into())));
        }
        for expected in ["1,000", "1200億", "57.16%"] {
            assert!(matched.contains(&(PRE_NUMBER, ChunkKind::Num, expected.into())));
        }
    }

    #[test]
    fn numbers_embedded_in_alnum_stay_with_the_eng_run() {
        let matched = texts("abc123與123abc");
        assert!(!matched.iter().any(|(_, _, text)| text == "123"));
    }

    #[test]
    fn post_numeral_measure_hook_merges_only_adjacent_m_q() {
        let text = "一個人";
        let mut segments = vec![
            Segment {
                byte_start: 0,
                byte_end: 3,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 3,
                byte_end: 6,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 6,
                byte_end: 9,
                kind: SegKind::Oov,
            },
        ];
        let tags = [PostTag::Numeral, PostTag::Measure, PostTag::Other];
        let mut trace = Vec::new();
        apply_post_hooks(text, &mut segments, &tags, &mut trace);
        assert_eq!(
            segments,
            vec![
                Segment {
                    byte_start: 0,
                    byte_end: 6,
                    kind: SegKind::Num
                },
                Segment {
                    byte_start: 6,
                    byte_end: 9,
                    kind: SegKind::Oov
                },
            ]
        );
        assert_eq!(trace[0].rule_id, POST_NUMERAL_MEASURE);
    }

    #[test]
    fn post_numeral_measure_hook_does_not_merge_other_measure_words() {
        let text = "一美元";
        let mut segments = vec![
            Segment {
                byte_start: 0,
                byte_end: 3,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 3,
                byte_end: 9,
                kind: SegKind::Oov,
            },
        ];
        let original = segments.clone();
        let tags = [PostTag::Numeral, PostTag::Measure];
        let mut trace = Vec::new();
        apply_post_hooks(text, &mut segments, &tags, &mut trace);
        assert_eq!(segments, original);
        assert!(trace.is_empty());
    }

    #[test]
    fn chinese_numeral_time_merges_only_explicit_period_units() {
        let text = "第十季";
        let mut segments = vec![
            Segment {
                byte_start: 0,
                byte_end: 6,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 6,
                byte_end: 9,
                kind: SegKind::Oov,
            },
        ];
        let tags = [PostTag::Numeral, PostTag::Other];
        let mut trace = Vec::new();
        apply_post_hooks(text, &mut segments, &tags, &mut trace);
        assert_eq!(
            segments,
            vec![Segment {
                byte_start: 0,
                byte_end: 9,
                kind: SegKind::Time,
            }]
        );
        assert_eq!(trace[0].rule_id, POST_CHINESE_NUMERIC_TIME);

        for text in ["一起", "十分", "唯一年度"] {
            let split = text.chars().next().unwrap().len_utf8();
            let mut segments = vec![
                Segment {
                    byte_start: 0,
                    byte_end: split,
                    kind: SegKind::Oov,
                },
                Segment {
                    byte_start: split,
                    byte_end: text.len(),
                    kind: SegKind::Oov,
                },
            ];
            let original = segments.clone();
            let tags = [PostTag::Numeral, PostTag::Other];
            let mut trace = Vec::new();
            apply_post_hooks(text, &mut segments, &tags, &mut trace);
            assert_eq!(segments, original, "不應合併 {text}");
            assert!(trace.is_empty(), "不應觸發 {text}: {trace:?}");
        }
    }

    #[test]
    fn chinese_numeral_time_beats_shorter_measure_merge() {
        let text = "三個月";
        let mut segments = vec![
            Segment {
                byte_start: 0,
                byte_end: 3,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 3,
                byte_end: 6,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 6,
                byte_end: 9,
                kind: SegKind::Oov,
            },
        ];
        let tags = [PostTag::Numeral, PostTag::Measure, PostTag::Other];
        let mut trace = Vec::new();
        apply_post_hooks(text, &mut segments, &tags, &mut trace);
        assert_eq!(segments[0].kind, SegKind::Time);
        assert_eq!(segments[0].byte_end, text.len());
        assert_eq!(trace[0].rule_id, POST_CHINESE_NUMERIC_TIME);

        // 舊詞典可能把「三個」錯標成 a；完整表面形仍足以安全判定。
        let mut segments = vec![
            Segment {
                byte_start: 0,
                byte_end: 6,
                kind: SegKind::Oov,
            },
            Segment {
                byte_start: 6,
                byte_end: 9,
                kind: SegKind::Oov,
            },
        ];
        let tags = [PostTag::Other, PostTag::Other];
        let mut trace = Vec::new();
        apply_post_hooks(text, &mut segments, &tags, &mut trace);
        assert_eq!(segments[0].kind, SegKind::Time);
        assert_eq!(segments[0].byte_end, text.len());
        assert_eq!(trace[0].rule_id, POST_CHINESE_NUMERIC_TIME);
    }
}
