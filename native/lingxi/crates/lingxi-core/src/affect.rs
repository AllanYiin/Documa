//! 詞級情感 taxonomy、來源格式與執行期查詢。

use std::collections::{BTreeMap, HashMap, HashSet};

use serde::{Deserialize, Serialize};

use crate::custom_lexicon::CustomLexiconSpec;

pub const AFFECT_SCHEMA_VERSION: u16 = 1;
pub const EMOTION_FAMILIES: &[&str] = &[
    "joy",
    "affection",
    "esteem",
    "outlook",
    "sadness",
    "fear",
    "anger",
    "aversion",
    "self_conscious",
    "powerlessness",
    "social_comparison",
    "surprise",
    "cognition",
    "detachment",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Polarity {
    Positive,
    Negative,
    Neutral,
    Mixed,
    Contextual,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EmotionLabel {
    pub id: String,
    pub name_zh_tw: String,
    pub family: String,
    pub default_polarity: Polarity,
    pub description: String,
    #[serde(default)]
    pub examples: Vec<String>,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EmotionTaxonomy {
    pub schema_version: u16,
    pub version: String,
    pub labels: Vec<EmotionLabel>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AffectInput {
    #[serde(default)]
    pub polarity: Option<Polarity>,
    #[serde(default)]
    pub emotions: Vec<String>,
    #[serde(default)]
    pub context_dependent: bool,
    #[serde(default)]
    pub semantic_flags: Vec<String>,
    #[serde(default)]
    pub appraisals: Vec<String>,
    #[serde(default)]
    pub notes: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AffectSourceEntry {
    pub word: String,
    #[serde(flatten)]
    pub affect: AffectInput,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AffectLexiconFile {
    pub schema_version: u16,
    pub taxonomy_version: String,
    pub entries: Vec<AffectSourceEntry>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AffectModelEntry {
    pub word: String,
    pub affect: AffectInput,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct AffectModel {
    pub schema_version: u16,
    pub taxonomy: EmotionTaxonomy,
    pub entries: Vec<AffectModelEntry>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AffectAnnotation {
    pub polarity: Polarity,
    pub emotions: Vec<String>,
    pub context_dependent: bool,
    pub semantic_flags: Vec<String>,
    pub appraisals: Vec<String>,
    pub notes: Option<String>,
    pub source: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct AffectStats {
    pub word_count: usize,
    pub family_counts: BTreeMap<String, usize>,
    pub unknown_label_count: usize,
}

pub(crate) struct AffectStore {
    entries: HashMap<String, AffectAnnotation>,
    stats: AffectStats,
}

fn default_true() -> bool {
    true
}

pub fn parse_taxonomy(text: &str) -> Result<EmotionTaxonomy, String> {
    let taxonomy: EmotionTaxonomy =
        serde_json::from_str(text).map_err(|error| format!("taxonomy JSON 無效: {error}"))?;
    validate_taxonomy(&taxonomy)?;
    Ok(taxonomy)
}

pub fn parse_affect_lexicon(text: &str) -> Result<AffectLexiconFile, String> {
    let source: AffectLexiconFile =
        serde_json::from_str(text).map_err(|error| format!("情感詞典 JSON 無效: {error}"))?;
    if source.schema_version != AFFECT_SCHEMA_VERSION {
        return Err(format!(
            "情感詞典 schemaVersion {} 不受支援（目前為 {AFFECT_SCHEMA_VERSION}）",
            source.schema_version
        ));
    }
    Ok(source)
}

pub fn build_affect_model(
    taxonomy: EmotionTaxonomy,
    lexicon: AffectLexiconFile,
) -> Result<AffectModel, String> {
    validate_taxonomy(&taxonomy)?;
    if lexicon.taxonomy_version != taxonomy.version {
        return Err(format!(
            "情感詞典 taxonomyVersion {} 與 taxonomy {} 不一致",
            lexicon.taxonomy_version, taxonomy.version
        ));
    }
    let labels = label_map(&taxonomy);
    for entry in &lexicon.entries {
        validate_affect_input(&entry.word, &entry.affect, &labels)?;
    }
    Ok(AffectModel {
        schema_version: AFFECT_SCHEMA_VERSION,
        taxonomy,
        entries: lexicon
            .entries
            .into_iter()
            .map(|entry| AffectModelEntry {
                word: entry.word,
                affect: entry.affect,
            })
            .collect(),
    })
}

pub fn validate_taxonomy(taxonomy: &EmotionTaxonomy) -> Result<(), String> {
    if taxonomy.schema_version != AFFECT_SCHEMA_VERSION {
        return Err(format!(
            "taxonomy schemaVersion {} 不受支援（目前為 {AFFECT_SCHEMA_VERSION}）",
            taxonomy.schema_version
        ));
    }
    if taxonomy.version.trim().is_empty() {
        return Err("taxonomy version 不可為空".into());
    }
    let mut ids = HashSet::new();
    for label in &taxonomy.labels {
        if label.id.trim().is_empty() || label.family.trim().is_empty() {
            return Err("taxonomy label id 與 family 不可為空".into());
        }
        if !EMOTION_FAMILIES.contains(&label.family.as_str()) {
            return Err(format!(
                "taxonomy label {:?} 使用未知家族 {:?}",
                label.id, label.family
            ));
        }
        if label.default_polarity == Polarity::Mixed {
            return Err(format!(
                "taxonomy label {:?} 的 defaultPolarity 不可為 mixed",
                label.id
            ));
        }
        if !ids.insert(label.id.as_str()) {
            return Err(format!("taxonomy label id {:?} 重複", label.id));
        }
    }
    Ok(())
}

fn label_map(taxonomy: &EmotionTaxonomy) -> HashMap<&str, &EmotionLabel> {
    taxonomy
        .labels
        .iter()
        .map(|label| (label.id.as_str(), label))
        .collect()
}

fn validate_affect_input(
    word: &str,
    input: &AffectInput,
    labels: &HashMap<&str, &EmotionLabel>,
) -> Result<(), String> {
    if input
        .polarity
        .is_some_and(|polarity| !matches!(polarity, Polarity::Mixed | Polarity::Contextual))
    {
        return Err(format!(
            "情感詞 {word:?} 的 polarity 只能覆寫為 mixed 或 contextual"
        ));
    }
    let mut seen = HashSet::new();
    for id in &input.emotions {
        if !seen.insert(id) {
            return Err(format!("情感詞 {word:?} 重複使用標籤 {id:?}"));
        }
        let Some(label) = labels.get(id.as_str()) else {
            return Err(format!("情感詞 {word:?} 使用未知標籤 {id:?}"));
        };
        if !label.enabled {
            return Err(format!("情感詞 {word:?} 使用已棄用標籤 {id:?}"));
        }
    }
    Ok(())
}

fn resolve_annotation(
    word: &str,
    input: &AffectInput,
    labels: &HashMap<&str, &EmotionLabel>,
) -> Result<AffectAnnotation, String> {
    validate_affect_input(word, input, labels)?;
    let polarity = match input.polarity {
        Some(value) => value,
        None => {
            let mut positive = false;
            let mut negative = false;
            let mut contextual = input.context_dependent;
            for id in &input.emotions {
                match labels[id.as_str()].default_polarity {
                    Polarity::Positive => positive = true,
                    Polarity::Negative => negative = true,
                    Polarity::Contextual => contextual = true,
                    Polarity::Mixed => {
                        positive = true;
                        negative = true;
                    }
                    Polarity::Neutral => {}
                }
            }
            if positive && negative {
                Polarity::Mixed
            } else if contextual {
                Polarity::Contextual
            } else if positive {
                Polarity::Positive
            } else if negative {
                Polarity::Negative
            } else {
                Polarity::Neutral
            }
        }
    };
    Ok(AffectAnnotation {
        polarity,
        emotions: input.emotions.clone(),
        context_dependent: input.context_dependent || polarity == Polarity::Contextual,
        semantic_flags: input.semantic_flags.clone(),
        appraisals: input.appraisals.clone(),
        notes: input.notes.clone(),
        source: input.source.clone(),
    })
}

impl AffectStore {
    pub(crate) fn from_model_and_custom(
        model: Option<AffectModel>,
        custom_lexicons: &[CustomLexiconSpec],
        normalize: impl Fn(&str) -> String,
    ) -> Result<Self, String> {
        let mut entries = HashMap::new();
        let mut priorities: HashMap<String, i8> = HashMap::new();
        let taxonomy = match model {
            Some(model) => {
                if model.schema_version != AFFECT_SCHEMA_VERSION {
                    return Err(format!(
                        "affect asset schema {} 不受支援（目前為 {AFFECT_SCHEMA_VERSION}）",
                        model.schema_version
                    ));
                }
                validate_taxonomy(&model.taxonomy)?;
                let labels = label_map(&model.taxonomy);
                for entry in &model.entries {
                    let word = normalize(&entry.word);
                    let annotation = resolve_annotation(&word, &entry.affect, &labels)?;
                    priorities.insert(word.clone(), i8::MIN);
                    entries.insert(word, annotation);
                }
                Some(model.taxonomy)
            }
            None => None,
        };

        for spec in custom_lexicons.iter().filter(|spec| spec.enabled) {
            for entry in &spec.entries {
                let Some(input) = &entry.affect else { continue };
                if input.polarity.is_some_and(|polarity| {
                    !matches!(polarity, Polarity::Mixed | Polarity::Contextual)
                }) {
                    return Err(format!(
                        "自訂詞 {:?} 的 polarity 只能覆寫為 mixed 或 contextual",
                        entry.word
                    ));
                }
                let word = normalize(&entry.word);
                if priorities
                    .get(&word)
                    .is_some_and(|priority| *priority > spec.priority)
                {
                    continue;
                }
                let mut annotation = if let Some(taxonomy) = &taxonomy {
                    let labels = label_map(taxonomy);
                    resolve_annotation(&word, input, &labels)?
                } else {
                    if !input.emotions.is_empty() {
                        return Err(format!(
                            "自訂詞 {word:?} 使用情緒標籤，但未載入 affect.bin taxonomy"
                        ));
                    }
                    AffectAnnotation {
                        polarity: input.polarity.unwrap_or(Polarity::Neutral),
                        emotions: Vec::new(),
                        context_dependent: input.context_dependent,
                        semantic_flags: input.semantic_flags.clone(),
                        appraisals: input.appraisals.clone(),
                        notes: input.notes.clone(),
                        source: input.source.clone(),
                    }
                };
                if annotation.source.is_none() {
                    annotation.source = Some(spec.id.clone());
                }
                priorities.insert(word.clone(), spec.priority);
                entries.insert(word, annotation);
            }
        }
        let family_by_label: HashMap<_, _> = taxonomy
            .as_ref()
            .map(|taxonomy| {
                taxonomy
                    .labels
                    .iter()
                    .map(|label| (label.id.as_str(), label.family.as_str()))
                    .collect()
            })
            .unwrap_or_default();
        let mut family_counts = BTreeMap::new();
        for annotation in entries.values() {
            let families: HashSet<_> = annotation
                .emotions
                .iter()
                .filter_map(|id| family_by_label.get(id.as_str()).copied())
                .collect();
            for family in families {
                *family_counts.entry(family.to_owned()).or_default() += 1;
            }
        }
        let stats = AffectStats {
            word_count: entries.len(),
            family_counts,
            unknown_label_count: 0,
        };
        Ok(Self { entries, stats })
    }

    pub(crate) fn stats(&self) -> &AffectStats {
        &self.stats
    }

    pub(crate) fn get(&self, normalized_word: &str) -> Option<&AffectAnnotation> {
        self.entries.get(normalized_word)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn taxonomy() -> EmotionTaxonomy {
        EmotionTaxonomy {
            schema_version: 1,
            version: "1.0.0".into(),
            labels: vec![
                EmotionLabel {
                    id: "joy.joy".into(),
                    name_zh_tw: "喜悅".into(),
                    family: "joy".into(),
                    default_polarity: Polarity::Positive,
                    description: "愉快與喜悅".into(),
                    examples: vec!["開心".into()],
                    enabled: true,
                },
                EmotionLabel {
                    id: "sadness.sadness".into(),
                    name_zh_tw: "悲傷".into(),
                    family: "sadness".into(),
                    default_polarity: Polarity::Negative,
                    description: "悲傷".into(),
                    examples: vec!["難過".into()],
                    enabled: true,
                },
            ],
        }
    }

    #[test]
    fn mixed_polarity_is_derived_from_multiple_labels() {
        let taxonomy = taxonomy();
        let labels = label_map(&taxonomy);
        let input = AffectInput {
            emotions: vec!["joy.joy".into(), "sadness.sadness".into()],
            ..Default::default()
        };
        let annotation = resolve_annotation("百感交集", &input, &labels).unwrap();
        assert_eq!(annotation.polarity, Polarity::Mixed);
    }

    #[test]
    fn entry_polarity_override_is_limited_to_mixed_or_contextual() {
        let taxonomy = taxonomy();
        let labels = label_map(&taxonomy);
        let invalid = AffectInput {
            polarity: Some(Polarity::Positive),
            emotions: vec!["joy.joy".into()],
            ..Default::default()
        };
        assert!(resolve_annotation("開心", &invalid, &labels).is_err());

        let contextual = AffectInput {
            polarity: Some(Polarity::Contextual),
            emotions: vec!["joy.joy".into()],
            ..Default::default()
        };
        assert_eq!(
            resolve_annotation("驚喜", &contextual, &labels)
                .unwrap()
                .polarity,
            Polarity::Contextual
        );
    }
}
