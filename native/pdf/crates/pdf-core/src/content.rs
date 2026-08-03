use crate::{
    ErrorCode, Lexer, ParseLimits, PdfError, PdfObject, PdfResult, Token, parser::ObjectParser,
};

/// One content-stream operator and its already parsed operands.
#[derive(Debug, Clone, PartialEq)]
pub struct ContentOperation {
    pub operator: Vec<u8>,
    pub operands: Vec<PdfObject>,
    pub offset: usize,
}

/// Parse a decoded page content stream into operators.
///
/// Inline images are bounded and skipped as one `BI` operation because page rendering is out of
/// scope; their binary payload is never interpreted as text operators.
///
/// # Errors
///
/// Returns a structured error for malformed operands, unterminated inline images, or limits.
#[allow(clippy::single_match_else)]
pub fn parse_content(input: &[u8], limits: &ParseLimits) -> PdfResult<Vec<ContentOperation>> {
    if input.len() > limits.max_decoded_stream_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(0),
            "decoded content stream byte limit exceeded",
        ));
    }
    let mut position = 0_usize;
    let mut operands = Vec::new();
    let mut operations = Vec::new();
    loop {
        let mut lexer = Lexer::with_limits(input, limits.clone());
        lexer.set_position(position)?;
        let Some(token) = lexer.next_token()? else {
            if operands.is_empty() {
                return Ok(operations);
            }
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                Some(position),
                "content stream ends with operands but no operator",
            ));
        };
        match token.token {
            Token::Keyword(operator) => {
                if operations.len() >= limits.max_content_operations {
                    return Err(PdfError::new(
                        ErrorCode::LimitExceeded,
                        Some(token.start),
                        "content operation limit exceeded",
                    ));
                }
                position = token.end;
                if operator == b"BI" {
                    if !operands.is_empty() {
                        return Err(PdfError::new(
                            ErrorCode::InvalidObject,
                            Some(token.start),
                            "BI cannot consume preceding operands",
                        ));
                    }
                    position = skip_inline_image(input, position, limits)?;
                }
                operations.push(ContentOperation {
                    operator,
                    operands: std::mem::take(&mut operands),
                    offset: token.start,
                });
            }
            _ => {
                if operands.len() >= limits.max_array_items {
                    return Err(PdfError::new(
                        ErrorCode::LimitExceeded,
                        Some(token.start),
                        "content operand limit exceeded",
                    ));
                }
                let mut parser = ObjectParser::at(input, position, limits.clone())?;
                operands.push(parser.parse_value(0)?);
                position = parser.position();
            }
        }
    }
}

fn skip_inline_image(input: &[u8], mut position: usize, limits: &ParseLimits) -> PdfResult<usize> {
    let mut token_count = 0_usize;
    loop {
        let mut lexer = Lexer::with_limits(input, limits.clone());
        lexer.set_position(position)?;
        let token = lexer.next_token()?.ok_or_else(|| {
            PdfError::new(
                ErrorCode::UnexpectedEof,
                Some(position),
                "inline image dictionary has no ID operator",
            )
        })?;
        position = token.end;
        token_count += 1;
        if token_count
            > limits
                .max_dictionary_entries
                .saturating_mul(2)
                .saturating_add(1)
        {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(position),
                "inline image dictionary token limit exceeded",
            ));
        }
        if token.token == Token::Keyword(b"ID".to_vec()) {
            break;
        }
    }

    position = consume_inline_image_separator(input, position)?;
    let search_end = input
        .len()
        .min(position.saturating_add(limits.max_stream_bytes));
    let mut cursor = position;
    while cursor + 2 < search_end {
        if is_content_whitespace(input[cursor])
            && input[cursor + 1] == b'E'
            && input[cursor + 2] == b'I'
            && input
                .get(cursor + 3)
                .is_none_or(|byte| is_content_whitespace(*byte) || is_content_delimiter(*byte))
        {
            return Ok(cursor + 3);
        }
        cursor += 1;
    }
    Err(PdfError::new(
        if search_end < input.len() {
            ErrorCode::LimitExceeded
        } else {
            ErrorCode::UnexpectedEof
        },
        Some(position),
        "inline image payload has no bounded EI terminator",
    ))
}

fn consume_inline_image_separator(input: &[u8], position: usize) -> PdfResult<usize> {
    match input.get(position) {
        Some(b'\r') if input.get(position + 1) == Some(&b'\n') => Ok(position + 2),
        Some(byte) if is_content_whitespace(*byte) => Ok(position + 1),
        _ => Err(PdfError::new(
            ErrorCode::InvalidStream,
            Some(position),
            "inline image ID must be followed by one whitespace separator",
        )),
    }
}

const fn is_content_whitespace(byte: u8) -> bool {
    matches!(byte, 0x00 | b'\t' | b'\n' | 0x0c | b'\r' | b' ')
}

const fn is_content_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%'
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PdfName, PdfString};

    #[test]
    fn parses_text_operators_and_nested_operands() {
        let operations = parse_content(
            b"BT /F1 12 Tf [(Hello) -120 (world)] TJ ET",
            &ParseLimits::default(),
        )
        .expect("valid content");
        assert_eq!(
            operations
                .iter()
                .map(|operation| operation.operator.as_slice())
                .collect::<Vec<_>>(),
            [b"BT".as_slice(), b"Tf", b"TJ", b"ET"]
        );
        assert_eq!(
            operations[1].operands,
            [
                PdfObject::Name(PdfName(b"F1".to_vec())),
                PdfObject::Integer(12)
            ]
        );
        assert!(matches!(
            &operations[2].operands[0],
            PdfObject::Array(items)
                if items[0] == PdfObject::String(PdfString(b"Hello".to_vec()))
        ));
    }

    #[test]
    fn skips_inline_image_payload_without_lexing_binary() {
        let operations = parse_content(
            b"q BI /W 1 /H 1 ID \xff\x00\xfe EI Q",
            &ParseLimits::default(),
        )
        .expect("bounded inline image");
        assert_eq!(
            operations
                .iter()
                .map(|operation| operation.operator.as_slice())
                .collect::<Vec<_>>(),
            [b"q".as_slice(), b"BI", b"Q"]
        );
    }
}
