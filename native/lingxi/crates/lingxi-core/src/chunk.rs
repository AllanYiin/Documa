//! 預切塊：把輸入依字元類別與規則切成「待分詞的中文塊」與「直接定案的詞段」。
//!
//! 規則刻意保守：只有 rules registry 找到的受保護 span 與一般英數串直接成段；
//! 中文數字與量詞序列留在 Han 塊內，由 DAG+HMM 依詞典機率決定，
//! 避免規則搶走「一起」「十分」等真詞。
//! 可增減的特殊規則集中在 rules 模組，不在這個掃描器內逐條堆疊。

use crate::rules::{self, RuleAction, RuleBucket, RuleTrace};

/// 塊種類：Han 需進 DAG 分詞，其餘直接定案。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChunkKind {
    /// 連續中文（含注音、〇、日文假名），交給 DAG+DP。
    Han,
    Url,
    Email,
    Eng,
    Num,
    Time,
    Punct,
    Space,
    Other,
}

/// 一個塊：byte 區間 + 種類。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Chunk {
    pub byte_start: usize,
    pub byte_end: usize,
    pub kind: ChunkKind,
}

/// 字元大類（走訪期間用）。
#[derive(Clone, Copy, PartialEq, Eq)]
enum CharClass {
    /// 中文漢字 / 注音 / 〇 / 日文假名（舊版 RegexCut 的可切集合）。
    Han,
    /// ASCII 英數與 &/_（舊版 RegexEngNum 集合）。
    Alnum,
    Space,
    Punct,
    Other,
}

fn char_class(c: char) -> CharClass {
    match c {
        '\u{4E00}'..='\u{9FFF}'          // CJK 統一漢字
        | '\u{3105}'..='\u{3129}'        // 注音符號
        | '\u{3007}'                     // 〇
        | '\u{3040}'..='\u{30FF}'        // 日文平假名/片假名
        | '\u{31F0}'..='\u{31FF}' => CharClass::Han,
        'a'..='z' | 'A'..='Z' | '0'..='9' | '&' | '/' | '_' => CharClass::Alnum,
        c if c.is_whitespace() => CharClass::Space,
        // 常用標點：ASCII 標點（扣除已列入 Alnum 者）+ CJK 標點 + 全形符號 + 一般標點區。
        '!'..='~' => CharClass::Punct, // 剩餘 ASCII 可見字元皆視為標點
        '\u{3000}'..='\u{303F}'          // CJK 符號與標點（。、「」…）
        | '\u{FF00}'..='\u{FFEF}'        // 全形形式（！？，：…）
        | '\u{2000}'..='\u{206F}' => CharClass::Punct,
        _ => CharClass::Other,
    }
}

/// 是否屬於目前分詞管線視為 Han 的字元。
///
/// 除了預切塊本身，主管線也用它辨識跨 ASCII/Han 的詞典詞。
pub(crate) fn is_han_char(c: char) -> bool {
    char_class(c) == CharClass::Han
}

/// 對整段文字做預切塊。輸入須為正規化後文字；`out` 依序收到不重疊、
/// 覆蓋全文的塊。
pub fn split(text: &str, out: &mut Vec<Chunk>) {
    split_with_trace(text, out, &mut Vec::new());
}

pub(crate) fn split_with_trace(text: &str, out: &mut Vec<Chunk>, trace: &mut Vec<RuleTrace>) {
    let protected = rules::collect_pre_matches(text);
    let mut cursor = 0usize;
    for protected_match in protected {
        if cursor < protected_match.byte_start {
            scan_plain(&text[cursor..protected_match.byte_start], cursor, out);
        }
        out.push(Chunk {
            byte_start: protected_match.byte_start,
            byte_end: protected_match.byte_end,
            kind: protected_match.kind,
        });
        trace.push(RuleTrace {
            rule_id: protected_match.rule_id,
            bucket: RuleBucket::Pre,
            action: RuleAction::Protect,
            byte_start: protected_match.byte_start,
            byte_end: protected_match.byte_end,
        });
        cursor = protected_match.byte_end;
    }
    if cursor < text.len() {
        scan_plain(&text[cursor..], cursor, out);
    }
}

/// 無 URL/email 的純文字走訪：依字元類別分段。
fn scan_plain(text: &str, base: usize, out: &mut Vec<Chunk>) {
    let chars: Vec<(usize, char)> = text.char_indices().collect();
    let n = chars.len();
    // 第 i 個字元的結束 byte 位置。
    let end_of = |i: usize| {
        if i + 1 < n {
            chars[i + 1].0
        } else {
            text.len()
        }
    };

    let mut i = 0usize;
    while i < n {
        let (start_byte, c) = chars[i];
        let class = char_class(c);
        match class {
            CharClass::Alnum => {
                // 吃掉整段英數串。
                let mut j = i;
                let mut all_digits = true;
                while j < n && char_class(chars[j].1) == CharClass::Alnum {
                    all_digits &= chars[j].1.is_ascii_digit();
                    j += 1;
                }
                let mut kind = ChunkKind::Eng;
                if all_digits {
                    kind = ChunkKind::Num;
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind,
                });
                i = j;
            }
            CharClass::Han => {
                let mut j = i;
                while j < n && char_class(chars[j].1) == CharClass::Han {
                    j += 1;
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind: ChunkKind::Han,
                });
                i = j;
            }
            CharClass::Space | CharClass::Other => {
                // 空白與其他符號：同類連續合併為一段。
                let mut j = i;
                while j < n && char_class(chars[j].1) == class {
                    j += 1;
                }
                let kind = if class == CharClass::Space {
                    ChunkKind::Space
                } else {
                    ChunkKind::Other
                };
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind,
                });
                i = j;
            }
            CharClass::Punct => {
                // 標點：僅相同字元的連續（如 「！！！」「……」）合併。
                let mut j = i;
                while j < n && chars[j].1 == c {
                    j += 1;
                }
                out.push(Chunk {
                    byte_start: base + start_byte,
                    byte_end: base + end_of(j - 1),
                    kind: ChunkKind::Punct,
                });
                i = j;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn kinds_and_texts(text: &str) -> Vec<(ChunkKind, String)> {
        let mut chunks = Vec::new();
        split(text, &mut chunks);
        chunks
            .iter()
            .map(|c| (c.kind, text[c.byte_start..c.byte_end].to_string()))
            .collect()
    }

    #[test]
    fn extracts_url_and_email() {
        let r = kinds_and_texts("請寄到test@example.com或上https://www.ptt.cc/bbs查詢");
        assert!(
            r.contains(&(ChunkKind::Email, "test@example.com".into())),
            "{r:?}"
        );
        assert!(
            r.contains(&(ChunkKind::Url, "https://www.ptt.cc/bbs".into())),
            "{r:?}"
        );
    }

    #[test]
    fn supports_long_modern_tlds() {
        let r = kinds_and_texts("寄到a@example.technology或看https://example.technology/path");
        assert!(
            r.contains(&(ChunkKind::Email, "a@example.technology".into())),
            "{r:?}"
        );
        assert!(
            r.contains(&(ChunkKind::Url, "https://example.technology/path".into())),
            "{r:?}"
        );
    }

    #[test]
    fn digits_with_time_unit_and_percent_are_kept_whole() {
        let r = kinds_and_texts("2014年開始的3個月內漲了1,000點，15%又3.5%，全形70％");
        assert!(r.contains(&(ChunkKind::Time, "2014年".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Time, "3個月".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "1,000".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "15%".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "3.5%".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Num, "70％".into())), "{r:?}");
    }

    #[test]
    fn ordinal_period_stops_han_words_from_crossing_its_boundary() {
        let r = kinds_and_texts("特斯拉第2季資本支出");
        assert_eq!(
            r,
            vec![
                (ChunkKind::Han, "特斯拉".into()),
                (ChunkKind::Time, "第2季".into()),
                (ChunkKind::Han, "資本支出".into()),
            ]
        );
    }

    #[test]
    fn ascii_digits_absorb_chinese_magnitude_but_not_currency() {
        let r = kinds_and_texts("1200億美元");
        assert_eq!(
            r,
            vec![
                (ChunkKind::Num, "1200億".into()),
                (ChunkKind::Han, "美元".into()),
            ]
        );
    }

    #[test]
    fn han_runs_stay_whole_and_cover_all() {
        let text = "台北的天氣真好！！！hello world";
        let mut chunks = Vec::new();
        split(text, &mut chunks);
        // 全覆蓋且不重疊。
        let mut cursor = 0;
        for c in &chunks {
            assert_eq!(c.byte_start, cursor);
            cursor = c.byte_end;
        }
        assert_eq!(cursor, text.len());
        let r = kinds_and_texts(text);
        assert!(
            r.contains(&(ChunkKind::Han, "台北的天氣真好".into())),
            "{r:?}"
        );
        assert!(r.contains(&(ChunkKind::Punct, "！！！".into())), "{r:?}");
        assert!(r.contains(&(ChunkKind::Eng, "hello".into())), "{r:?}");
    }

    #[test]
    fn chinese_numerals_stay_in_han_chunk() {
        // 中文數字不強制成段，交給詞典（「一起」不能被當數字切走）。
        let r = kinds_and_texts("我們一起走");
        assert_eq!(r, vec![(ChunkKind::Han, "我們一起走".to_string())]);
    }
}
