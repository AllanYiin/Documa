//! Bounded PDF filter codecs (ISO 32000-1, sections 7.4.2–7.4.5).
//! <https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf>

use crate::{ErrorCode, PdfError, PdfResult, decode_budget::DecodeBudget};

fn invalid(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidStream, None, message)
}

fn append(
    output: &mut Vec<u8>,
    bytes: &[u8],
    maximum: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<()> {
    if bytes.len() > maximum.saturating_sub(output.len()) {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "filter output limit or expansion ratio exceeded",
        ));
    }
    budget.charge(bytes.len())?;
    output.extend_from_slice(bytes);
    Ok(())
}

const fn whitespace(byte: u8) -> bool {
    matches!(byte, 0 | b'\t' | b'\n' | 12 | b'\r' | b' ')
}

pub(crate) fn ascii_hex(
    input: &[u8],
    maximum: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let mut output = Vec::new();
    let mut high = None;
    for &byte in input {
        if whitespace(byte) {
            continue;
        }
        if byte == b'>' {
            if let Some(nibble) = high {
                append(&mut output, &[nibble << 4], maximum, budget)?;
            }
            return Ok(output);
        }
        let value = match byte {
            b'0'..=b'9' => byte - b'0',
            b'a'..=b'f' => byte - b'a' + 10,
            b'A'..=b'F' => byte - b'A' + 10,
            _ => return Err(invalid("ASCIIHexDecode contains a non-hexadecimal byte")),
        };
        if let Some(nibble) = high.take() {
            append(&mut output, &[(nibble << 4) | value], maximum, budget)?;
        } else {
            high = Some(value);
        }
    }
    Err(invalid("ASCIIHexDecode is missing its EOD marker"))
}

pub(crate) fn ascii85(
    input: &[u8],
    maximum: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let mut output = Vec::new();
    let mut value = 0_u32;
    let mut count = 0_usize;
    let mut bytes = input.iter().copied();
    while let Some(byte) = bytes.next() {
        if whitespace(byte) {
            continue;
        }
        match byte {
            b'z' if count == 0 => append(&mut output, &[0; 4], maximum, budget)?,
            b'!'..=b'u' => {
                value = value
                    .checked_mul(85)
                    .and_then(|value| value.checked_add(u32::from(byte - b'!')))
                    .ok_or_else(|| invalid("ASCII85Decode tuple overflows 32 bits"))?;
                count += 1;
                if count == 5 {
                    append(&mut output, &value.to_be_bytes(), maximum, budget)?;
                    value = 0;
                    count = 0;
                }
            }
            b'~' => {
                if bytes.next() != Some(b'>') {
                    return Err(invalid("ASCII85Decode requires a contiguous ~> EOD marker"));
                }
                if count == 1 {
                    return Err(invalid("ASCII85Decode final tuple has only one character"));
                }
                if count > 1 {
                    for _ in count..5 {
                        value = value
                            .checked_mul(85)
                            .and_then(|value| value.checked_add(84))
                            .ok_or_else(|| {
                                invalid("ASCII85Decode final tuple overflows 32 bits")
                            })?;
                    }
                    append(
                        &mut output,
                        &value.to_be_bytes()[..count - 1],
                        maximum,
                        budget,
                    )?;
                }
                return Ok(output);
            }
            _ => {
                return Err(invalid(
                    "ASCII85Decode contains an invalid byte or misplaced z",
                ));
            }
        }
    }
    Err(invalid("ASCII85Decode is missing its EOD marker"))
}

pub(crate) fn run_length(
    input: &[u8],
    maximum: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    let mut output = Vec::new();
    let mut cursor = 0;
    while let Some(&length) = input.get(cursor) {
        cursor += 1;
        match length {
            128 => return Ok(output),
            0..=127 => {
                let count = usize::from(length) + 1;
                let bytes = input
                    .get(cursor..cursor + count)
                    .ok_or_else(|| invalid("RunLengthDecode literal run is truncated"))?;
                append(&mut output, bytes, maximum, budget)?;
                cursor += count;
            }
            _ => {
                let &byte = input
                    .get(cursor)
                    .ok_or_else(|| invalid("RunLengthDecode repeat run is truncated"))?;
                cursor += 1;
                append(
                    &mut output,
                    &[byte; 128][..257 - usize::from(length)],
                    maximum,
                    budget,
                )?;
            }
        }
    }
    Err(invalid("RunLengthDecode is missing its EOD marker"))
}

// LZW keeps fixed-size prefix/suffix tables and a fixed-size expansion scratch
// buffer. No input can grow the dictionary beyond 4096 entries or recurse.
pub(crate) fn lzw(
    input: &[u8],
    early_change: usize,
    maximum: usize,
    budget: &mut DecodeBudget,
) -> PdfResult<Vec<u8>> {
    if early_change > 1 {
        return Err(invalid("LZWDecode EarlyChange must be 0 or 1"));
    }
    let mut prefixes = [0_u16; 4096];
    let mut suffixes = [0_u8; 4096];
    let mut scratch = [0_u8; 4096];
    let mut output = Vec::new();
    let mut cursor = 0_usize;
    let mut width = 9;
    let mut next = 258;
    let mut previous = None;
    let mut initialized = false;
    loop {
        let code = read_code(input, &mut cursor, width)?;
        if !initialized && code != 256 {
            return Err(invalid("LZWDecode must begin with a clear-table code"));
        }
        initialized = true;
        match code {
            256 => {
                width = 9;
                next = 258;
                previous = None;
                continue;
            }
            257 => return Ok(output),
            _ => {}
        }
        let special = code == next;
        let expansion_code = if special {
            previous.ok_or_else(|| invalid("LZWDecode references an undefined initial code"))?
        } else if code < next {
            code
        } else {
            return Err(invalid("LZWDecode references an undefined table entry"));
        };
        let start = expand(expansion_code, &prefixes, &suffixes, &mut scratch)?;
        let first = scratch[start];
        append(&mut output, &scratch[start..], maximum, budget)?;
        if special {
            append(&mut output, &[first], maximum, budget)?;
        }
        if let Some(old) = previous
            && next < 4096
        {
            prefixes[next] = u16::try_from(old).expect("12-bit LZW code");
            suffixes[next] = first;
            next += 1;
            if width < 12 && next + early_change == 1 << width {
                width += 1;
            }
        }
        previous = Some(code);
    }
}

fn read_code(input: &[u8], cursor: &mut usize, width: usize) -> PdfResult<usize> {
    let end = cursor
        .checked_add(width)
        .ok_or_else(|| invalid("LZWDecode bit offset overflow"))?;
    if end.div_ceil(8) > input.len() {
        return Err(invalid("LZWDecode is truncated before EOD"));
    }
    let mut code = 0;
    for bit in *cursor..end {
        code = (code << 1) | usize::from((input[bit / 8] >> (7 - bit % 8)) & 1);
    }
    *cursor = end;
    Ok(code)
}

fn expand(
    mut code: usize,
    prefixes: &[u16; 4096],
    suffixes: &[u8; 4096],
    scratch: &mut [u8; 4096],
) -> PdfResult<usize> {
    let mut start = scratch.len();
    while code >= 258 {
        start = start
            .checked_sub(1)
            .ok_or_else(|| invalid("LZWDecode dictionary chain exceeds its bound"))?;
        scratch[start] = suffixes[code];
        code = usize::from(prefixes[code]);
    }
    if code > 255 {
        return Err(invalid("LZWDecode table contains a control code"));
    }
    start = start
        .checked_sub(1)
        .ok_or_else(|| invalid("LZWDecode dictionary chain exceeds its bound"))?;
    scratch[start] = u8::try_from(code).expect("literal byte checked");
    Ok(start)
}
