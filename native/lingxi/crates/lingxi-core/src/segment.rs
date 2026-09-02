//! 分詞管線各階段共用的詞段型別。

/// 詞段種類：來源決定後續處理與詞性推導方式。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SegKind {
    /// 詞典詞，攜帶詞條 id（詞性直接查詞典）。
    Dict(u32),
    /// 未登入詞（單字或 HMM 合併後的多字新詞），詞性由 POS Viterbi 決定。
    Oov,
    /// 網址。
    Url,
    /// Email。
    Email,
    /// 含英文字母的英數串。
    Eng,
    /// 數字（含小數點／千分位、中文數量級與尾隨百分號）。
    Num,
    /// 數字時間詞或序數期間詞（如 2014年、12月、第2季）。
    Time,
    /// 標點符號（同字元連續者合併為一段）。
    Punct,
    /// 空白（連續合併）。
    Space,
    /// 其他（emoji、罕見符號等，連續合併）。
    Other,
}

/// 一個詞段：原始輸入字串的 byte 區間 + 種類。
/// 只存區間不存字串副本，呼叫端以切片取詞（零拷貝）。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Segment {
    pub byte_start: usize,
    pub byte_end: usize,
    pub kind: SegKind,
}
