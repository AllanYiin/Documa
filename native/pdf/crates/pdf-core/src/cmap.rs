use std::collections::{BTreeMap, BTreeSet};

use crate::{ErrorCode, ParseLimits, PdfError, PdfResult};

#[derive(Debug, Clone, PartialEq, Eq)]
enum CMapToken {
    Integer(usize),
    Hex(Vec<u8>),
    Word(Vec<u8>),
    StartArray,
    EndArray,
}

/// Parsed subset of a `ToUnicode` `CMap` needed for text extraction.
#[derive(Debug, Clone, Default)]
pub(crate) struct ToUnicodeCMap {
    mappings: BTreeMap<Vec<u8>, String>,
    invalid_sources: BTreeSet<Vec<u8>>,
    code_lengths: Vec<usize>,
}

#[derive(Debug, Clone)]
pub(crate) struct DecodedCMapCode {
    pub source: Vec<u8>,
    pub unicode: String,
    pub missing: bool,
    pub invalid: bool,
}

impl ToUnicodeCMap {
    pub(crate) fn parse(input: &[u8], limits: &ParseLimits) -> PdfResult<Self> {
        let tokens = tokenize(input, limits)?;
        let mut cmap = Self::default();
        let mut index = 0_usize;
        while index + 1 < tokens.len() {
            let CMapToken::Integer(count) = tokens[index] else {
                index += 1;
                continue;
            };
            let CMapToken::Word(ref operator) = tokens[index + 1] else {
                index += 1;
                continue;
            };
            index += 2;
            match operator.as_slice() {
                b"begincodespacerange" => {
                    for _ in 0..count {
                        let start = expect_hex(&tokens, &mut index, "codespace start")?;
                        let end = expect_hex(&tokens, &mut index, "codespace end")?;
                        if start.len() != end.len() || start.is_empty() {
                            return Err(PdfError::new(
                                ErrorCode::InvalidObject,
                                None,
                                "ToUnicode codespace endpoints must have equal nonzero lengths",
                            ));
                        }
                        cmap.code_lengths.push(start.len());
                    }
                }
                b"beginbfchar" => {
                    for _ in 0..count {
                        let source = expect_hex(&tokens, &mut index, "bfchar source")?;
                        let destination = expect_hex(&tokens, &mut index, "bfchar destination")?;
                        cmap.insert_utf16(source, &destination, limits)?;
                    }
                }
                b"beginbfrange" => {
                    for _ in 0..count {
                        let start = expect_hex(&tokens, &mut index, "bfrange start")?;
                        let end = expect_hex(&tokens, &mut index, "bfrange end")?;
                        if start.len() != end.len() {
                            return Err(PdfError::new(
                                ErrorCode::InvalidObject,
                                None,
                                "bfrange source endpoints have different lengths",
                            ));
                        }
                        let range_count = inclusive_range_count(&start, &end)?;
                        match tokens.get(index) {
                            Some(CMapToken::Hex(destination)) => {
                                index += 1;
                                for offset in 0..range_count {
                                    let source = add_big_endian(&start, offset)?;
                                    let unicode_bytes = add_big_endian(destination, offset)?;
                                    cmap.insert_utf16(source, &unicode_bytes, limits)?;
                                }
                            }
                            Some(CMapToken::StartArray) => {
                                index += 1;
                                for offset in 0..range_count {
                                    let destination =
                                        expect_hex(&tokens, &mut index, "bfrange array value")?;
                                    cmap.insert_utf16(
                                        add_big_endian(&start, offset)?,
                                        &destination,
                                        limits,
                                    )?;
                                }
                                if tokens.get(index) != Some(&CMapToken::EndArray) {
                                    return Err(PdfError::new(
                                        ErrorCode::InvalidObject,
                                        None,
                                        "unterminated bfrange destination array",
                                    ));
                                }
                                index += 1;
                            }
                            _ => {
                                return Err(PdfError::new(
                                    ErrorCode::InvalidObject,
                                    None,
                                    "bfrange destination must be a hex string or array",
                                ));
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        cmap.finalize_code_lengths();
        Ok(cmap)
    }

    fn finalize_code_lengths(&mut self) {
        if self.code_lengths.is_empty() {
            self.code_lengths.extend(
                self.mappings
                    .keys()
                    .chain(self.invalid_sources.iter())
                    .map(Vec::len)
                    .collect::<BTreeSet<_>>(),
            );
        }
        self.code_lengths
            .sort_unstable_by(|left, right| right.cmp(left));
        self.code_lengths.dedup();
    }

    #[cfg(test)]
    pub(crate) fn decode(&self, bytes: &[u8]) -> (String, usize) {
        let codes = self.decode_codes(bytes);
        let missing = codes.iter().filter(|code| code.missing).count();
        let text = codes.into_iter().map(|code| code.unicode).collect();
        (text, missing)
    }

    pub(crate) fn decode_codes(&self, bytes: &[u8]) -> Vec<DecodedCMapCode> {
        let mut output = Vec::new();
        let mut position = 0_usize;
        while position < bytes.len() {
            let decoded = self.code_lengths.iter().find_map(|length| {
                let code = bytes.get(position..position.saturating_add(*length))?;
                if let Some(text) = self.mappings.get(code) {
                    Some((*length, code.to_vec(), text.clone(), false))
                } else if self.invalid_sources.contains(code) {
                    Some((*length, code.to_vec(), "\u{fffd}".to_owned(), true))
                } else {
                    None
                }
            });
            if let Some((length, source, unicode, invalid)) = decoded {
                output.push(DecodedCMapCode {
                    source,
                    unicode,
                    missing: false,
                    invalid,
                });
                position += length;
            } else {
                let length = self
                    .code_lengths
                    .iter()
                    .copied()
                    .filter(|length| position + length <= bytes.len())
                    .min()
                    .unwrap_or(1);
                let end = position.saturating_add(length).min(bytes.len());
                output.push(DecodedCMapCode {
                    source: bytes[position..end].to_vec(),
                    unicode: "\u{fffd}".to_owned(),
                    missing: true,
                    invalid: false,
                });
                position = end;
            }
        }
        output
    }

    fn insert_utf16(
        &mut self,
        source: Vec<u8>,
        destination: &[u8],
        limits: &ParseLimits,
    ) -> PdfResult<()> {
        match decode_utf16_be(destination) {
            Ok(decoded) => self.insert(source, decoded, limits),
            Err(error) if error.code == ErrorCode::InvalidObject => {
                self.insert_invalid(source, limits)
            }
            Err(error) => Err(error),
        }
    }

    fn insert(
        &mut self,
        source: Vec<u8>,
        destination: String,
        limits: &ParseLimits,
    ) -> PdfResult<()> {
        self.ensure_mapping_capacity(&source, limits)?;
        self.code_lengths.push(source.len());
        self.invalid_sources.remove(&source);
        self.mappings.insert(source, destination);
        Ok(())
    }

    fn insert_invalid(&mut self, source: Vec<u8>, limits: &ParseLimits) -> PdfResult<()> {
        self.ensure_mapping_capacity(&source, limits)?;
        self.code_lengths.push(source.len());
        self.mappings.remove(&source);
        self.invalid_sources.insert(source);
        Ok(())
    }

    fn ensure_mapping_capacity(&self, source: &[u8], limits: &ParseLimits) -> PdfResult<()> {
        let already_known =
            self.mappings.contains_key(source) || self.invalid_sources.contains(source);
        let mapping_count = self
            .mappings
            .len()
            .saturating_add(self.invalid_sources.len());
        if !already_known && mapping_count >= limits.max_cmap_mappings {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "ToUnicode mapping limit exceeded",
            ));
        }
        Ok(())
    }
}

fn tokenize(input: &[u8], limits: &ParseLimits) -> PdfResult<Vec<CMapToken>> {
    let mut tokens = Vec::new();
    let mut position = 0_usize;
    while position < input.len() {
        match input[position] {
            byte if is_space(byte) => position += 1,
            b'%' => {
                while position < input.len() && !matches!(input[position], b'\r' | b'\n') {
                    position += 1;
                }
            }
            b'[' => {
                tokens.push(CMapToken::StartArray);
                position += 1;
            }
            b']' => {
                tokens.push(CMapToken::EndArray);
                position += 1;
            }
            b'<' if input.get(position + 1) == Some(&b'<') => {
                position += 2;
            }
            b'>' if input.get(position + 1) == Some(&b'>') => {
                position += 2;
            }
            b'<' => {
                let (bytes, end) = read_hex(input, position, limits)?;
                tokens.push(CMapToken::Hex(bytes));
                position = end;
            }
            b'/' => {
                position += 1;
                while position < input.len()
                    && !is_space(input[position])
                    && !is_delimiter(input[position])
                {
                    position += 1;
                }
            }
            byte if byte.is_ascii_digit() => {
                let start = position;
                while input.get(position).is_some_and(u8::is_ascii_digit) {
                    position += 1;
                }
                let value = std::str::from_utf8(&input[start..position])
                    .ok()
                    .and_then(|text| text.parse::<usize>().ok())
                    .ok_or_else(|| {
                        PdfError::new(
                            ErrorCode::InvalidObject,
                            Some(start),
                            "CMap integer is out of range",
                        )
                    })?;
                tokens.push(CMapToken::Integer(value));
            }
            byte if is_delimiter(byte) => position += 1,
            _ => {
                let start = position;
                while position < input.len()
                    && !is_space(input[position])
                    && !is_delimiter(input[position])
                {
                    position += 1;
                }
                tokens.push(CMapToken::Word(input[start..position].to_vec()));
            }
        }
        if tokens.len() > limits.max_content_operations {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(position),
                "CMap token limit exceeded",
            ));
        }
    }
    Ok(tokens)
}

fn read_hex(input: &[u8], start: usize, limits: &ParseLimits) -> PdfResult<(Vec<u8>, usize)> {
    let mut output = Vec::new();
    let mut high = None;
    let mut position = start + 1;
    while let Some(&byte) = input.get(position) {
        position += 1;
        if byte == b'>' {
            if let Some(high) = high {
                output.push(high * 16);
            }
            return Ok((output, position));
        }
        if is_space(byte) {
            continue;
        }
        let value = hex_value(byte).ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidHex,
                Some(position - 1),
                "invalid CMap hexadecimal digit",
            )
        })?;
        if let Some(high) = high.take() {
            output.push(high * 16 + value);
            if output.len() > limits.max_string_bytes {
                return Err(PdfError::new(
                    ErrorCode::LimitExceeded,
                    Some(start),
                    "CMap hex string limit exceeded",
                ));
            }
        } else {
            high = Some(value);
        }
    }
    Err(PdfError::new(
        ErrorCode::UnexpectedEof,
        Some(start),
        "unterminated CMap hex string",
    ))
}

fn expect_hex(tokens: &[CMapToken], index: &mut usize, label: &str) -> PdfResult<Vec<u8>> {
    let Some(CMapToken::Hex(bytes)) = tokens.get(*index) else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            format!("{label} must be a hex string"),
        ));
    };
    *index += 1;
    Ok(bytes.clone())
}

fn decode_utf16_be(bytes: &[u8]) -> PdfResult<String> {
    let bytes = bytes.strip_prefix(&[0xfe, 0xff]).unwrap_or(bytes);
    if !bytes.len().is_multiple_of(2) {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "ToUnicode destination has odd UTF-16BE length",
        ));
    }
    let units = bytes
        .chunks_exact(2)
        .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
        .collect::<Vec<_>>();
    String::from_utf16(&units).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "ToUnicode destination contains invalid UTF-16",
        )
    })
}

fn inclusive_range_count(start: &[u8], end: &[u8]) -> PdfResult<usize> {
    let start_value = big_endian_to_u64(start)?;
    let end_value = big_endian_to_u64(end)?;
    let distance = end_value.checked_sub(start_value).ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "CMap range end precedes start",
        )
    })?;
    usize::try_from(distance)
        .ok()
        .and_then(|distance| distance.checked_add(1))
        .ok_or_else(|| PdfError::new(ErrorCode::LimitExceeded, None, "CMap range size overflow"))
}

fn add_big_endian(bytes: &[u8], increment: usize) -> PdfResult<Vec<u8>> {
    let value = big_endian_to_u64(bytes)?;
    let increment = u64::try_from(increment).map_err(|_| {
        PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "CMap range increment is out of range",
        )
    })?;
    let value = value.checked_add(increment).ok_or_else(|| {
        PdfError::new(ErrorCode::LimitExceeded, None, "CMap range value overflow")
    })?;
    let encoded = value.to_be_bytes();
    if bytes.len() > encoded.len() {
        return Err(PdfError::new(
            ErrorCode::UnsupportedFeature,
            None,
            "CMap codes above eight bytes are unsupported",
        ));
    }
    let start = encoded.len() - bytes.len();
    if encoded[..start].iter().any(|byte| *byte != 0) {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "CMap range increment exceeds code width",
        ));
    }
    Ok(encoded[start..].to_vec())
}

fn big_endian_to_u64(bytes: &[u8]) -> PdfResult<u64> {
    if bytes.len() > 8 {
        return Err(PdfError::new(
            ErrorCode::UnsupportedFeature,
            None,
            "CMap codes above eight bytes are unsupported",
        ));
    }
    Ok(bytes
        .iter()
        .fold(0_u64, |value, byte| (value << 8) | u64::from(*byte)))
}

const fn is_space(byte: u8) -> bool {
    matches!(byte, 0x00 | b'\t' | b'\n' | 0x0c | b'\r' | b' ')
}

const fn is_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'[' | b']' | b'<' | b'>' | b'{' | b'}' | b'/' | b'(' | b')'
    )
}

const fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_bfchar_bfrange_and_surrogate_pairs() {
        let cmap = ToUnicodeCMap::parse(
            br"
                1 begincodespacerange <00> <ff> endcodespacerange
                2 beginbfchar <01> <0041> <02> <d83dde00> endbfchar
                1 beginbfrange <10> <12> <0061> endbfrange
            ",
            &ParseLimits::default(),
        )
        .expect("valid ToUnicode");
        let (text, missing) = cmap.decode(&[1, 2, 0x10, 0x11, 0x12, 0xff]);
        assert_eq!(text, "A😀abc\u{fffd}");
        assert_eq!(missing, 1);
    }

    #[test]
    fn distinguishes_invalid_destinations_from_missing_sources() {
        let cmap = ToUnicodeCMap::parse(
            br"
                1 begincodespacerange <00> <ff> endcodespacerange
                1 beginbfchar <01> <d800> endbfchar
            ",
            &ParseLimits::default(),
        )
        .expect("recoverable invalid destination");
        let codes = cmap.decode_codes(&[1, 2]);

        assert_eq!(codes[0].unicode, "\u{fffd}");
        assert!(codes[0].invalid);
        assert!(!codes[0].missing);
        assert_eq!(codes[1].unicode, "\u{fffd}");
        assert!(!codes[1].invalid);
        assert!(codes[1].missing);
    }
}
