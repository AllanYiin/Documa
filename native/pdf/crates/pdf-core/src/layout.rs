use unicode_normalization::UnicodeNormalization;

use crate::text_model::{PositionedGlyph, SeparatorOrigin, TextSeparator, WritingMode};

#[derive(Debug, Default)]
pub(crate) struct AutoLayoutResult {
    pub text: String,
    pub separators: Vec<TextSeparator>,
    pub inserted_spaces: usize,
    pub inserted_line_breaks: usize,
    pub ambiguous_boundaries: usize,
    pub reading_order_ambiguous: bool,
}

#[derive(Debug)]
struct Line<'a> {
    glyphs: Vec<&'a PositionedGlyph>,
    normal_center: f64,
    max_font_size: f64,
}

pub(crate) fn auto_layout(page_index: usize, glyphs: &[PositionedGlyph]) -> AutoLayoutResult {
    if glyphs.is_empty() {
        return AutoLayoutResult::default();
    }

    let mut groups: Vec<OrientationGroup<'_>> = Vec::new();
    for glyph in glyphs {
        if let Some(group) = groups.iter_mut().find(|group| {
            group.rotation_bucket == glyph.rotation_bucket
                && group.writing_mode == glyph.writing_mode
        }) {
            group.glyphs.push(glyph);
        } else {
            groups.push(OrientationGroup {
                rotation_bucket: glyph.rotation_bucket,
                writing_mode: glyph.writing_mode,
                glyphs: vec![glyph],
            });
        }
    }
    groups.sort_by_key(|group| {
        group
            .glyphs
            .first()
            .map_or(u64::MAX, |glyph| glyph.source_ordinal)
    });

    let mixed_orientation = groups.len() > 1;
    let mut result = AutoLayoutResult {
        reading_order_ambiguous: mixed_orientation,
        ambiguous_boundaries: usize::from(mixed_orientation),
        ..AutoLayoutResult::default()
    };
    let mut previous_source_ordinal = None;
    for group in groups {
        let before_source_ordinal = group.glyphs.first().map(|glyph| glyph.source_ordinal);
        let group_last_source_ordinal = group.glyphs.iter().map(|glyph| glyph.source_ordinal).max();
        let group_result = layout_orientation(page_index, &group.glyphs, group.writing_mode);
        if !result.text.is_empty() && !group_result.text.is_empty() {
            result.text.push('\n');
            result.inserted_line_breaks = result.inserted_line_breaks.saturating_add(1);
            result.separators.push(TextSeparator {
                page_index,
                after_source_ordinal: previous_source_ordinal,
                before_source_ordinal,
                text: "\n".to_owned(),
                origin: SeparatorOrigin::GeometryLineBreak,
            });
        }
        result.text.push_str(&group_result.text);
        result.separators.extend(group_result.separators);
        result.inserted_spaces = result
            .inserted_spaces
            .saturating_add(group_result.inserted_spaces);
        result.inserted_line_breaks = result
            .inserted_line_breaks
            .saturating_add(group_result.inserted_line_breaks);
        result.ambiguous_boundaries = result
            .ambiguous_boundaries
            .saturating_add(group_result.ambiguous_boundaries);
        result.reading_order_ambiguous |= group_result.reading_order_ambiguous;
        previous_source_ordinal = group_last_source_ordinal.or(previous_source_ordinal);
    }
    result
}

#[derive(Debug)]
struct OrientationGroup<'a> {
    rotation_bucket: i16,
    writing_mode: WritingMode,
    glyphs: Vec<&'a PositionedGlyph>,
}

fn layout_orientation(
    page_index: usize,
    glyphs: &[&PositionedGlyph],
    writing_mode: WritingMode,
) -> AutoLayoutResult {
    if writing_mode == WritingMode::Vertical {
        return content_order_fallback(glyphs, true);
    }

    let baseline = glyphs[0].baseline;
    let mut ordered = glyphs.to_vec();
    ordered.sort_by(|left, right| {
        normal_projection(right, baseline)
            .total_cmp(&normal_projection(left, baseline))
            .then_with(|| {
                along_projection(left, baseline).total_cmp(&along_projection(right, baseline))
            })
            .then_with(|| left.source_ordinal.cmp(&right.source_ordinal))
    });
    let lines = cluster_lines(ordered, baseline);
    if has_multi_column_or_severe_overlap(&lines, baseline) {
        return content_order_fallback(glyphs, true);
    }

    let mut result = AutoLayoutResult::default();
    for (line_index, mut line) in lines.into_iter().enumerate() {
        line.glyphs.sort_by(|left, right| {
            along_projection(left, baseline)
                .total_cmp(&along_projection(right, baseline))
                .then_with(|| left.source_ordinal.cmp(&right.source_ordinal))
        });
        if line_index > 0 && !result.text.ends_with('\n') {
            let before = line.glyphs.first().map(|glyph| glyph.source_ordinal);
            let after = glyphs
                .iter()
                .filter(|glyph| before.is_none_or(|before| glyph.source_ordinal < before))
                .map(|glyph| glyph.source_ordinal)
                .max();
            result.text.push('\n');
            result.inserted_line_breaks = result.inserted_line_breaks.saturating_add(1);
            result.separators.push(TextSeparator {
                page_index,
                after_source_ordinal: after,
                before_source_ordinal: before,
                text: "\n".to_owned(),
                origin: SeparatorOrigin::GeometryLineBreak,
            });
        }
        append_line(page_index, &line.glyphs, baseline, &mut result);
    }
    result
}

fn cluster_lines<'a>(glyphs: Vec<&'a PositionedGlyph>, baseline: [f64; 2]) -> Vec<Line<'a>> {
    let mut lines: Vec<Line<'a>> = Vec::new();
    for glyph in glyphs {
        let normal = normal_projection(glyph, baseline);
        let page_scale = projected_advance(glyph, baseline)
            .abs()
            .max(glyph.font_size.abs())
            .max(1.0);
        if let Some(line) = lines.last_mut() {
            let tolerance = line.max_font_size.max(page_scale) * 0.5;
            if (normal - line.normal_center).abs() <= tolerance {
                line.normal_center = (line.normal_center + normal) * 0.5;
                line.max_font_size = line.max_font_size.max(page_scale);
                line.glyphs.push(glyph);
                continue;
            }
        }
        lines.push(Line {
            glyphs: vec![glyph],
            normal_center: normal,
            max_font_size: page_scale,
        });
    }
    lines
}

fn append_line(
    page_index: usize,
    glyphs: &[&PositionedGlyph],
    baseline: [f64; 2],
    result: &mut AutoLayoutResult,
) {
    let mut previous: Option<&PositionedGlyph> = None;
    for glyph in glyphs {
        if let Some(last) = previous {
            let gap = along_projection(glyph, baseline) - glyph_end(last, baseline);
            let scale = projected_advance(last, baseline)
                .abs()
                .max(last.font_size.abs() * 0.5)
                .max(glyph.font_size.abs() * 0.5)
                .max(1.0);
            let normalized_gap = gap / scale;
            let left = last.unicode.chars().next_back();
            let right = glyph.unicode.chars().next();
            if should_insert_space(left, right, normalized_gap)
                && !result.text.ends_with(char::is_whitespace)
                && !glyph.unicode.starts_with(char::is_whitespace)
            {
                result.text.push(' ');
                result.inserted_spaces += 1;
                result.separators.push(TextSeparator {
                    page_index,
                    after_source_ordinal: Some(last.source_ordinal),
                    before_source_ordinal: Some(glyph.source_ordinal),
                    text: " ".to_owned(),
                    origin: SeparatorOrigin::GeometrySpace,
                });
            } else if (0.15..=0.35).contains(&normalized_gap)
                && left.is_some_and(is_word_character)
                && right.is_some_and(is_word_character)
            {
                result.ambiguous_boundaries += 1;
            }
        }
        append_explicit_text(&mut result.text, &glyph.unicode);
        previous = Some(glyph);
    }
}

fn append_explicit_text(output: &mut String, text: &str) {
    for character in text.chars() {
        if is_cjk_compatibility(character) {
            for normalized in character.to_string().nfkc() {
                append_character(output, normalized);
            }
        } else {
            append_character(output, character);
        }
    }
}

fn append_character(output: &mut String, character: char) {
    if character.is_whitespace() && output.ends_with(char::is_whitespace) {
        return;
    }
    output.push(character);
}

fn is_cjk_compatibility(character: char) -> bool {
    matches!(u32::from(character), 0x2e80..=0x2fdf | 0xf900..=0xfaff)
}

fn should_insert_space(left: Option<char>, right: Option<char>, gap: f64) -> bool {
    let (Some(left), Some(right)) = (left, right) else {
        return false;
    };
    if left.is_whitespace() || right.is_whitespace() {
        return false;
    }
    if is_cjk(left) && is_cjk(right) {
        return false;
    }
    if is_closing_punctuation(right) || is_opening_punctuation(left) {
        return false;
    }
    if is_word_character(left) && is_word_character(right) {
        let threshold = if is_cjk(left) || is_cjk(right) {
            0.5
        } else {
            0.35
        };
        return gap > threshold;
    }
    gap > 0.6
}

fn has_multi_column_or_severe_overlap(lines: &[Line<'_>], baseline: [f64; 2]) -> bool {
    lines.iter().any(|line| {
        let mut glyphs = line.glyphs.clone();
        glyphs.sort_by(|left, right| {
            along_projection(left, baseline)
                .total_cmp(&along_projection(right, baseline))
                .then_with(|| left.source_ordinal.cmp(&right.source_ordinal))
        });
        glyphs.windows(2).any(|pair| {
            let gap = along_projection(pair[1], baseline) - glyph_end(pair[0], baseline);
            let scale = projected_advance(pair[0], baseline)
                .abs()
                .max(pair[0].font_size.abs() * 0.5)
                .max(1.0);
            let normalized = gap / scale;
            !normalized.is_finite() || !(-1.0..=8.0).contains(&normalized)
        })
    })
}

fn content_order_fallback(
    glyphs: &[&PositionedGlyph],
    reading_order_ambiguous: bool,
) -> AutoLayoutResult {
    let mut ordered = glyphs.to_vec();
    ordered.sort_by_key(|glyph| glyph.source_ordinal);
    let mut text = String::new();
    for glyph in ordered {
        append_explicit_text(&mut text, &glyph.unicode);
    }
    AutoLayoutResult {
        text,
        reading_order_ambiguous,
        ambiguous_boundaries: usize::from(reading_order_ambiguous),
        ..AutoLayoutResult::default()
    }
}

fn along_projection(glyph: &PositionedGlyph, baseline: [f64; 2]) -> f64 {
    glyph.origin[0] * baseline[0] + glyph.origin[1] * baseline[1]
}

fn normal_projection(glyph: &PositionedGlyph, baseline: [f64; 2]) -> f64 {
    glyph.origin[0] * -baseline[1] + glyph.origin[1] * baseline[0]
}

fn projected_advance(glyph: &PositionedGlyph, baseline: [f64; 2]) -> f64 {
    glyph.advance[0] * baseline[0] + glyph.advance[1] * baseline[1]
}

fn glyph_end(glyph: &PositionedGlyph, baseline: [f64; 2]) -> f64 {
    along_projection(glyph, baseline) + projected_advance(glyph, baseline)
}

fn is_word_character(character: char) -> bool {
    character.is_alphanumeric() || matches!(character, '_' | '\'' | '’')
}

fn is_cjk(character: char) -> bool {
    matches!(
        u32::from(character),
        0x2e80..=0x2fdf
            | 0x3040..=0x30ff
            | 0x31f0..=0x31ff
            | 0x3400..=0x4dbf
            | 0x4e00..=0x9fff
            | 0xac00..=0xd7af
            | 0xf900..=0xfaff
            | 0x20000..=0x323af
    )
}

fn is_closing_punctuation(character: char) -> bool {
    matches!(
        character,
        '.' | ',' | ';' | ':' | '!' | '?' | ')' | ']' | '}' | '，' | '。' | '！' | '？' | '：'
    )
}

fn is_opening_punctuation(character: char) -> bool {
    matches!(character, '(' | '[' | '{' | '（' | '「' | '『' | '【')
}
