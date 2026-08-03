#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Identifies how a positioned glyph obtained its Unicode value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum TextOrigin {
    ActualText,
    ToUnicode,
    FontFallback,
    Replacement,
}

/// Identifies why Auto inserted a separator between glyphs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum SeparatorOrigin {
    Explicit,
    GeometrySpace,
    GeometryLineBreak,
    BlockBreak,
}

/// One separator synthesized by the V2 Auto layout pass.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct TextSeparator {
    pub page_index: usize,
    pub after_source_ordinal: Option<u64>,
    pub before_source_ordinal: Option<u64>,
    pub text: String,
    pub origin: SeparatorOrigin,
}

/// Writing direction retained from the selected PDF font.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum WritingMode {
    Horizontal,
    Vertical,
}

/// One decoded PDF character code with deterministic page-space geometry.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PositionedGlyph {
    pub page_index: usize,
    pub source_ordinal: u64,
    pub unicode: String,
    pub text_origin: TextOrigin,
    pub mcid: Option<i64>,
    pub font_resource: Option<String>,
    pub font_size: f64,
    pub writing_mode: WritingMode,
    pub origin: [f64; 2],
    pub advance: [f64; 2],
    pub baseline: [f64; 2],
    pub rotation_bucket: i16,
}
