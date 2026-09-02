//! 中文句界切分。輸出保留原始 UTF-8 byte offset，不改寫文字。

/// 原文中的一句及其 byte 區間。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SentenceSpan {
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
    pub sentence_index: usize,
}

/// 句界切分設定。
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct SentenceSplitOptions {
    /// 是否將全形／半形分號視為句界。
    pub semicolon_boundary: bool,
}

/// 依中文標點與換行切句，保留終止標點並略過句子外圍空白。
pub fn split_sentences(text: &str) -> Vec<SentenceSpan> {
    split_sentences_with_options(text, SentenceSplitOptions::default())
}

/// 可設定分號行為的中文句界切分。
pub fn split_sentences_with_options(
    text: &str,
    options: SentenceSplitOptions,
) -> Vec<SentenceSpan> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut pending_terminal = false;

    for (byte, ch) in text.char_indices() {
        if ch == '\n' || ch == '\r' {
            let candidate = &text[start..byte];
            if candidate
                .trim_end()
                .chars()
                .last()
                .is_some_and(|boundary| requires_following_clause(candidate, boundary))
            {
                continue;
            }
            push_trimmed(text, start, byte, &mut out);
            start = byte + ch.len_utf8();
            pending_terminal = false;
            continue;
        }

        if pending_terminal && !is_terminal(ch, options) && !is_closer(ch) {
            push_trimmed(text, start, byte, &mut out);
            start = byte;
            pending_terminal = false;
        }

        if is_terminal(ch, options) {
            pending_terminal = true;
        }
    }
    push_trimmed(text, start, text.len(), &mut out);
    out
}

/// 判斷逗號前的內容是否只是條件／關聯前件，不能獨立成為完整子句。
pub(crate) fn requires_following_clause(candidate: &str, boundary: char) -> bool {
    if !matches!(boundary, '，' | ',') {
        return false;
    }
    let content = candidate
        .trim()
        .trim_end_matches(['，', ','])
        .trim_start_matches(|ch: char| ch.is_ascii_digit() || matches!(ch, '.' | ')' | '）' | '、'))
        .trim();
    let correlative = ["不只", "不僅", "不但"]
        .iter()
        .any(|marker| content.contains(marker));
    let dependent_prefix = [
        "如果", "若", "倘若", "只要", "除非", "一旦", "當", "雖然", "儘管", "即使", "由於", "因為",
        "除了",
    ]
    .iter()
    .any(|prefix| content.starts_with(prefix));
    let quantitative_condition = ["超過", "高於", "低於", "少於", "未滿", "達到", "多於"]
        .iter()
        .any(|marker| content.contains(marker))
        && [
            "毫秒", "秒", "分鐘", "小時", "天", "日", "週", "周", "月", "季", "年", "%", "％",
            "元", "萬元", "億元", "公里", "公尺", "公分", "公斤", "GB", "MB", "TB",
        ]
        .iter()
        .any(|unit| content.ends_with(unit));
    let definition = contains_parenthesized_acronym(content)
        || ["是指", "意指", "指的是", "定義為", "也就是", "換言之"]
            .iter()
            .any(|marker| content.contains(marker));
    let ordered_sequence = content.match_indices('先').any(|(index, _)| {
        !content[index + '先'.len_utf8()..].starts_with(['生', '進', '前', '祖'])
    });
    correlative || dependent_prefix || quantitative_condition || definition || ordered_sequence
}

fn contains_parenthesized_acronym(text: &str) -> bool {
    [('（', '）'), ('(', ')')].into_iter().any(|(open, close)| {
        let Some(start) = text.rfind(open) else {
            return false;
        };
        let Some(end_offset) = text[start + open.len_utf8()..].find(close) else {
            return false;
        };
        let value = &text[start + open.len_utf8()..start + open.len_utf8() + end_offset];
        let uppercase = value.chars().filter(|ch| ch.is_ascii_uppercase()).count();
        uppercase >= 2
            && value.chars().count() <= 12
            && value.chars().all(|ch| {
                ch.is_ascii_uppercase()
                    || ch.is_ascii_digit()
                    || matches!(ch, '&' | '-' | '.' | '/')
            })
    })
}

fn is_terminal(ch: char, options: SentenceSplitOptions) -> bool {
    matches!(ch, '。' | '！' | '？' | '!' | '?' | '…')
        || (options.semicolon_boundary && matches!(ch, '；' | ';'))
}

fn is_closer(ch: char) -> bool {
    matches!(
        ch,
        '"' | '\'' | '”' | '’' | '」' | '』' | '》' | '〉' | '）' | ')' | '】' | ']' | '〕'
    )
}

fn push_trimmed(text: &str, start: usize, end: usize, out: &mut Vec<SentenceSpan>) {
    if start >= end {
        return;
    }
    let slice = &text[start..end];
    let leading = slice.len() - slice.trim_start().len();
    let trailing = slice.len() - slice.trim_end().len();
    let byte_start = start + leading;
    let byte_end = end - trailing;
    if byte_start >= byte_end {
        return;
    }
    out.push(SentenceSpan {
        text: text[byte_start..byte_end].to_string(),
        byte_start,
        byte_end,
        sentence_index: out.len(),
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_chinese_punctuation_and_preserves_offsets() {
        let text = "  第一句。\n第二句！？』 第三句";
        let spans = split_sentences(text);
        let values: Vec<&str> = spans.iter().map(|span| span.text.as_str()).collect();
        assert_eq!(values, ["第一句。", "第二句！？』", "第三句"]);
        for span in &spans {
            assert_eq!(&text[span.byte_start..span.byte_end], span.text);
        }
    }

    #[test]
    fn semicolon_is_optional_and_decimal_period_does_not_split() {
        let text = "成長3.5%；仍需觀察；最後結論。";
        assert_eq!(split_sentences(text).len(), 1);
        assert_eq!(
            split_sentences_with_options(
                text,
                SentenceSplitOptions {
                    semicolon_boundary: true,
                },
            )
            .len(),
            3
        );
    }

    #[test]
    fn keeps_condition_line_with_its_following_consequence() {
        let text = "每天久坐超過10小時，\n慢性腎臟病風險會明顯上升。";
        let sentences = split_sentences(text);
        assert_eq!(sentences.len(), 1);
        assert_eq!(sentences[0].text, text);
    }

    #[test]
    fn recognizes_definition_and_ordered_sequence_as_dependent_clauses() {
        assert!(requires_following_clause(
            "Fear Of Missing Out（FOMO）在投資市場，",
            '，'
        ));
        assert!(requires_following_clause("FOMO 是指害怕錯過機會，", '，'));
        assert!(requires_following_clause("團隊決定先擴充容量，", '，'));
        assert!(!requires_following_clause("先進製程已經量產，", '，'));
    }
}
