use std::collections::BTreeMap;

use crate::{
    ErrorCode, Lexer, ParseLimits, PdfDictionary, PdfError, PdfName, PdfObject, PdfResult,
    PdfStream, SpannedToken, Token, decode_budget::DecodeBudget, parser::ObjectParser,
};

/// Storage kind represented by a cross-reference entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum XrefKind {
    Free,
    InUse,
    Compressed,
}

/// One cross-reference entry.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct XrefEntry {
    pub offset: usize,
    pub generation: u16,
    pub kind: XrefKind,
    pub object_stream: Option<u32>,
    pub object_index: Option<u32>,
}

impl XrefEntry {
    const fn free(next_free: usize, generation: u16) -> Self {
        Self {
            offset: next_free,
            generation,
            kind: XrefKind::Free,
            object_stream: None,
            object_index: None,
        }
    }

    const fn in_use(offset: usize, generation: u16) -> Self {
        Self {
            offset,
            generation,
            kind: XrefKind::InUse,
            object_stream: None,
            object_index: None,
        }
    }

    const fn compressed(object_stream: u32, object_index: u32) -> Self {
        Self {
            offset: 0,
            generation: 0,
            kind: XrefKind::Compressed,
            object_stream: Some(object_stream),
            object_index: Some(object_index),
        }
    }
}

pub(crate) struct XrefSection {
    pub entries: BTreeMap<u32, XrefEntry>,
    pub trailer: PdfDictionary,
}

pub(crate) fn parse_xref_section(
    input: &[u8],
    offset: usize,
    limits: &ParseLimits,
    budget: &mut DecodeBudget,
) -> PdfResult<XrefSection> {
    let mut lexer = Lexer::with_limits(input, limits.clone());
    lexer.set_position(offset)?;
    let first = next_required(&mut lexer, "expected xref table or stream")?;
    if first.token == Token::Keyword(b"xref".to_vec()) {
        let mut section = parse_classic_xref(input, offset, limits)?;
        if let Some(xref_stream_offset) = dictionary_integer(&section.trailer, b"XRefStm") {
            let xref_stream_offset = usize::try_from(xref_stream_offset).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(offset),
                    "XRefStm offset is negative or out of range",
                )
            })?;
            if xref_stream_offset >= input.len() || xref_stream_offset == offset {
                return Err(PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(offset),
                    "XRefStm offset is outside the input or self-referential",
                ));
            }
            let hybrid = parse_xref_stream(input, xref_stream_offset, limits, budget)?;
            for (number, entry) in hybrid.entries {
                section.entries.insert(number, entry);
            }
        }
        Ok(section)
    } else {
        parse_xref_stream(input, offset, limits, budget)
    }
}

pub(crate) fn parse_classic_xref(
    input: &[u8],
    offset: usize,
    limits: &ParseLimits,
) -> PdfResult<XrefSection> {
    let mut lexer = Lexer::with_limits(input, limits.clone());
    lexer.set_position(offset)?;
    let header = next_required(&mut lexer, "expected xref keyword")?;
    if header.token != Token::Keyword(b"xref".to_vec()) {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(offset),
            "classic xref table expected",
        ));
    }

    let mut entries = BTreeMap::new();
    loop {
        let token = next_required(&mut lexer, "expected xref subsection or trailer")?;
        if token.token == Token::Keyword(b"trailer".to_vec()) {
            let mut object_parser = ObjectParser::at(input, lexer.position(), limits.clone())?;
            let trailer_object = object_parser.parse_value(0)?;
            let PdfObject::Dictionary(trailer) = trailer_object else {
                return Err(PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(token.end),
                    "trailer must be a dictionary",
                ));
            };
            return Ok(XrefSection { entries, trailer });
        }

        let first_object = token_to_u32(&token, "xref subsection start")?;
        let count_token = next_required(&mut lexer, "expected xref subsection count")?;
        let count = token_to_usize(&count_token, "xref subsection count")?;
        enforce_entry_limit(entries.len(), count, limits, count_token.start)?;

        for index in 0..count {
            let object_number = first_object
                .checked_add(u32::try_from(index).map_err(|_| {
                    PdfError::new(
                        ErrorCode::LimitExceeded,
                        Some(token.start),
                        "xref object number overflow",
                    )
                })?)
                .ok_or_else(|| {
                    PdfError::new(
                        ErrorCode::LimitExceeded,
                        Some(token.start),
                        "xref object number overflow",
                    )
                })?;
            let offset_token = next_required(&mut lexer, "expected xref entry offset")?;
            let generation_token = next_required(&mut lexer, "expected xref entry generation")?;
            let status_token = next_required(&mut lexer, "expected xref entry status")?;
            let entry_offset = token_to_usize(&offset_token, "xref entry offset")?;
            let generation = token_to_u16(&generation_token, "xref entry generation")?;
            let entry = match status_token.token {
                Token::Keyword(ref status) if status == b"n" => {
                    if entry_offset >= input.len() {
                        return Err(PdfError::new(
                            ErrorCode::InvalidXref,
                            Some(offset_token.start),
                            "in-use xref offset is outside the input",
                        ));
                    }
                    XrefEntry::in_use(entry_offset, generation)
                }
                Token::Keyword(ref status) if status == b"f" => {
                    XrefEntry::free(entry_offset, generation)
                }
                _ => {
                    return Err(PdfError::new(
                        ErrorCode::InvalidXref,
                        Some(status_token.start),
                        "xref status must be n or f",
                    ));
                }
            };
            entries.insert(object_number, entry);
        }
    }
}

fn parse_xref_stream(
    input: &[u8],
    offset: usize,
    limits: &ParseLimits,
    budget: &mut DecodeBudget,
) -> PdfResult<XrefSection> {
    let parser = ObjectParser::at(input, offset, limits.clone())?;
    let indirect = parser.parse_indirect()?;
    let PdfObject::Stream(stream) = indirect.value else {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(offset),
            "startxref does not point to an xref table or stream",
        ));
    };
    if !matches!(
        stream.dictionary.get(&PdfName(b"Type".to_vec())),
        Some(PdfObject::Name(name)) if name.is(b"XRef")
    ) {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(offset),
            "xref stream dictionary must have /Type /XRef",
        ));
    }
    let structural_limit = xref_stream_structural_limit(&stream.dictionary, limits)?;
    let decoded = crate::filter::decode_stream_with_structural_budget(
        &stream,
        limits,
        structural_limit,
        budget,
    )?;
    let entries = decode_xref_entries(&stream, &decoded, input.len(), limits)?;
    Ok(XrefSection {
        entries,
        trailer: stream.dictionary,
    })
}

fn xref_stream_structural_limit(
    dictionary: &PdfDictionary,
    limits: &ParseLimits,
) -> PdfResult<usize> {
    let widths = integer_array(dictionary, b"W")?
        .ok_or_else(|| PdfError::new(ErrorCode::InvalidXref, None, "xref stream has no W array"))?;
    if widths.len() != 3 || widths.iter().any(|width| *width > 8) {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream has invalid field widths",
        ));
    }
    let row_bytes = widths.iter().try_fold(0_usize, |total, width| {
        total.checked_add(*width).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "xref stream row width overflow",
            )
        })
    })?;
    if row_bytes == 0 {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream row width cannot be zero",
        ));
    }

    let size = dictionary_usize(dictionary, b"Size")?.ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidTrailer,
            None,
            "xref stream has no integer Size",
        )
    })?;
    let index = integer_array(dictionary, b"Index")?.unwrap_or_else(|| vec![0, size]);
    if !index.len().is_multiple_of(2) {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream Index must contain start/count pairs",
        ));
    }
    let entry_count = index.chunks_exact(2).try_fold(0_usize, |total, pair| {
        total.checked_add(pair[1]).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "xref stream entry count overflow",
            )
        })
    })?;
    enforce_entry_limit(0, entry_count, limits, 0)?;
    let expected_bytes = entry_count.checked_mul(row_bytes).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "xref stream decoded size overflow",
        )
    })?;
    let structural_limit = expected_bytes.checked_mul(2).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "xref stream structural decode budget overflow",
        )
    })?;
    if structural_limit > limits.max_decoded_stream_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "xref stream structural decode budget exceeds configured limit",
        ));
    }
    Ok(structural_limit)
}

#[allow(clippy::too_many_lines)] // Keeping field validation in wire order makes malformed offsets auditable.
fn decode_xref_entries(
    stream: &PdfStream,
    decoded: &[u8],
    input_len: usize,
    limits: &ParseLimits,
) -> PdfResult<BTreeMap<u32, XrefEntry>> {
    let widths = integer_array(&stream.dictionary, b"W")?
        .ok_or_else(|| PdfError::new(ErrorCode::InvalidXref, None, "xref stream has no W array"))?;
    if widths.len() != 3 {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream W array must have three integers",
        ));
    }
    if widths.iter().any(|width| *width > 8) {
        return Err(PdfError::new(
            ErrorCode::UnsupportedFeature,
            None,
            "xref stream field widths above eight bytes are unsupported",
        ));
    }
    let row_bytes = widths.iter().try_fold(0_usize, |total, width| {
        total.checked_add(*width).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "xref stream row width overflow",
            )
        })
    })?;
    if row_bytes == 0 {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream row width cannot be zero",
        ));
    }

    let size = dictionary_usize(&stream.dictionary, b"Size")?.ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidTrailer,
            None,
            "xref stream has no integer Size",
        )
    })?;
    let index = integer_array(&stream.dictionary, b"Index")?.unwrap_or_else(|| vec![0, size]);
    if index.len() % 2 != 0 {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "xref stream Index must contain start/count pairs",
        ));
    }
    let entry_count = index.chunks_exact(2).try_fold(0_usize, |total, pair| {
        total.checked_add(pair[1]).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "xref stream entry count overflow",
            )
        })
    })?;
    enforce_entry_limit(0, entry_count, limits, 0)?;
    let expected_bytes = entry_count.checked_mul(row_bytes).ok_or_else(|| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "xref stream decoded size overflow",
        )
    })?;
    if decoded.len() != expected_bytes {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            format!(
                "xref stream decoded length {} does not match expected {expected_bytes}",
                decoded.len()
            ),
        ));
    }

    let mut entries = BTreeMap::new();
    let mut rows = decoded.chunks_exact(row_bytes);
    for pair in index.chunks_exact(2) {
        let first = u32::try_from(pair[0]).map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidXref,
                None,
                "xref stream object number is out of range",
            )
        })?;
        for relative in 0..pair[1] {
            let number = first
                .checked_add(u32::try_from(relative).map_err(|_| {
                    PdfError::new(
                        ErrorCode::LimitExceeded,
                        None,
                        "xref stream object number overflow",
                    )
                })?)
                .ok_or_else(|| {
                    PdfError::new(
                        ErrorCode::LimitExceeded,
                        None,
                        "xref stream object number overflow",
                    )
                })?;
            let row = rows.next().expect("decoded length checked");
            let mut position = 0_usize;
            let field0 = read_field(row, &mut position, widths[0], 1);
            let field1 = read_field(row, &mut position, widths[1], 0);
            let field2 = read_field(row, &mut position, widths[2], 0);
            let entry = match field0 {
                0 => XrefEntry::free(
                    usize::try_from(field1).map_err(|_| {
                        PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "free-list object number is out of range",
                        )
                    })?,
                    u16::try_from(field2).map_err(|_| {
                        PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "free xref generation is out of range",
                        )
                    })?,
                ),
                1 => {
                    let entry_offset = usize::try_from(field1).map_err(|_| {
                        PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "xref stream byte offset is out of range",
                        )
                    })?;
                    if entry_offset >= input_len {
                        return Err(PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "xref stream in-use offset is outside the input",
                        ));
                    }
                    XrefEntry::in_use(
                        entry_offset,
                        u16::try_from(field2).map_err(|_| {
                            PdfError::new(
                                ErrorCode::InvalidXref,
                                None,
                                "xref stream generation is out of range",
                            )
                        })?,
                    )
                }
                2 => XrefEntry::compressed(
                    u32::try_from(field1).map_err(|_| {
                        PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "object stream number is out of range",
                        )
                    })?,
                    u32::try_from(field2).map_err(|_| {
                        PdfError::new(
                            ErrorCode::InvalidXref,
                            None,
                            "object stream index is out of range",
                        )
                    })?,
                ),
                _ => {
                    return Err(PdfError::new(
                        ErrorCode::UnsupportedFeature,
                        None,
                        format!("unsupported xref stream entry type {field0}"),
                    ));
                }
            };
            entries.insert(number, entry);
        }
    }
    Ok(entries)
}

fn integer_array(dictionary: &PdfDictionary, name: &[u8]) -> PdfResult<Option<Vec<usize>>> {
    let Some(object) = dictionary.get(&PdfName(name.to_vec())) else {
        return Ok(None);
    };
    let PdfObject::Array(values) = object else {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            None,
            format!("{} must be an array", String::from_utf8_lossy(name)),
        ));
    };
    values
        .iter()
        .map(|value| {
            let PdfObject::Integer(value) = value else {
                return Err(PdfError::new(
                    ErrorCode::InvalidXref,
                    None,
                    format!("{} entries must be integers", String::from_utf8_lossy(name)),
                ));
            };
            usize::try_from(*value).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidXref,
                    None,
                    format!(
                        "{} entry is negative or out of range",
                        String::from_utf8_lossy(name)
                    ),
                )
            })
        })
        .collect::<PdfResult<Vec<_>>>()
        .map(Some)
}

fn dictionary_integer(dictionary: &PdfDictionary, name: &[u8]) -> Option<i64> {
    dictionary
        .get(&PdfName(name.to_vec()))
        .and_then(PdfObject::as_integer)
}

fn dictionary_usize(dictionary: &PdfDictionary, name: &[u8]) -> PdfResult<Option<usize>> {
    dictionary_integer(dictionary, name)
        .map(|value| {
            usize::try_from(value).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidXref,
                    None,
                    format!(
                        "{} is negative or out of range",
                        String::from_utf8_lossy(name)
                    ),
                )
            })
        })
        .transpose()
}

fn read_field(row: &[u8], position: &mut usize, width: usize, default: u64) -> u64 {
    if width == 0 {
        return default;
    }
    let mut value = 0_u64;
    for byte in &row[*position..*position + width] {
        value = (value << 8) | u64::from(*byte);
    }
    *position += width;
    value
}

fn enforce_entry_limit(
    current: usize,
    additional: usize,
    limits: &ParseLimits,
    offset: usize,
) -> PdfResult<()> {
    if additional > limits.max_xref_entries
        || current.saturating_add(additional) > limits.max_xref_entries
    {
        Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(offset),
            "xref entry limit exceeded",
        ))
    } else {
        Ok(())
    }
}

fn next_required(lexer: &mut Lexer<'_>, message: &str) -> PdfResult<SpannedToken> {
    lexer.next_token()?.ok_or_else(|| {
        PdfError::new(
            ErrorCode::UnexpectedEof,
            Some(lexer.position()),
            message.to_owned(),
        )
    })
}

fn token_to_u32(token: &SpannedToken, label: &str) -> PdfResult<u32> {
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    u32::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} is out of range"),
        )
    })
}

fn token_to_u16(token: &SpannedToken, label: &str) -> PdfResult<u16> {
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    u16::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} is out of range"),
        )
    })
}

fn token_to_usize(token: &SpannedToken, label: &str) -> PdfResult<usize> {
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    usize::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidXref,
            Some(token.start),
            format!("{label} is out of range"),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_classic_xref_and_trailer() {
        let input =
            b"xref\n0 2\n0000000000 65535 f\n0000000009 00000 n\ntrailer << /Size 2 /Root 1 0 R >>";
        let section = parse_classic_xref(input, 0, &ParseLimits::default()).expect("valid xref");
        assert_eq!(section.entries[&1].offset, 9);
        assert_eq!(
            section
                .trailer
                .get(&PdfName(b"Root".to_vec()))
                .and_then(PdfObject::as_reference),
            Some(crate::ObjectId::new(1, 0))
        );
    }
}
