//! 詞典：AC 自動機查詢 + 文字正規化。
//!
//! `Dict` 由 `DictModel` 資產建構，提供一次掃描取得句中所有詞典命中
//! （即 DAG 的全部邊），以及與轉換階段完全一致的文字正規化。

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};

use daachorse::CharwiseDoubleArrayAhoCorasick;

use crate::custom_lexicon::{
    validate_custom_lexicon, CustomLexiconSpec, CUSTOM_LEXICON_BASE_BIAS,
    CUSTOM_LEXICON_PRIORITY_STEP,
};
use crate::model::DictModel;
use crate::userdict::UserDictEntry;

/// 執行期詞典。所有查詢皆為唯讀，`Send + Sync`。
/// 自訂詞典（若有）於建構期一次載入，之後與主詞典同樣不可變。
pub struct Dict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    /// 詞性名稱表；詞條 tag id 索引此表。含自訂詞條新增的詞性。
    pub tag_names: Vec<String>,
    word_tags: Vec<u8>,
    word_log_probs: Vec<f32>,
    /// 從資產 logp 還原的主詞典正頻率，供 runtime 覆寫後重新正規化。
    word_freqs: Vec<f64>,
    /// 被 runtime 詞條覆寫的主詞典 id。
    overridden_main_ids: HashSet<u32>,
    /// 異體字映射（皆為 UTF-8 等長對）。
    variant_map: Vec<(char, char)>,
    /// 自訂詞典自動機；詞條 id 自 `word_tags.len()` 起編，與主詞典共用 id 空間。
    user: Option<UserDict>,
    /// 無詞頻的多領域自訂辭典；不參與主詞典 total/logp 正規化。
    custom: Option<CustomDict>,
}

/// 自訂詞典的執行期結構（平行陣列語意同主詞典）。
struct UserDict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    tags: Vec<u8>,
    log_probs: Vec<f32>,
    id_base: u32,
}

struct CustomDict {
    automaton: CharwiseDoubleArrayAhoCorasick<u32>,
    tags: Vec<u8>,
    biases: Vec<f64>,
    sources: Vec<String>,
    domains: Vec<String>,
    priorities: Vec<i8>,
    id_base: u32,
}

/// 一筆詞典命中：位於輸入字串的 byte 區間與詞條 id。
#[derive(Clone, Copy, Debug)]
pub struct DictMatch {
    pub byte_start: usize,
    pub byte_end: usize,
    pub word_id: u32,
    /// Some 表示無詞頻自訂詞；值為獨立於主詞頻的加分。
    pub custom_bias: Option<f64>,
}

impl Dict {
    /// 由資產模型建構。
    ///
    /// automaton bytes 來自 lingxi-convert 的 daachorse serialize；
    /// 資產檔頭已做 xxh3 校驗，故此處信任其內容（deserialize_unchecked）。
    pub fn from_model(m: DictModel) -> Self {
        let (automaton, _rest) = unsafe {
            CharwiseDoubleArrayAhoCorasick::<u32>::deserialize_unchecked(&m.automaton_bytes)
        };
        let word_freqs: Vec<f64> = m
            .word_log_probs
            .iter()
            .map(|&logp| ((logp + m.total_log) as f64).exp())
            .collect();
        Dict {
            automaton,
            tag_names: m.tag_names,
            word_tags: m.word_tags,
            word_log_probs: m.word_log_probs,
            word_freqs,
            overridden_main_ids: HashSet::new(),
            variant_map: m.variant_map,
            user: None,
            custom: None,
        }
    }

    /// 載入自訂詞典（建構期呼叫一次）。
    ///
    /// - 詞先經 `normalize`，重複詞條後者覆蓋前者。
    /// - 僅接受 2 至 255 字元、有限正頻率；主詞典同詞視為覆寫。
    /// - 主詞典與 runtime 詞條合併後重新計算 total 與全部 logp。
    /// - 新詞性字串直接擴充 `tag_names`；超過 u8 id 空間（256）回傳錯誤。
    pub fn install_user_dict(&mut self, entries: &[UserDictEntry]) -> Result<(), String> {
        // 去重（後者覆蓋）並保持穩定順序，供自動機編 id。
        let mut index: HashMap<String, usize> = HashMap::new();
        let mut words: Vec<String> = Vec::new();
        let mut tags: Vec<u8> = Vec::new();
        let mut frequencies: Vec<f64> = Vec::new();
        for e in entries {
            let word = self.normalize(&e.word).into_owned();
            let char_len = word.chars().count();
            if !(2..=u8::MAX as usize).contains(&char_len) {
                return Err(format!("自訂詞 {word:?} 必須包含 2 至 255 個字元"));
            }
            let tag_name = e.tag.as_deref().unwrap_or("Na");
            let tag_id = match self.tag_names.iter().position(|t| t == tag_name) {
                Some(i) => i as u8,
                None => {
                    if self.tag_names.len() > u8::MAX as usize {
                        return Err(format!("詞性表已滿（256），無法新增詞性 {tag_name}"));
                    }
                    self.tag_names.push(tag_name.to_string());
                    (self.tag_names.len() - 1) as u8
                }
            };
            let frequency = e.freq.ok_or_else(|| {
                format!("自訂詞 {word:?} 缺少頻率；0.3.0 起 runtime 詞典必須提供正頻率")
            })?;
            if !frequency.is_finite() || frequency <= 0.0 {
                return Err(format!("自訂詞 {word:?} 的頻率必須是有限正數"));
            }
            match index.get(&word) {
                Some(&i) => {
                    tags[i] = tag_id;
                    frequencies[i] = frequency;
                }
                None => {
                    index.insert(word.clone(), words.len());
                    words.push(word);
                    tags.push(tag_id);
                    frequencies.push(frequency);
                }
            }
        }
        if words.is_empty() {
            return Ok(());
        }

        let mut overridden_main_ids = HashSet::new();
        for word in &words {
            for found in self.automaton.find_overlapping_iter(word) {
                if found.start() == 0 && found.end() == word.len() {
                    overridden_main_ids.insert(found.value());
                }
            }
        }
        let base_total: f64 = self.word_freqs.iter().sum();
        let overridden_total: f64 = overridden_main_ids
            .iter()
            .map(|&id| self.word_freqs[id as usize])
            .sum();
        let total = base_total - overridden_total + frequencies.iter().sum::<f64>();
        if !total.is_finite() || total <= 0.0 {
            return Err("runtime 詞典合併後總頻率不合法".into());
        }
        let total_log = total.ln();
        self.word_log_probs = self
            .word_freqs
            .iter()
            .map(|frequency| (frequency.ln() - total_log) as f32)
            .collect();
        self.overridden_main_ids = overridden_main_ids;
        let log_probs: Vec<f32> = frequencies
            .iter()
            .map(|frequency| (frequency.ln() - total_log) as f32)
            .collect();

        let id_base = self.word_tags.len() as u32;
        let patterns = words
            .iter()
            .enumerate()
            .map(|(i, w)| (w.as_str(), id_base + i as u32));
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(patterns)
            .map_err(|e| format!("自訂詞典自動機建構失敗: {e}"))?;
        self.user = Some(UserDict {
            automaton,
            tags,
            log_probs,
            id_base,
        });
        Ok(())
    }

    /// 安裝多份無詞頻自訂辭典。主詞典與 legacy user dictionary 機率完全不變。
    pub fn install_custom_lexicons(&mut self, specs: &[CustomLexiconSpec]) -> Result<(), String> {
        #[derive(Clone)]
        struct Candidate {
            word: String,
            tag: String,
            priority: i8,
            source: String,
            domain: String,
        }

        let mut ids = HashSet::new();
        let mut candidates: Vec<Candidate> = Vec::new();
        let mut index: HashMap<String, usize> = HashMap::new();
        for spec in specs {
            validate_custom_lexicon(spec)?;
            if !ids.insert(spec.id.as_str()) {
                return Err(format!("自訂辭典 id {:?} 重複", spec.id));
            }
            if !spec.enabled {
                continue;
            }
            for entry in &spec.entries {
                let word = self.normalize(&entry.word).into_owned();
                let char_len = word.chars().count();
                if !(2..=u8::MAX as usize).contains(&char_len) {
                    return Err(format!(
                        "自訂辭典 {:?} 的詞 {word:?} 必須包含 2 至 255 個字元",
                        spec.id
                    ));
                }
                let candidate = Candidate {
                    word: word.clone(),
                    tag: entry.pos.clone().unwrap_or_else(|| "Na".into()),
                    priority: spec.priority,
                    source: spec.id.clone(),
                    domain: spec.domain.clone(),
                };
                match index.get(&word).copied() {
                    Some(position) if candidates[position].priority > spec.priority => {}
                    Some(position) => candidates[position] = candidate,
                    None => {
                        index.insert(word, candidates.len());
                        candidates.push(candidate);
                    }
                }
            }
        }
        if candidates.is_empty() {
            self.custom = None;
            return Ok(());
        }

        let mut tags = Vec::with_capacity(candidates.len());
        let mut biases = Vec::with_capacity(candidates.len());
        let mut sources = Vec::with_capacity(candidates.len());
        let mut domains = Vec::with_capacity(candidates.len());
        for candidate in &candidates {
            let tag_id = match self.tag_names.iter().position(|tag| tag == &candidate.tag) {
                Some(id) => id as u8,
                None => {
                    if self.tag_names.len() > u8::MAX as usize {
                        return Err(format!("詞性表已滿（256），無法新增詞性 {}", candidate.tag));
                    }
                    self.tag_names.push(candidate.tag.clone());
                    (self.tag_names.len() - 1) as u8
                }
            };
            tags.push(tag_id);
            biases.push(
                CUSTOM_LEXICON_BASE_BIAS
                    + f64::from(candidate.priority) * CUSTOM_LEXICON_PRIORITY_STEP,
            );
            sources.push(candidate.source.clone());
            domains.push(candidate.domain.clone());
        }
        let priorities = candidates
            .iter()
            .map(|candidate| candidate.priority)
            .collect();

        let legacy_len = self.user.as_ref().map_or(0, |user| user.tags.len());
        let id_base = (self.word_tags.len() + legacy_len) as u32;
        let patterns = candidates
            .iter()
            .enumerate()
            .map(|(index, candidate)| (candidate.word.as_str(), id_base + index as u32));
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(patterns)
            .map_err(|error| format!("多領域自訂辭典自動機建構失敗: {error}"))?;
        self.custom = Some(CustomDict {
            automaton,
            tags,
            biases,
            sources,
            domains,
            priorities,
            id_base,
        });
        Ok(())
    }

    /// 一次掃描回傳文字（須已正規化）中的所有詞典命中，byte 區間可重疊。
    /// 主詞典與自訂詞典的命中串接輸出；同一區間兩邊皆命中時由呼叫端
    /// 依 log 機率取捨（DAG DP 與詞性回查皆取機率較高者）。
    #[inline]
    pub fn matches<'s>(&'s self, normalized: &'s str) -> impl Iterator<Item = DictMatch> + 's {
        let main = self
            .automaton
            .find_overlapping_iter(normalized)
            .filter(move |m| !self.overridden_main_ids.contains(&m.value()))
            .map(|m| DictMatch {
                byte_start: m.start(),
                byte_end: m.end(),
                word_id: m.value(),
                custom_bias: None,
            });
        let user = self.user.iter().flat_map(move |u| {
            u.automaton
                .find_overlapping_iter(normalized)
                .map(|m| DictMatch {
                    byte_start: m.start(),
                    byte_end: m.end(),
                    word_id: m.value(),
                    custom_bias: None,
                })
        });
        let custom = self.custom.iter().flat_map(move |custom| {
            custom
                .automaton
                .find_overlapping_iter(normalized)
                .map(|m| DictMatch {
                    byte_start: m.start(),
                    byte_end: m.end(),
                    word_id: m.value(),
                    custom_bias: Some(custom.biases[(m.value() - custom.id_base) as usize]),
                })
        });
        main.chain(user).chain(custom)
    }

    /// 詞條的 ln(freq/total)。
    #[inline]
    pub fn log_prob(&self, word_id: u32) -> f32 {
        match &self.user {
            Some(user)
                if word_id >= user.id_base
                    && word_id < user.id_base + user.log_probs.len() as u32 =>
            {
                user.log_probs[(word_id - user.id_base) as usize]
            }
            _ => self.word_log_probs[word_id as usize],
        }
    }

    /// 是否為 runtime／curated 覆寫詞條。
    #[inline]
    pub fn is_user(&self, word_id: u32) -> bool {
        self.user.as_ref().is_some_and(|user| {
            word_id >= user.id_base && word_id < user.id_base + user.tags.len() as u32
        }) || self
            .custom
            .as_ref()
            .is_some_and(|custom| word_id >= custom.id_base)
    }
    /// 詞條的詞性 id（索引 `tag_names`）。
    #[inline]
    pub fn tag(&self, word_id: u32) -> u8 {
        if let Some(custom) = &self.custom {
            if word_id >= custom.id_base {
                return custom.tags[(word_id - custom.id_base) as usize];
            }
        }
        match &self.user {
            Some(user) if word_id >= user.id_base => user.tags[(word_id - user.id_base) as usize],
            _ => self.word_tags[word_id as usize],
        }
    }

    pub fn custom_source(&self, word_id: u32) -> Option<(&str, &str, i8)> {
        let custom = self.custom.as_ref()?;
        if word_id < custom.id_base {
            return None;
        }
        let index = (word_id - custom.id_base) as usize;
        Some((
            &custom.sources[index],
            &custom.domains[index],
            custom.priorities[index],
        ))
    }

    /// 文字正規化：ASCII 小寫 + 異體字替換。
    ///
    /// 與 lingxi-convert 對詞典 key 的正規化完全一致，且保證輸出與輸入
    /// byte 長度相同（ASCII 小寫等長、異體字對已驗證等長），因此在
    /// 正規化字串上得到的 byte offset 可直接切片原始輸入。
    /// 無須修改時回傳 Borrowed，零配置。
    pub fn normalize<'a>(&self, text: &'a str) -> Cow<'a, str> {
        let needs_change = text
            .chars()
            .any(|c| c.is_ascii_uppercase() || self.variant_map.iter().any(|(f, _)| *f == c));
        if !needs_change {
            return Cow::Borrowed(text);
        }
        Cow::Owned(
            text.chars()
                .map(|c| {
                    let c = c.to_ascii_lowercase();
                    self.variant_map
                        .iter()
                        .find(|(f, _)| *f == c)
                        .map(|(_, t)| *t)
                        .unwrap_or(c)
                })
                .collect(),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::DictModel;

    fn tiny_dict() -> Dict {
        let words = ["甲乙", "乙丙"];
        let automaton = CharwiseDoubleArrayAhoCorasick::<u32>::with_values(
            words
                .iter()
                .enumerate()
                .map(|(id, word)| (*word, id as u32)),
        )
        .unwrap();
        Dict::from_model(DictModel {
            automaton_bytes: automaton.serialize(),
            tag_names: vec!["Na".into()],
            word_tags: vec![0, 0],
            word_log_probs: vec![0.5f32.ln(), 0.5f32.ln()],
            word_char_lens: vec![2, 2],
            total_log: 20.0f32.ln(),
            variant_map: vec![],
        })
    }

    #[test]
    fn runtime_dictionary_rejects_invalid_entries() {
        for entry in [
            UserDictEntry {
                word: "甲".into(),
                freq: Some(1.0),
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: None,
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: Some(0.0),
                tag: None,
            },
            UserDictEntry {
                word: "甲乙".into(),
                freq: Some(f64::NAN),
                tag: None,
            },
        ] {
            assert!(tiny_dict().install_user_dict(&[entry]).is_err());
        }
    }

    #[test]
    fn frequency_free_custom_lexicon_keeps_main_probabilities_unchanged() {
        let mut dict = tiny_dict();
        let before = dict.word_log_probs.clone();
        dict.install_custom_lexicons(&[CustomLexiconSpec {
            schema_version: 1,
            id: "medical".into(),
            domain: "medical".into(),
            priority: 3,
            enabled: true,
            entries: vec![crate::CustomLexiconEntry {
                word: "甲乙丙".into(),
                pos: Some("Nb".into()),
                affect: None,
            }],
        }])
        .unwrap();
        assert_eq!(dict.word_log_probs, before);
        let found = dict
            .matches("甲乙丙")
            .find(|item| item.custom_bias.is_some())
            .unwrap();
        assert_eq!(found.custom_bias, Some(7.5));
        assert_eq!(
            dict.custom_source(found.word_id),
            Some(("medical", "medical", 3))
        );
    }

    #[test]
    fn runtime_override_recomputes_the_combined_total() {
        let mut dict = tiny_dict();
        dict.install_user_dict(&[UserDictEntry {
            word: "甲乙".into(),
            freq: Some(30.0),
            tag: Some("Na".into()),
        }])
        .unwrap();
        let matches: Vec<_> = dict.matches("甲乙乙丙").collect();
        let overridden: Vec<_> = matches
            .iter()
            .filter(|item| item.byte_start == 0 && item.byte_end == 6)
            .collect();
        assert_eq!(overridden.len(), 1, "主詞典同詞必須被 runtime 詞條取代");
        assert!((dict.log_prob(overridden[0].word_id) - (0.75f32).ln()).abs() < 1e-5);
        let base = matches.iter().find(|item| item.byte_start == 6).unwrap();
        assert!((dict.log_prob(base.word_id) - (0.25f32).ln()).abs() < 1e-5);
    }

    #[test]
    fn custom_lexicon_conflicts_follow_priority_then_load_order() {
        let entry = |word: &str, pos: &str| crate::CustomLexiconEntry {
            word: word.into(),
            pos: Some(pos.into()),
            affect: None,
        };
        let mut dict = tiny_dict();
        dict.install_custom_lexicons(&[
            CustomLexiconSpec {
                schema_version: 1,
                id: "first".into(),
                domain: "medical".into(),
                priority: 2,
                enabled: true,
                entries: vec![entry("甲乙丙", "Na"), entry("甲乙丙", "Nb")],
            },
            CustomLexiconSpec {
                schema_version: 1,
                id: "lower".into(),
                domain: "legal".into(),
                priority: 1,
                enabled: true,
                entries: vec![entry("甲乙丙", "Nc")],
            },
            CustomLexiconSpec {
                schema_version: 1,
                id: "later".into(),
                domain: "finance".into(),
                priority: 2,
                enabled: true,
                entries: vec![entry("甲乙丙", "Nd")],
            },
            CustomLexiconSpec {
                schema_version: 1,
                id: "off".into(),
                domain: "ignored".into(),
                priority: 10,
                enabled: false,
                entries: vec![entry("甲乙丙", "Ne")],
            },
        ])
        .unwrap();

        let found = dict
            .matches("甲乙丙")
            .find(|item| item.custom_bias.is_some())
            .unwrap();
        assert_eq!(
            dict.custom_source(found.word_id),
            Some(("later", "finance", 2))
        );
        assert_eq!(dict.tag_names[dict.tag(found.word_id) as usize], "Nd");
        assert_eq!(found.custom_bias, Some(7.0));
    }

    #[test]
    fn custom_lexicon_rejects_duplicate_ids_and_normalizes_variants() {
        let mut dict = tiny_dict();
        dict.variant_map = vec![('臺', '台')];
        let spec = |id: &str, word: &str| CustomLexiconSpec {
            schema_version: 1,
            id: id.into(),
            domain: "test".into(),
            priority: 0,
            enabled: true,
            entries: vec![crate::CustomLexiconEntry {
                word: word.into(),
                pos: None,
                affect: None,
            }],
        };
        assert!(dict
            .install_custom_lexicons(&[spec("same", "臺北"), spec("same", "台北")])
            .unwrap_err()
            .contains("id"));

        dict.install_custom_lexicons(&[spec("traditional", "臺北"), spec("normalized", "台北")])
            .unwrap();
        let matches: Vec<_> = dict
            .matches("台北")
            .filter(|item| item.custom_bias.is_some())
            .collect();
        assert_eq!(matches.len(), 1);
        assert_eq!(
            dict.custom_source(matches[0].word_id),
            Some(("normalized", "test", 0))
        );
    }
}
