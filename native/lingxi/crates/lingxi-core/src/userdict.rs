//! 自訂詞典：使用者詞條的解析。
//!
//! 主詞典是離線轉換的靜態資產；自訂詞典在 `Segmenter` 建構時一併載入，
//! 於 `Dict` 內建成第二個 AC 自動機（見 dict.rs），與主詞典共同參與 DAG 建邊。
//! 語意為覆寫／新增多字詞；與主詞典合併後重新正規化全部詞頻。
//!
//! 文字格式與 jieba 使用者詞典相容，每行一條：
//!
//! ```text
//! 詞 頻率 [詞性]
//! ```
//!
//! 頻率必須是有限正數；缺值、零值、負值與非有限值會在載入時報錯；
//! 詞性省略時預設 "n"。`#` 開頭為註解行。

/// 一條自訂詞條。
#[derive(Clone, Debug)]
pub struct UserDictEntry {
    pub word: String,
    /// 原始頻率；None 會在載入時報錯。
    pub freq: Option<f64>,
    /// 詞性名稱；None = "n"。
    pub tag: Option<String>,
}

/// 解析 jieba 格式的使用者詞典文字。
///
/// 容錯規則：空行與 `#` 註解行略過；第二欄可解析為數字時視為頻率，
/// 否則暫存為詞性；載入時仍因缺少頻率而明確報錯。
pub fn parse_user_dict(text: &str) -> Vec<UserDictEntry> {
    let mut entries = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split_whitespace();
        let Some(word) = fields.next() else { continue };
        let mut freq = None;
        let mut tag = None;
        if let Some(second) = fields.next() {
            match second.parse::<f64>() {
                Ok(f) => {
                    freq = Some(f);
                    tag = fields.next().map(str::to_string);
                }
                Err(_) => tag = Some(second.to_string()),
            }
        }
        entries.push(UserDictEntry {
            word: word.to_string(),
            freq,
            tag,
        });
    }
    entries
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_all_field_combinations() {
        let text = "# 註解\n板南線 Nb\n鹽酥雞 300 Na\n柯文哲\n\n  高頻詞  9999  \n";
        let e = parse_user_dict(text);
        assert_eq!(e.len(), 4);
        assert_eq!(
            (e[0].word.as_str(), e[0].freq, e[0].tag.as_deref()),
            ("板南線", None, Some("Nb"))
        );
        assert_eq!(
            (e[1].word.as_str(), e[1].freq, e[1].tag.as_deref()),
            ("鹽酥雞", Some(300.0), Some("Na"))
        );
        assert_eq!(
            (e[2].word.as_str(), e[2].freq, e[2].tag.as_deref()),
            ("柯文哲", None, None)
        );
        assert_eq!(
            (e[3].word.as_str(), e[3].freq, e[3].tag.as_deref()),
            ("高頻詞", Some(9999.0), None)
        );
    }
}
