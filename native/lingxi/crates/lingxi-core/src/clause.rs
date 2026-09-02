//! 結構感知的中文子句切分。
//!
//! 子句界只在括號、引號、行內程式碼與 Markdown 粗體之外生效，避免把
//! 強調內容或函數／工具名稱拆散。輸出保留原始 UTF-8 byte offset。

use crate::sentence::{
    requires_following_clause, split_sentences_with_options, SentenceSplitOptions,
};

/// 原文中的一個子句及其 byte 區間。
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClauseSpan {
    pub text: String,
    pub byte_start: usize,
    pub byte_end: usize,
    /// 所屬句子的原文順序。
    pub sentence_index: usize,
    /// 全文中的子句順序。
    pub clause_index: usize,
    /// 此子句是否來自條列項目所在行。
    pub list_item: bool,
}

/// 子句切分設定。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ClauseSplitOptions {
    pub comma_boundary: bool,
    pub semicolon_boundary: bool,
    pub colon_boundary: bool,
}

impl Default for ClauseSplitOptions {
    fn default() -> Self {
        Self {
            comma_boundary: true,
            semicolon_boundary: true,
            colon_boundary: true,
        }
    }
}

/// 以預設規則切分句子內的子句。
pub fn split_clauses(text: &str) -> Vec<ClauseSpan> {
    split_clauses_with_options(text, ClauseSplitOptions::default())
}

/// 結構感知子句切分；不在括號、引號、反引號或 `**粗體**` 內切分。
pub fn split_clauses_with_options(text: &str, options: ClauseSplitOptions) -> Vec<ClauseSpan> {
    let sentences = split_sentences_with_options(
        text,
        SentenceSplitOptions {
            semicolon_boundary: false,
        },
    );
    let mut out = Vec::new();
    for sentence in sentences {
        split_sentence_clauses(text, &sentence, options, &mut out);
    }
    out
}

fn split_sentence_clauses(
    source: &str,
    sentence: &crate::sentence::SentenceSpan,
    options: ClauseSplitOptions,
    out: &mut Vec<ClauseSpan>,
) {
    let slice = &source[sentence.byte_start..sentence.byte_end];
    let chars: Vec<(usize, char)> = slice.char_indices().collect();
    let list_item = is_list_item_prefix(slice);
    let mut start = 0usize;
    let mut stack: Vec<char> = Vec::new();
    let mut markdown_bold = false;
    let mut inline_code = false;
    let mut i = 0usize;

    while i < chars.len() {
        let (byte, ch) = chars[i];
        let next = chars.get(i + 1).map(|(_, value)| *value);

        if ch == '*' && next == Some('*') && !inline_code {
            markdown_bold = !markdown_bold;
            i += 2;
            continue;
        }
        if ch == '`' && !markdown_bold {
            inline_code = !inline_code;
            i += 1;
            continue;
        }
        if markdown_bold || inline_code {
            i += 1;
            continue;
        }

        if let Some(closer) = matching_closer(ch) {
            stack.push(closer);
        } else if stack.last().copied() == Some(ch) {
            stack.pop();
        } else if is_symmetric_quote(ch) {
            if stack.last().copied() == Some(ch) {
                stack.pop();
            } else {
                stack.push(ch);
            }
        }

        if stack.is_empty()
            && is_clause_boundary(ch, options)
            && !is_numeric_separator(&chars, i, ch)
        {
            let end = byte + ch.len_utf8();
            if !requires_following_clause(&slice[start..end], ch) {
                push_trimmed_clause(source, sentence, start, end, list_item, out);
                start = end;
            }
        }
        i += 1;
    }
    push_trimmed_clause(source, sentence, start, slice.len(), list_item, out);
}

fn is_numeric_separator(chars: &[(usize, char)], index: usize, boundary: char) -> bool {
    boundary == ','
        && index > 0
        && chars
            .get(index - 1)
            .is_some_and(|(_, ch)| ch.is_ascii_digit())
        && chars
            .get(index + 1)
            .is_some_and(|(_, ch)| ch.is_ascii_digit())
}

fn matching_closer(ch: char) -> Option<char> {
    match ch {
        '(' => Some(')'),
        '（' => Some('）'),
        '[' => Some(']'),
        '【' => Some('】'),
        '〔' => Some('〕'),
        '{' => Some('}'),
        '「' => Some('」'),
        '『' => Some('』'),
        '《' => Some('》'),
        '〈' => Some('〉'),
        '“' => Some('”'),
        '‘' => Some('’'),
        _ => None,
    }
}

fn is_symmetric_quote(ch: char) -> bool {
    matches!(ch, '"' | '\'')
}

fn is_clause_boundary(ch: char, options: ClauseSplitOptions) -> bool {
    (options.comma_boundary && matches!(ch, '，' | ',' | '、'))
        || (options.semicolon_boundary && matches!(ch, '；' | ';'))
        || (options.colon_boundary && matches!(ch, '：' | ':'))
}

fn push_trimmed_clause(
    source: &str,
    sentence: &crate::sentence::SentenceSpan,
    local_start: usize,
    local_end: usize,
    list_item: bool,
    out: &mut Vec<ClauseSpan>,
) {
    if local_start >= local_end {
        return;
    }
    let absolute_start = sentence.byte_start + local_start;
    let absolute_end = sentence.byte_start + local_end;
    let slice = &source[absolute_start..absolute_end];
    let leading = slice.len() - slice.trim_start().len();
    let trailing = slice.len() - slice.trim_end().len();
    let byte_start = absolute_start + leading;
    let byte_end = absolute_end - trailing;
    if byte_start >= byte_end {
        return;
    }
    out.push(ClauseSpan {
        text: source[byte_start..byte_end].to_string(),
        byte_start,
        byte_end,
        sentence_index: sentence.sentence_index,
        clause_index: out.len(),
        list_item,
    });
}

fn is_list_item_prefix(text: &str) -> bool {
    let text = text.trim_start();
    if ["- ", "* ", "+ ", "•", "‧", "▪", "◦"]
        .iter()
        .any(|prefix| text.starts_with(prefix))
    {
        return true;
    }
    let prefix = text
        .chars()
        .take_while(|ch| ch.is_ascii_digit() || "一二三四五六七八九十".contains(*ch))
        .collect::<String>();
    !prefix.is_empty() && text[prefix.len()..].starts_with(['.', ')', '）', '、'])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_clauses_and_preserves_offsets() {
        let text = "系統已上線，但仍不能刪除資料；請先備份。";
        let clauses = split_clauses(text);
        let values: Vec<&str> = clauses.iter().map(|clause| clause.text.as_str()).collect();
        assert_eq!(values, ["系統已上線，", "但仍不能刪除資料；", "請先備份。"]);
        for clause in clauses {
            assert_eq!(&text[clause.byte_start..clause.byte_end], clause.text);
        }
    }

    #[test]
    fn protects_emphasis_quotes_parentheses_and_object_names() {
        let text = "保留 **不得刪除，務必備份**，並呼叫 `tools.run(a, b)`；完成。";
        let clauses = split_clauses(text);
        let values: Vec<&str> = clauses.iter().map(|clause| clause.text.as_str()).collect();
        assert_eq!(
            values,
            [
                "保留 **不得刪除，務必備份**，",
                "並呼叫 `tools.run(a, b)`；",
                "完成。"
            ]
        );
    }

    #[test]
    fn keeps_list_items_as_independent_sentence_scopes() {
        let text = "- 第一項：不得覆寫\n- 第二項：呼叫 build()";
        let clauses = split_clauses(text);
        assert_eq!(clauses.len(), 4);
        assert_eq!(clauses[0].sentence_index, 0);
        assert_eq!(clauses[2].sentence_index, 1);
        assert!(clauses.iter().all(|clause| clause.list_item));
    }

    #[test]
    fn keeps_condition_and_consequence_in_the_same_clause() {
        let text = "每天久坐超過10小時，慢性腎臟病風險會明顯上升。";
        let clauses = split_clauses(text);
        assert_eq!(clauses.len(), 1);
        assert_eq!(clauses[0].text, text);

        let correlative = "不只每天工作超過10個小時，還幾乎沒有起身活動。";
        let clauses = split_clauses(correlative);
        assert_eq!(clauses.len(), 1);
        assert_eq!(clauses[0].text, correlative);

        let embedded_correlative = "坐太久不只會傷腎，嚴重時真的可能會致命。";
        let clauses = split_clauses(embedded_correlative);
        assert_eq!(clauses.len(), 1);
        assert_eq!(clauses[0].text, embedded_correlative);
    }

    #[test]
    fn keeps_definitions_and_ordered_actions_complete() {
        let definition =
            "Fear Of Missing Out（FOMO）在投資市場，是指看到別人賺錢，自己沒跟上而感到焦慮與恐慌。";
        assert_eq!(split_clauses(definition)[0].text, definition);
        assert_eq!(split_clauses(definition).len(), 1);

        let actions = "團隊決定先擴充容量，再修正記憶體洩漏問題，預計可恢復服務穩定性。";
        assert_eq!(split_clauses(actions)[0].text, actions);
        assert_eq!(split_clauses(actions).len(), 1);
    }

    #[test]
    fn does_not_split_thousands_separators() {
        let text = "樣本共1,024人，風險增加15%。";
        let clauses = split_clauses(text);
        assert_eq!(clauses.len(), 2);
        assert_eq!(clauses[0].text, "樣本共1,024人，");
    }
}
