use std::collections::BTreeMap;

use crate::{
    ErrorCode, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    text_model::WritingMode,
};

const DEFAULT_GLYPH_WIDTH: f64 = 500.0;
const DEFAULT_CID_WIDTH: f64 = 1_000.0;
const DEFAULT_ASCENT: f64 = 800.0;
const DEFAULT_DESCENT: f64 = -200.0;
const MAX_VERTICAL_METRIC_MAGNITUDE: f64 = 4_000.0;

#[derive(Debug, Clone, Copy)]
pub(crate) struct FontVerticalMetrics {
    pub(crate) ascent: f64,
    pub(crate) descent: f64,
}

/// CID vertical displacement and vector from horizontal to vertical origin.
#[derive(Debug, Clone, Copy)]
pub(crate) struct CidVerticalMetrics {
    pub(crate) displacement: f64,
    pub(crate) position: [f64; 2],
}

impl Default for FontVerticalMetrics {
    fn default() -> Self {
        Self {
            ascent: DEFAULT_ASCENT,
            descent: DEFAULT_DESCENT,
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) enum FontMetrics {
    Simple {
        first_char: u32,
        widths: Vec<f64>,
        missing_width: f64,
        vertical: FontVerticalMetrics,
    },
    Cid {
        default_width: f64,
        widths: BTreeMap<u32, f64>,
        writing_mode: WritingMode,
        vertical: FontVerticalMetrics,
        default_vertical: [f64; 2],
        vertical_widths: BTreeMap<u32, CidVerticalMetrics>,
    },
}

impl FontMetrics {
    pub(crate) fn from_font(
        document: &PdfDocument,
        dictionary: &PdfDictionary,
        is_type0: bool,
    ) -> PdfResult<Self> {
        if is_type0 {
            parse_cid_metrics(document, dictionary)
        } else {
            parse_simple_metrics(document, dictionary)
        }
    }

    pub(crate) fn width(&self, code: u32) -> f64 {
        match self {
            Self::Simple {
                first_char,
                widths,
                missing_width,
                ..
            } => code
                .checked_sub(*first_char)
                .and_then(|index| usize::try_from(index).ok())
                .and_then(|index| widths.get(index))
                .copied()
                .unwrap_or(*missing_width),
            Self::Cid {
                default_width,
                widths,
                ..
            } => widths.get(&code).copied().unwrap_or(*default_width),
        }
    }

    pub(crate) const fn writing_mode(&self) -> WritingMode {
        match self {
            Self::Simple { .. } => WritingMode::Horizontal,
            Self::Cid { writing_mode, .. } => *writing_mode,
        }
    }

    pub(crate) const fn vertical_metrics(&self) -> FontVerticalMetrics {
        match self {
            Self::Simple { vertical, .. } | Self::Cid { vertical, .. } => *vertical,
        }
    }

    pub(crate) fn cid_vertical_metrics(&self, code: u32) -> CidVerticalMetrics {
        match self {
            Self::Cid {
                default_vertical,
                vertical_widths,
                ..
            } => vertical_widths
                .get(&code)
                .copied()
                .unwrap_or(CidVerticalMetrics {
                    displacement: default_vertical[1],
                    position: [self.width(code) / 2.0, default_vertical[0]],
                }),
            Self::Simple { .. } => CidVerticalMetrics {
                displacement: -1000.0,
                position: [self.width(code) / 2.0, 880.0],
            },
        }
    }
}

fn parse_simple_metrics(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
) -> PdfResult<FontMetrics> {
    let first_char = dictionary
        .get(&PdfName(b"FirstChar".to_vec()))
        .map(integer_u32)
        .transpose()?
        .unwrap_or(0);
    let widths = dictionary
        .get(&PdfName(b"Widths".to_vec()))
        .map(|object| resolve_array(document, object, "font Widths"))
        .transpose()?
        .map(|values| {
            values
                .iter()
                .map(|value| finite_number(value, "font width"))
                .collect::<PdfResult<Vec<_>>>()
        })
        .transpose()?
        .unwrap_or_default();
    if widths.len() > document.limits.max_array_items {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "font Widths limit exceeded",
        ));
    }
    let descriptor = dictionary
        .get(&PdfName(b"FontDescriptor".to_vec()))
        .map(|object| resolve_dictionary(document, object, "FontDescriptor"))
        .transpose()?;
    let missing_width = descriptor
        .as_ref()
        .and_then(|value| value.get(&PdfName(b"MissingWidth".to_vec())))
        .map(|value| finite_number(value, "MissingWidth"))
        .transpose()?
        .unwrap_or(DEFAULT_GLYPH_WIDTH);
    let vertical = descriptor
        .as_ref()
        .map(parse_vertical_metrics)
        .transpose()?
        .unwrap_or_default();
    Ok(FontMetrics::Simple {
        first_char,
        widths,
        missing_width,
        vertical,
    })
}

fn parse_cid_metrics(document: &PdfDocument, dictionary: &PdfDictionary) -> PdfResult<FontMetrics> {
    let descendants = dictionary
        .get(&PdfName(b"DescendantFonts".to_vec()))
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "Type0 font is missing DescendantFonts",
            )
        })
        .and_then(|object| resolve_array(document, object, "DescendantFonts"))?;
    let descendant = descendants.first().ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "Type0 DescendantFonts must contain a CIDFont",
        )
    })?;
    let descendant = resolve_dictionary(document, descendant, "CIDFont")?;
    let default_width = descendant
        .get(&PdfName(b"DW".to_vec()))
        .map(|value| finite_number(value, "CIDFont DW"))
        .transpose()?
        .unwrap_or(DEFAULT_CID_WIDTH);
    let widths = descendant
        .get(&PdfName(b"W".to_vec()))
        .map(|object| resolve_array(document, object, "CIDFont W"))
        .transpose()?
        .map(|values| parse_cid_widths(&values, document.limits.max_array_items))
        .transpose()?
        .unwrap_or_default();
    let writing_mode = dictionary
        .get(&PdfName(b"Encoding".to_vec()))
        .map(|object| resolve_object(document, object))
        .transpose()?
        .as_ref()
        .and_then(|object| match object {
            PdfObject::Name(name) if name.as_bytes().ends_with(b"-V") => {
                Some(WritingMode::Vertical)
            }
            _ => None,
        })
        .unwrap_or(WritingMode::Horizontal);
    let default_vertical = match descendant.get(&PdfName(b"DW2".to_vec())) {
        Some(object) => {
            let values = resolve_array(document, object, "CIDFont DW2")?;
            let [position, displacement] = values.as_slice() else {
                return invalid("CIDFont DW2 must contain exactly two numbers");
            };
            [
                finite_number(position, "CIDFont DW2 position")?,
                finite_number(displacement, "CIDFont DW2 displacement")?,
            ]
        }
        None => [880.0, -1000.0],
    };
    let vertical_widths = descendant
        .get(&PdfName(b"W2".to_vec()))
        .map(|object| resolve_array(document, object, "CIDFont W2"))
        .transpose()?
        .map(|values| parse_cid_vertical_widths(&values, document.limits.max_array_items))
        .transpose()?
        .unwrap_or_default();
    let vertical = descendant
        .get(&PdfName(b"FontDescriptor".to_vec()))
        .map(|object| resolve_dictionary(document, object, "FontDescriptor"))
        .transpose()?
        .as_ref()
        .map(parse_vertical_metrics)
        .transpose()?
        .unwrap_or_default();
    Ok(FontMetrics::Cid {
        default_width,
        widths,
        writing_mode,
        vertical,
        default_vertical,
        vertical_widths,
    })
}

fn parse_cid_vertical_widths(
    values: &[PdfObject],
    limit: usize,
) -> PdfResult<BTreeMap<u32, CidVerticalMetrics>> {
    let mut metrics = BTreeMap::new();
    let mut index = 0;
    while index < values.len() {
        let start = integer_u32(&values[index])?;
        let next = values.get(index + 1).ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                "CIDFont W2 entry is truncated",
            )
        })?;
        index += 2;
        let (end, entries) = match next {
            PdfObject::Array(entries) => {
                if entries.len() % 3 != 0 {
                    return invalid("CIDFont W2 metrics must be complete triples");
                }
                (None, entries.as_slice())
            }
            PdfObject::Integer(_) => {
                let end = integer_u32(next)?;
                if end < start {
                    return invalid("CIDFont W2 range end precedes start");
                }
                let entries = values.get(index..index + 3).ok_or_else(|| {
                    PdfError::new(
                        ErrorCode::InvalidObject,
                        None,
                        "CIDFont W2 range is truncated",
                    )
                })?;
                index += 3;
                (Some(end), entries)
            }
            _ => return invalid("CIDFont W2 entry must be an array or range end"),
        };
        let count = end
            .map_or(Some(entries.len() / 3), |end| {
                usize::try_from(end - start)
                    .ok()
                    .and_then(|n| n.checked_add(1))
            })
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::LimitExceeded,
                    None,
                    "CIDFont W2 range size overflow",
                )
            })?;
        if count > limit.saturating_sub(metrics.len()) {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "CIDFont vertical metric limit exceeded",
            ));
        }
        for offset in 0..count {
            let triple = if end.is_some() {
                entries
            } else {
                &entries[offset * 3..offset * 3 + 3]
            };
            let code = u32::try_from(offset)
                .ok()
                .and_then(|offset| start.checked_add(offset))
                .ok_or_else(|| {
                    PdfError::new(ErrorCode::LimitExceeded, None, "CIDFont W2 code overflow")
                })?;
            metrics.insert(
                code,
                CidVerticalMetrics {
                    displacement: finite_number(&triple[0], "CIDFont W2 displacement")?,
                    position: [
                        finite_number(&triple[1], "CIDFont W2 x origin")?,
                        finite_number(&triple[2], "CIDFont W2 y origin")?,
                    ],
                },
            );
        }
    }
    Ok(metrics)
}

fn parse_vertical_metrics(dictionary: &PdfDictionary) -> PdfResult<FontVerticalMetrics> {
    let ascent = dictionary
        .get(&PdfName(b"Ascent".to_vec()))
        .map(|value| finite_number(value, "font Ascent"))
        .transpose()?;
    let descent = dictionary
        .get(&PdfName(b"Descent".to_vec()))
        .map(|value| finite_number(value, "font Descent"))
        .transpose()?;
    let (Some(ascent), Some(descent)) = (ascent, descent) else {
        return Ok(FontVerticalMetrics::default());
    };
    if ascent <= descent {
        return Ok(FontVerticalMetrics::default());
    }
    Ok(FontVerticalMetrics {
        ascent: ascent.clamp(
            -MAX_VERTICAL_METRIC_MAGNITUDE,
            MAX_VERTICAL_METRIC_MAGNITUDE,
        ),
        descent: descent.clamp(
            -MAX_VERTICAL_METRIC_MAGNITUDE,
            MAX_VERTICAL_METRIC_MAGNITUDE,
        ),
    })
}

fn parse_cid_widths(values: &[PdfObject], limit: usize) -> PdfResult<BTreeMap<u32, f64>> {
    let mut widths = BTreeMap::new();
    let mut index = 0_usize;
    while index < values.len() {
        let start = integer_u32(&values[index])?;
        index += 1;
        let Some(next) = values.get(index) else {
            return invalid("CIDFont W entry is incomplete");
        };
        match next {
            PdfObject::Array(entries) => {
                index += 1;
                for (offset, value) in entries.iter().enumerate() {
                    let code = start
                        .checked_add(u32::try_from(offset).map_err(|_| {
                            PdfError::new(
                                ErrorCode::LimitExceeded,
                                None,
                                "CIDFont width index overflow",
                            )
                        })?)
                        .ok_or_else(|| {
                            PdfError::new(
                                ErrorCode::LimitExceeded,
                                None,
                                "CIDFont width code overflow",
                            )
                        })?;
                    insert_width(
                        &mut widths,
                        code,
                        finite_number(value, "CIDFont width")?,
                        limit,
                    )?;
                }
            }
            PdfObject::Integer(_) => {
                let end = integer_u32(next)?;
                index += 1;
                let width = values
                    .get(index)
                    .ok_or_else(|| {
                        PdfError::new(
                            ErrorCode::InvalidObject,
                            None,
                            "CIDFont W range has no width",
                        )
                    })
                    .and_then(|value| finite_number(value, "CIDFont range width"))?;
                index += 1;
                if end < start {
                    return invalid("CIDFont W range end precedes start");
                }
                let count = usize::try_from(end - start)
                    .ok()
                    .and_then(|distance| distance.checked_add(1))
                    .ok_or_else(|| {
                        PdfError::new(
                            ErrorCode::LimitExceeded,
                            None,
                            "CIDFont W range size overflow",
                        )
                    })?;
                if count > limit.saturating_sub(widths.len()) {
                    return Err(PdfError::new(
                        ErrorCode::LimitExceeded,
                        None,
                        "CIDFont width mapping limit exceeded",
                    ));
                }
                for code in start..=end {
                    widths.insert(code, width);
                }
            }
            _ => return invalid("CIDFont W entry must be an array or range end"),
        }
    }
    Ok(widths)
}

fn insert_width(
    widths: &mut BTreeMap<u32, f64>,
    code: u32,
    width: f64,
    limit: usize,
) -> PdfResult<()> {
    if !widths.contains_key(&code) && widths.len() >= limit {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "CIDFont width mapping limit exceeded",
        ));
    }
    widths.insert(code, width);
    Ok(())
}

fn resolve_array(
    document: &PdfDocument,
    object: &PdfObject,
    label: &str,
) -> PdfResult<Vec<PdfObject>> {
    match resolve_object(document, object)? {
        PdfObject::Array(values) => Ok(values),
        _ => Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            format!("{label} must be an array"),
        )),
    }
}

fn resolve_dictionary(
    document: &PdfDocument,
    object: &PdfObject,
    label: &str,
) -> PdfResult<PdfDictionary> {
    resolve_object(document, object)?
        .as_dictionary()
        .cloned()
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                None,
                format!("{label} must be a dictionary"),
            )
        })
}

fn resolve_object(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfObject> {
    if let Some(id) = object.as_reference() {
        Ok(document.object(id)?.value)
    } else {
        Ok(object.clone())
    }
}

fn integer_u32(object: &PdfObject) -> PdfResult<u32> {
    let PdfObject::Integer(value) = object else {
        return invalid("font metric code must be an integer");
    };
    u32::try_from(*value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "font metric code is negative",
        )
    })
}

#[allow(clippy::cast_precision_loss)]
fn finite_number(object: &PdfObject, label: &str) -> PdfResult<f64> {
    let value = match object {
        PdfObject::Integer(value) => *value as f64,
        PdfObject::Real(value) => *value,
        _ => return invalid(&format!("{label} must be numeric")),
    };
    if value.is_finite() {
        Ok(value)
    } else {
        invalid(&format!("{label} must be finite"))
    }
}

fn invalid<T>(message: &str) -> PdfResult<T> {
    Err(PdfError::new(ErrorCode::InvalidObject, None, message))
}
