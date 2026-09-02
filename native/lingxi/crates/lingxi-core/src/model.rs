//! 模型資料結構與二進位資產（asset）編解碼。
//!
//! 所有模型由 tools/lingxi-convert 離線從舊版 JSON 轉換；執行期只做
//! postcard 反序列化，零 JSON 解析。機率一律為 f32 log 域。
//!
//! 資產檔格式：4 bytes magic + u16 LE version + u64 LE xxh3(payload) + payload。
//! 通用資產維持 LXA2/version 2；量化 POS 使用 LXA3/version 3。

use serde::{de::DeserializeOwned, Deserialize, Serialize};

/// log 機率下限哨兵（對應舊版 C# 的 -3.14E+100），語意為「不可能」。
/// 不用 f32::NEG_INFINITY 是為了讓加法不產生 NaN（-inf + inf 等病態情況）。
pub const MIN_LOG: f32 = -1.0e30;

/// 資產檔頭 magic。
pub const ASSET_MAGIC: [u8; 4] = *b"LXA2";
/// i16 定點量化 POS asset 的 magic。
pub const QUANTIZED_POS_ASSET_MAGIC: [u8; 4] = *b"LXA3";
/// 資產格式版本，結構有不相容變更時遞增。
pub const ASSET_VERSION: u16 = 2;
/// POS 機率陣列改以 i16 定點儲存的資產版本。
pub const QUANTIZED_POS_ASSET_VERSION: u16 = 3;

/// BMES 狀態索引固定順序：B=0, M=1, E=2, S=3。
pub const STATE_B: usize = 0;
pub const STATE_M: usize = 1;
pub const STATE_E: usize = 2;
pub const STATE_S: usize = 3;

// ---------------------------------------------------------------------------
// 共用：字元索引表
// ---------------------------------------------------------------------------

/// 排序後的字元表；以二分搜尋將 char 映射為 dense 陣列索引。
/// 取代舊版兩層 HashMap<char, ...>，查詢後直接讀取連續記憶體列。
#[derive(Serialize, Deserialize)]
pub struct CharTable {
    /// 已排序、去重的字元集合。
    pub chars: Vec<char>,
}

impl CharTable {
    /// 回傳字元在 dense 陣列中的列索引；不在表中回傳 None。
    #[inline]
    pub fn index_of(&self, c: char) -> Option<usize> {
        self.chars.binary_search(&c).ok()
    }
}

// ---------------------------------------------------------------------------
// 詞典模型
// ---------------------------------------------------------------------------

/// 詞典模型：daachorse AC 自動機 + SoA 詞條表。
///
/// 自動機一次 `find_overlapping_iter` 掃出句中所有詞典命中，
/// match value 即詞條 id，用以索引下方平行陣列。
#[derive(Serialize, Deserialize)]
pub struct DictModel {
    /// daachorse CharwiseDoubleArrayAhoCorasick<u32> 的序列化 bytes。
    pub automaton_bytes: Vec<u8>,
    /// 詞性標籤名稱表（資料驅動，非硬編 enum）；詞條 tag 為此表索引。
    pub tag_names: Vec<String>,
    /// 每個詞條的詞性標籤 id。
    pub word_tags: Vec<u8>,
    /// 每個詞條的 ln(freq / total)。
    pub word_log_probs: Vec<f32>,
    /// 每個詞條的字元數（DP 時免重算）。
    pub word_char_lens: Vec<u8>,
    /// ln(total_freq)：僅計入正頻率的多字詞條。
    pub total_log: f32,
    /// 異體字正規化映射（如 体→體、臺→台）；僅含 UTF-8 等長對，維持 byte offset 不變。
    pub variant_map: Vec<(char, char)>,
}

// ---------------------------------------------------------------------------
// BMES 分詞 HMM（含二階）
// ---------------------------------------------------------------------------

/// 未登入詞切分用的 BMES HMM，一階與二階矩陣皆含。
/// 解碼採二階 Viterbi：複合狀態 (prev, cur) 共 16 態的標準 DP。
#[derive(Serialize, Deserialize)]
pub struct BmesModel {
    /// 發射機率共用的完整訓練字元表。
    pub chars: CharTable,
    /// 初始機率 log P(s0)。
    pub start: [f32; 4],
    /// 一階轉移 log P(cur | prev)，索引 [prev][cur]。
    pub trans1: [[f32; 4]; 4],
    /// 二階轉移 log P(cur | prev2, prev1)，索引 [prev2][prev1][cur]。
    pub trans2: [[[f32; 4]; 4]; 4],
    /// 一階發射 log P(char | s)，每字元一列 [s]。
    pub emit1: Vec<[f32; 4]>,
    /// 二階發射 log P(char | prev, cur)，每字元一列 [prev][cur]。
    pub emit2: Vec<[[f32; 4]; 4]>,
    /// 一階發射的 `<UNK>` 平滑機率，索引 [state]。
    pub emit1_unknown: [f32; 4],
    /// 二階發射的 `<UNK>` 平滑機率，索引 [prev][cur]。
    pub emit2_unknown: [[f32; 4]; 4],
}

// ---------------------------------------------------------------------------
// POS 標註 HMM（joint state：BMES × 詞性）
// ---------------------------------------------------------------------------

/// OOV 詞性標註用的 joint-state HMM（狀態如 "B-a"、"E-nr"）。
/// 詞典詞的詞性直接查 DictModel，不經過此模型。
#[derive(Serialize, Deserialize)]
pub struct PosModel {
    /// 狀態名稱（如 "B-a"），索引即 state id。
    pub state_names: Vec<String>,
    /// 每個狀態的 BMES 部分（0..=3，對應 STATE_*）。
    pub state_bmes: Vec<u8>,
    /// 每個狀態的詞性部分，為 tag_names 索引。
    pub state_tags: Vec<u8>,
    /// POS 詞性名稱表（與 DictModel.tag_names 獨立，以字串為對齊介面）。
    pub tag_names: Vec<String>,
    /// 初始機率，長度 = 狀態數。
    pub start: Vec<f32>,
    /// 一階轉移機率 dense [S*S]，索引 prev * S + cur。
    pub trans1: Vec<f32>,
    /// 二階轉移機率 dense [S*S*S]，索引 (prev2 * S + prev1) * S + cur。
    pub trans2: Vec<f32>,
    /// 發射 CSR 的字元表。
    pub chars: CharTable,
    /// CSR 列偏移，長度 = chars.len() + 1。
    pub emit_offsets: Vec<u32>,
    /// CSR：允許狀態 id（同一字元列內遞增排序）。
    pub emit_states: Vec<u16>,
    /// CSR：對應的發射 log 機率。
    pub emit_logps: Vec<f32>,
    /// 每個 joint-state 的 `<UNK>` 發射平滑機率。
    pub emit_unknown: Vec<f32>,
    /// 詞彙 POS 自動機；pattern value 為詞彙列 id。
    pub lexicon_automaton_bytes: Vec<u8>,
    /// 詞彙列 CSR 偏移，長度 = 詞彙數 + 1。
    pub lexicon_offsets: Vec<u32>,
    /// 詞彙列中的 POS tag id。
    pub lexicon_tags: Vec<u8>,
    /// 完整 log P(tag | word)。
    pub lexicon_logps: Vec<f32>,
}

impl PosModel {
    /// 取得某字元的（允許狀態, 發射機率）連續切片；字元不在表中回傳 None
    /// （呼叫端此時應退化為「所有狀態皆可、發射為地板值」）。
    #[inline]
    pub fn emit_row(&self, c: char) -> Option<(&[u16], &[f32])> {
        let i = self.chars.index_of(c)?;
        let lo = self.emit_offsets[i] as usize;
        let hi = self.emit_offsets[i + 1] as usize;
        Some((&self.emit_states[lo..hi], &self.emit_logps[lo..hi]))
    }
}

const QUANTIZED_MIN_LOG: i16 = i16::MIN;
const QUANTIZED_FINITE_MIN: i16 = i16::MIN + 1;

/// 一張 log-probability 表的 i16 定點表示。數值採 little-endian bytes，避免
/// postcard 對 signed integer 使用 varint 後失去固定 2-byte 的體積優勢。
#[derive(Serialize, Deserialize)]
struct QuantizedLogTable {
    scale: f32,
    values_le: Vec<u8>,
}

impl QuantizedLogTable {
    fn encode(values: &[f32]) -> Self {
        let max_abs = values
            .iter()
            .copied()
            .filter(|value| value.is_finite() && *value > MIN_LOG / 2.0)
            .map(f32::abs)
            .fold(0.0f32, f32::max);
        let scale = if max_abs > 0.0 {
            max_abs / QUANTIZED_FINITE_MIN.unsigned_abs() as f32
        } else {
            1.0
        };
        let mut values_le = Vec::with_capacity(values.len() * 2);
        for value in values {
            let quantized = if !value.is_finite() || *value <= MIN_LOG / 2.0 {
                QUANTIZED_MIN_LOG
            } else {
                (*value / scale)
                    .round()
                    .clamp(QUANTIZED_FINITE_MIN as f32, i16::MAX as f32) as i16
            };
            values_le.extend_from_slice(&quantized.to_le_bytes());
        }
        Self { scale, values_le }
    }

    fn decode(self, label: &str) -> Result<Vec<f32>, AssetError> {
        if !self.scale.is_finite() || self.scale <= 0.0 {
            return Err(AssetError::InvalidQuantized(format!(
                "{label} scale 必須為正有限值"
            )));
        }
        if !self.values_le.len().is_multiple_of(2) {
            return Err(AssetError::InvalidQuantized(format!(
                "{label} byte 長度不是 2 的倍數"
            )));
        }
        Ok(self
            .values_le
            .chunks_exact(2)
            .map(|bytes| {
                let value = i16::from_le_bytes([bytes[0], bytes[1]]);
                if value == QUANTIZED_MIN_LOG {
                    MIN_LOG
                } else {
                    value as f32 * self.scale
                }
            })
            .collect())
    }
}

#[derive(Serialize, Deserialize)]
struct QuantizedPosModel {
    state_names: Vec<String>,
    state_bmes: Vec<u8>,
    state_tags: Vec<u8>,
    tag_names: Vec<String>,
    start: QuantizedLogTable,
    trans1: QuantizedLogTable,
    trans2: QuantizedLogTable,
    chars: CharTable,
    emit_offsets: Vec<u32>,
    emit_states: Vec<u16>,
    emit_logps: QuantizedLogTable,
    emit_unknown: QuantizedLogTable,
    lexicon_automaton_bytes: Vec<u8>,
    lexicon_offsets: Vec<u32>,
    lexicon_tags: Vec<u8>,
    lexicon_logps: QuantizedLogTable,
}

impl QuantizedPosModel {
    fn from_model(model: &PosModel) -> Self {
        Self {
            state_names: model.state_names.clone(),
            state_bmes: model.state_bmes.clone(),
            state_tags: model.state_tags.clone(),
            tag_names: model.tag_names.clone(),
            start: QuantizedLogTable::encode(&model.start),
            trans1: QuantizedLogTable::encode(&model.trans1),
            trans2: QuantizedLogTable::encode(&model.trans2),
            chars: CharTable {
                chars: model.chars.chars.clone(),
            },
            emit_offsets: model.emit_offsets.clone(),
            emit_states: model.emit_states.clone(),
            emit_logps: QuantizedLogTable::encode(&model.emit_logps),
            emit_unknown: QuantizedLogTable::encode(&model.emit_unknown),
            lexicon_automaton_bytes: model.lexicon_automaton_bytes.clone(),
            lexicon_offsets: model.lexicon_offsets.clone(),
            lexicon_tags: model.lexicon_tags.clone(),
            lexicon_logps: QuantizedLogTable::encode(&model.lexicon_logps),
        }
    }

    fn into_model(self) -> Result<PosModel, AssetError> {
        Ok(PosModel {
            state_names: self.state_names,
            state_bmes: self.state_bmes,
            state_tags: self.state_tags,
            tag_names: self.tag_names,
            start: self.start.decode("start")?,
            trans1: self.trans1.decode("trans1")?,
            trans2: self.trans2.decode("trans2")?,
            chars: self.chars,
            emit_offsets: self.emit_offsets,
            emit_states: self.emit_states,
            emit_logps: self.emit_logps.decode("emit_logps")?,
            emit_unknown: self.emit_unknown.decode("emit_unknown")?,
            lexicon_automaton_bytes: self.lexicon_automaton_bytes,
            lexicon_offsets: self.lexicon_offsets,
            lexicon_tags: self.lexicon_tags,
            lexicon_logps: self.lexicon_logps.decode("lexicon_logps")?,
        })
    }
}

// ---------------------------------------------------------------------------
// 資產編解碼
// ---------------------------------------------------------------------------

/// 資產解碼錯誤。
#[derive(Debug)]
pub enum AssetError {
    /// 舊版 LXA1 資產；0.3.0 明確拒絕載入。
    LegacyLxa1,
    /// 檔頭 magic 不符或檔案過短。
    BadMagic,
    /// 版本不符（附實際讀到的版本）。
    BadVersion(u16),
    /// payload 的 xxh3 校驗失敗。
    BadHash,
    /// postcard 反序列化失敗。
    Decode(postcard::Error),
    /// LXA3 POS 量化表 metadata 或 byte layout 無效。
    InvalidQuantized(String),
}

impl std::fmt::Display for AssetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AssetError::LegacyLxa1 => {
                write!(f, "LXA1 資產不相容；請以 0.3.0 converter 重建 LXA2")
            }
            AssetError::BadMagic => write!(f, "asset magic 不符（非 LXA2/LXA3 資產檔）"),
            AssetError::BadVersion(v) => {
                write!(f, "asset 版本 {v} 與程式支援版本 {ASSET_VERSION} 不符")
            }
            AssetError::BadHash => write!(f, "asset 校驗和不符（檔案損毀）"),
            AssetError::Decode(e) => write!(f, "asset 反序列化失敗: {e}"),
            AssetError::InvalidQuantized(message) => {
                write!(f, "量化 POS asset 無效: {message}")
            }
        }
    }
}

impl std::error::Error for AssetError {}

/// 將模型編碼為帶檔頭的資產 bytes（僅離線轉換工具使用）。
pub fn encode_asset<T: Serialize>(value: &T) -> Vec<u8> {
    encode_versioned_asset(value, ASSET_MAGIC, ASSET_VERSION)
}

fn encode_versioned_asset<T: Serialize>(value: &T, magic: [u8; 4], version: u16) -> Vec<u8> {
    let payload = postcard::to_allocvec(value).expect("postcard 序列化不應失敗");
    let hash = xxhash_rust::xxh3::xxh3_64(&payload);
    let mut out = Vec::with_capacity(4 + 2 + 8 + payload.len());
    out.extend_from_slice(&magic);
    out.extend_from_slice(&version.to_le_bytes());
    out.extend_from_slice(&hash.to_le_bytes());
    out.extend_from_slice(&payload);
    out
}

/// 驗證檔頭並解碼資產。
pub fn decode_asset<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, AssetError> {
    if bytes.len() >= 4 && bytes[0..4] == *b"LXA1" {
        return Err(AssetError::LegacyLxa1);
    }
    if bytes.len() < 14 || bytes[0..4] != ASSET_MAGIC {
        return Err(AssetError::BadMagic);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    if version != ASSET_VERSION {
        return Err(AssetError::BadVersion(version));
    }
    let stored_hash = u64::from_le_bytes(bytes[6..14].try_into().unwrap());
    let payload = &bytes[14..];
    if xxhash_rust::xxh3::xxh3_64(payload) != stored_hash {
        return Err(AssetError::BadHash);
    }
    postcard::from_bytes(payload).map_err(AssetError::Decode)
}

/// 將 POS 機率陣列量化為 i16 定點，輸出 LXA3 asset。
pub fn encode_quantized_pos_asset(model: &PosModel) -> Vec<u8> {
    encode_versioned_asset(
        &QuantizedPosModel::from_model(model),
        QUANTIZED_POS_ASSET_MAGIC,
        QUANTIZED_POS_ASSET_VERSION,
    )
}

/// 解碼 POS asset；同時接受既有 LXA2 f32 與 LXA3 i16 定點格式。
pub fn decode_pos_asset(bytes: &[u8]) -> Result<PosModel, AssetError> {
    if bytes.len() >= 4 && bytes[0..4] == *b"LXA1" {
        return Err(AssetError::LegacyLxa1);
    }
    if bytes.len() < 14 || (bytes[0..4] != ASSET_MAGIC && bytes[0..4] != QUANTIZED_POS_ASSET_MAGIC)
    {
        return Err(AssetError::BadMagic);
    }
    let version = u16::from_le_bytes([bytes[4], bytes[5]]);
    let payload = &bytes[14..];
    let stored_hash = u64::from_le_bytes(bytes[6..14].try_into().unwrap());
    if xxhash_rust::xxh3::xxh3_64(payload) != stored_hash {
        return Err(AssetError::BadHash);
    }
    match (&bytes[0..4], version) {
        (magic, ASSET_VERSION) if magic == ASSET_MAGIC => {
            postcard::from_bytes(payload).map_err(AssetError::Decode)
        }
        (magic, QUANTIZED_POS_ASSET_VERSION) if magic == QUANTIZED_POS_ASSET_MAGIC => {
            let quantized: QuantizedPosModel =
                postcard::from_bytes(payload).map_err(AssetError::Decode)?;
            quantized.into_model()
        }
        _ => Err(AssetError::BadVersion(version)),
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    fn tiny_pos_model() -> PosModel {
        PosModel {
            state_names: vec!["S-Na".into(), "S-Nb".into()],
            state_bmes: vec![STATE_S as u8, STATE_S as u8],
            state_tags: vec![0, 1],
            tag_names: vec!["Na".into(), "Nb".into()],
            start: vec![-0.25, -1.75],
            trans1: vec![-0.1, -2.2, MIN_LOG, -0.3],
            trans2: vec![-0.05, -1.1, -3.7, MIN_LOG, -0.2, -0.4, -2.0, -4.5],
            chars: CharTable {
                chars: vec!['甲', '乙'],
            },
            emit_offsets: vec![0, 1, 2],
            emit_states: vec![0, 1],
            emit_logps: vec![-0.125, -9.75],
            emit_unknown: vec![-12.5, MIN_LOG],
            lexicon_automaton_bytes: vec![1, 2, 3],
            lexicon_offsets: vec![0, 1],
            lexicon_tags: vec![1],
            lexicon_logps: vec![-0.875],
        }
    }

    fn assert_quantized_values_close(expected: &[f32], actual: &[f32]) {
        assert_eq!(expected.len(), actual.len());
        for (left, right) in expected.iter().zip(actual) {
            if *left == MIN_LOG {
                assert_eq!(*right, MIN_LOG);
            } else {
                assert!((left - right).abs() < 0.001, "{left} != {right}");
            }
        }
    }

    #[test]
    fn lxa1_is_explicitly_rejected() {
        let error = decode_asset::<u8>(b"LXA1\x01\x00legacy").unwrap_err();
        assert!(matches!(error, AssetError::LegacyLxa1));
        assert!(error.to_string().contains("LXA1"));
    }

    #[test]
    fn quantized_pos_asset_round_trips_with_reserved_min_log() {
        let model = tiny_pos_model();
        let bytes = encode_quantized_pos_asset(&model);
        assert_eq!(&bytes[0..4], b"LXA3");
        assert_eq!(u16::from_le_bytes([bytes[4], bytes[5]]), 3);
        let decoded = decode_pos_asset(&bytes).unwrap();
        assert_quantized_values_close(&model.start, &decoded.start);
        assert_quantized_values_close(&model.trans1, &decoded.trans1);
        assert_quantized_values_close(&model.trans2, &decoded.trans2);
        assert_quantized_values_close(&model.emit_logps, &decoded.emit_logps);
        assert_quantized_values_close(&model.emit_unknown, &decoded.emit_unknown);
        assert_quantized_values_close(&model.lexicon_logps, &decoded.lexicon_logps);
        assert_eq!(decoded.lexicon_automaton_bytes, vec![1, 2, 3]);
    }

    #[test]
    fn pos_decoder_remains_compatible_with_lxa2() {
        let model = tiny_pos_model();
        let bytes = encode_asset(&model);
        let decoded = decode_pos_asset(&bytes).unwrap();
        assert_eq!(decoded.start, model.start);
        assert_eq!(decoded.trans2, model.trans2);
        assert_eq!(decoded.lexicon_logps, model.lexicon_logps);
    }
}
