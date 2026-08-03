use std::collections::{BTreeMap, BTreeSet};

use encoding_rs::{MACINTOSH, WINDOWS_1252};

use crate::{
    ErrorCode, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    cmap::ToUnicodeCMap,
    font_metrics::{FontMetrics, FontVerticalMetrics},
    text_model::WritingMode,
};

#[derive(Debug, Clone)]
enum FontFallback {
    WinAnsi,
    MacRoman,
    ByteIdentity,
    CompositeIdentity,
}

#[derive(Debug, Clone)]
pub(crate) struct FontDecoder {
    pub base_name: Option<String>,
    to_unicode: Option<ToUnicodeCMap>,
    fallback: FontFallback,
    differences: BTreeMap<u8, String>,
    metrics: FontMetrics,
}

#[derive(Debug, Clone)]
pub(crate) struct DecodedGlyph {
    pub unicode: String,
    pub code: u32,
    pub missing_mapping: bool,
    pub invalid_mapping: bool,
    pub used_fallback: bool,
}

#[derive(Debug, Clone)]
pub(crate) struct DecodedText {
    pub text: String,
    pub glyphs: Vec<DecodedGlyph>,
    pub legacy_glyph_count: usize,
    pub missing_mappings: usize,
    pub invalid_mappings: usize,
    pub used_fallback: bool,
}

pub(crate) fn load_fonts_from_resources(
    document: &PdfDocument,
    resources: &PdfDictionary,
) -> PdfResult<BTreeMap<PdfName, FontDecoder>> {
    let Some(fonts_object) = resources.get(&PdfName(b"Font".to_vec())) else {
        return Ok(BTreeMap::new());
    };
    let fonts = resolve_dictionary(document, fonts_object)?;
    let mut output = BTreeMap::new();
    for (resource_name, font_object) in fonts {
        let dictionary = resolve_dictionary(document, &font_object)?;
        let decoder = FontDecoder::from_dictionary(document, &dictionary)?;
        output.insert(resource_name, decoder);
    }
    Ok(output)
}

impl FontDecoder {
    fn from_dictionary(document: &PdfDocument, dictionary: &PdfDictionary) -> PdfResult<Self> {
        let subtype = dictionary
            .get(&PdfName(b"Subtype".to_vec()))
            .and_then(|object| {
                if let PdfObject::Name(name) = object {
                    Some(name.as_bytes())
                } else {
                    None
                }
            })
            .unwrap_or_default();
        let base_name = dictionary
            .get(&PdfName(b"BaseFont".to_vec()))
            .and_then(|object| {
                if let PdfObject::Name(name) = object {
                    Some(String::from_utf8_lossy(name.as_bytes()).into_owned())
                } else {
                    None
                }
            });
        let to_unicode = dictionary
            .get(&PdfName(b"ToUnicode".to_vec()))
            .map(|object| load_to_unicode(document, object))
            .transpose()?;
        let is_type0 = subtype == b"Type0";
        let (fallback, differences) = if is_type0 {
            (FontFallback::CompositeIdentity, BTreeMap::new())
        } else {
            parse_simple_encoding(document, dictionary)?
        };
        Ok(Self {
            base_name,
            to_unicode,
            fallback,
            differences,
            metrics: FontMetrics::from_font(document, dictionary, is_type0)?,
        })
    }

    pub(crate) fn decode(&self, bytes: &[u8]) -> DecodedText {
        let glyphs = if let Some(cmap) = &self.to_unicode {
            cmap.decode_codes(bytes)
                .into_iter()
                .map(|code| DecodedGlyph {
                    code: big_endian_code(&code.source),
                    unicode: code.unicode,
                    missing_mapping: code.missing,
                    invalid_mapping: code.invalid,
                    used_fallback: false,
                })
                .collect()
        } else {
            match self.fallback {
                FontFallback::WinAnsi | FontFallback::MacRoman | FontFallback::ByteIdentity => {
                    bytes
                        .iter()
                        .map(|byte| {
                            let unicode = match self.fallback {
                                FontFallback::WinAnsi => {
                                    decode_single_byte(&[*byte], WINDOWS_1252, &self.differences)
                                }
                                FontFallback::MacRoman => {
                                    decode_single_byte(&[*byte], MACINTOSH, &self.differences)
                                }
                                FontFallback::ByteIdentity => self
                                    .differences
                                    .get(byte)
                                    .and_then(|name| glyph_name_to_string(name))
                                    .unwrap_or_else(|| {
                                        if byte.is_ascii() {
                                            char::from(*byte).to_string()
                                        } else {
                                            "\u{fffd}".to_owned()
                                        }
                                    }),
                                FontFallback::CompositeIdentity => unreachable!(),
                            };
                            DecodedGlyph {
                                code: u32::from(*byte),
                                missing_mapping: unicode == "\u{fffd}",
                                invalid_mapping: false,
                                unicode,
                                used_fallback: true,
                            }
                        })
                        .collect()
                }
                FontFallback::CompositeIdentity => bytes
                    .chunks(2)
                    .map(|pair| {
                        let (code, unicode, missing_mapping) = if let [high, low] = pair {
                            let code = u16::from_be_bytes([*high, *low]);
                            let unicode = char::from_u32(u32::from(code))
                                .map_or_else(|| "\u{fffd}".to_owned(), |value| value.to_string());
                            (u32::from(code), unicode.clone(), unicode == "\u{fffd}")
                        } else {
                            (u32::from(pair[0]), "\u{fffd}".to_owned(), true)
                        };
                        DecodedGlyph {
                            code,
                            unicode,
                            missing_mapping,
                            invalid_mapping: false,
                            used_fallback: true,
                        }
                    })
                    .collect(),
            }
        };
        decoded_text(glyphs, self.to_unicode.is_some())
    }

    pub(crate) fn glyph_width(&self, code: u32) -> f64 {
        self.metrics.width(code)
    }

    pub(crate) const fn writing_mode(&self) -> WritingMode {
        self.metrics.writing_mode()
    }

    pub(crate) const fn vertical_metrics(&self) -> FontVerticalMetrics {
        self.metrics.vertical_metrics()
    }

    pub(crate) fn word_spacing_applies(&self, code: u32) -> bool {
        matches!(self.metrics, FontMetrics::Simple { .. }) && code == 32
    }
}

fn decoded_text(glyphs: Vec<DecodedGlyph>, has_to_unicode: bool) -> DecodedText {
    let missing_mappings = glyphs.iter().filter(|glyph| glyph.missing_mapping).count();
    let invalid_mappings = glyphs.iter().filter(|glyph| glyph.invalid_mapping).count();
    let used_fallback = glyphs.iter().any(|glyph| glyph.used_fallback);
    let text = glyphs.iter().map(|glyph| glyph.unicode.as_str()).collect();
    let legacy_glyph_count = if has_to_unicode {
        glyphs
            .iter()
            .map(|glyph| glyph.unicode.chars().count())
            .sum()
    } else {
        glyphs.len()
    };
    DecodedText {
        text,
        glyphs,
        legacy_glyph_count,
        missing_mappings,
        invalid_mappings,
        used_fallback,
    }
}

fn big_endian_code(bytes: &[u8]) -> u32 {
    bytes.iter().fold(0_u32, |value, byte| {
        value.wrapping_shl(8) | u32::from(*byte)
    })
}

fn load_to_unicode(document: &PdfDocument, object: &PdfObject) -> PdfResult<ToUnicodeCMap> {
    let object_id = object.as_reference();
    let value = if let Some(id) = object_id {
        document.object(id)?.value
    } else {
        object.clone()
    };
    let PdfObject::Stream(stream) = value else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "ToUnicode must resolve to a stream",
        ));
    };
    let decoded = document.decode_stream(&stream)?;
    ToUnicodeCMap::parse(&decoded, &document.limits).map_err(|mut error| {
        if let Some(id) = object_id {
            error.message = format!(
                "ToUnicode object {} {} R: {}",
                id.number, id.generation, error.message
            );
        }
        error
    })
}

fn resolve_encoding_object(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfObject> {
    let mut current = object.clone();
    let mut seen = BTreeSet::new();
    let mut depth = 0_usize;
    loop {
        let Some(id) = current.as_reference() else {
            return Ok(current);
        };
        if depth >= document.limits.max_object_depth {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "font Encoding reference depth limit exceeded",
            ));
        }
        if !seen.insert(id) {
            return Err(PdfError::new(
                ErrorCode::InvalidReference,
                None,
                "cyclic font Encoding reference",
            ));
        }
        current = document.object(id)?.value;
        depth += 1;
    }
}

fn parse_simple_encoding(
    document: &PdfDocument,
    dictionary: &PdfDictionary,
) -> PdfResult<(FontFallback, BTreeMap<u8, String>)> {
    let Some(encoding) = dictionary.get(&PdfName(b"Encoding".to_vec())) else {
        return Ok((FontFallback::ByteIdentity, BTreeMap::new()));
    };
    let encoding = resolve_encoding_object(document, encoding)?;
    match &encoding {
        PdfObject::Name(name) => Ok((fallback_for_name(name), BTreeMap::new())),
        PdfObject::Dictionary(encoding_dictionary) => {
            let fallback = encoding_dictionary
                .get(&PdfName(b"BaseEncoding".to_vec()))
                .and_then(|object| {
                    if let PdfObject::Name(name) = object {
                        Some(fallback_for_name(name))
                    } else {
                        None
                    }
                })
                .unwrap_or(FontFallback::ByteIdentity);
            let differences = encoding_dictionary
                .get(&PdfName(b"Differences".to_vec()))
                .map(parse_differences)
                .transpose()?
                .unwrap_or_default();
            Ok((fallback, differences))
        }
        _ => Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "font Encoding must be a name or dictionary",
        )),
    }
}

fn fallback_for_name(name: &PdfName) -> FontFallback {
    match name.as_bytes() {
        b"WinAnsiEncoding" => FontFallback::WinAnsi,
        b"MacRomanEncoding" => FontFallback::MacRoman,
        _ => FontFallback::ByteIdentity,
    }
}

fn parse_differences(object: &PdfObject) -> PdfResult<BTreeMap<u8, String>> {
    let PdfObject::Array(values) = object else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "Encoding Differences must be an array",
        ));
    };
    let mut code = None;
    let mut differences = BTreeMap::new();
    for value in values {
        match value {
            PdfObject::Integer(value) => {
                code = Some(u8::try_from(*value).map_err(|_| {
                    PdfError::new(
                        ErrorCode::InvalidObject,
                        None,
                        "Encoding Differences code is out of byte range",
                    )
                })?);
            }
            PdfObject::Name(name) => {
                let current = code.ok_or_else(|| {
                    PdfError::new(
                        ErrorCode::InvalidObject,
                        None,
                        "Encoding Differences starts with a glyph name",
                    )
                })?;
                differences.insert(
                    current,
                    String::from_utf8_lossy(name.as_bytes()).into_owned(),
                );
                code = current.checked_add(1);
            }
            _ => {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    None,
                    "Encoding Differences entries must be integers or names",
                ));
            }
        }
    }
    Ok(differences)
}

fn decode_single_byte(
    bytes: &[u8],
    encoding: &'static encoding_rs::Encoding,
    differences: &BTreeMap<u8, String>,
) -> String {
    let mut output = String::new();
    let mut run = Vec::new();
    let flush = |run: &mut Vec<u8>, output: &mut String| {
        if !run.is_empty() {
            let (decoded, _, _) = encoding.decode(run);
            output.push_str(&decoded);
            run.clear();
        }
    };
    for byte in bytes {
        if let Some(name) = differences.get(byte) {
            flush(&mut run, &mut output);
            output.push_str(&glyph_name_to_string(name).unwrap_or_else(|| "\u{fffd}".to_owned()));
        } else {
            run.push(*byte);
        }
    }
    flush(&mut run, &mut output);
    output
}

fn glyph_name_to_string(name: &str) -> Option<String> {
    let base = name.split('.').next().unwrap_or(name);
    match base {
        "space" => Some(" ".to_owned()),
        "hyphen" => Some("-".to_owned()),
        "endash" => Some("–".to_owned()),
        "emdash" => Some("—".to_owned()),
        "quoteleft" | "quotesingle" => Some("'".to_owned()),
        "quoteright" => Some("’".to_owned()),
        "quotedbl" => Some("\"".to_owned()),
        "bullet" => Some("•".to_owned()),
        "fi" => Some("fi".to_owned()),
        "fl" => Some("fl".to_owned()),
        _ if base.chars().count() == 1 => Some(base.to_owned()),
        _ if base.starts_with("uni") && base.len() >= 7 && (base.len() - 3).is_multiple_of(4) => {
            let mut output = String::new();
            for chunk in base.as_bytes()[3..].chunks_exact(4) {
                let value = std::str::from_utf8(chunk)
                    .ok()
                    .and_then(|text| u32::from_str_radix(text, 16).ok())
                    .and_then(char::from_u32)?;
                output.push(value);
            }
            Some(output)
        }
        _ if base.starts_with('u') && (5..=7).contains(&base.len()) => {
            u32::from_str_radix(&base[1..], 16)
                .ok()
                .and_then(char::from_u32)
                .map(|character| character.to_string())
        }
        _ => None,
    }
}

fn resolve_dictionary(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfDictionary> {
    let value = if let Some(id) = object.as_reference() {
        document.object(id)?.value
    } else {
        object.clone()
    };
    value
        .as_dictionary()
        .cloned()
        .ok_or_else(|| PdfError::new(ErrorCode::InvalidObject, None, "expected font dictionary"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_adobe_glyph_names_and_differences() {
        assert_eq!(glyph_name_to_string("uni4e2d"), Some("中".to_owned()));
        assert_eq!(glyph_name_to_string("u1F600"), Some("😀".to_owned()));
        assert_eq!(glyph_name_to_string("fi"), Some("fi".to_owned()));
    }
}
