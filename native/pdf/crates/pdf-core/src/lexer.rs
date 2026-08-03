use crate::{ErrorCode, ParseLimits, PdfError, PdfName, PdfResult, PdfString};

/// One lexical PDF token.
#[derive(Debug, Clone, PartialEq)]
pub enum Token {
    Null,
    Boolean(bool),
    Integer(i64),
    Real(f64),
    Name(PdfName),
    String(PdfString),
    StartArray,
    EndArray,
    StartDictionary,
    EndDictionary,
    Keyword(Vec<u8>),
}

/// Token paired with its half-open byte range.
#[derive(Debug, Clone, PartialEq)]
pub struct SpannedToken {
    pub token: Token,
    pub start: usize,
    pub end: usize,
}

/// Bounded lexer for PDF object syntax.
#[derive(Debug, Clone)]
pub struct Lexer<'a> {
    input: &'a [u8],
    position: usize,
    limits: ParseLimits,
}

impl<'a> Lexer<'a> {
    /// Create a lexer with default limits.
    #[must_use]
    pub fn new(input: &'a [u8]) -> Self {
        Self::with_limits(input, ParseLimits::default())
    }

    /// Create a lexer with explicit limits.
    #[must_use]
    pub const fn with_limits(input: &'a [u8], limits: ParseLimits) -> Self {
        Self {
            input,
            position: 0,
            limits,
        }
    }

    /// Current byte offset.
    #[must_use]
    pub const fn position(&self) -> usize {
        self.position
    }

    /// Read the next token.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed syntax or exceeded limits.
    pub fn next_token(&mut self) -> PdfResult<Option<SpannedToken>> {
        self.skip_whitespace_and_comments();
        if self.position == self.input.len() {
            return Ok(None);
        }

        let start = self.position;
        let byte = self.input[self.position];
        let token = match byte {
            b'[' => {
                self.position += 1;
                Token::StartArray
            }
            b']' => {
                self.position += 1;
                Token::EndArray
            }
            b'<' if self.input.get(self.position + 1) == Some(&b'<') => {
                self.position += 2;
                Token::StartDictionary
            }
            b'>' if self.input.get(self.position + 1) == Some(&b'>') => {
                self.position += 2;
                Token::EndDictionary
            }
            b'<' => Token::String(PdfString(self.read_hex_string()?)),
            b'(' => Token::String(PdfString(self.read_literal_string()?)),
            b'/' => Token::Name(PdfName(self.read_name()?)),
            b'+' | b'-' | b'.' | b'0'..=b'9' => self.read_number()?,
            b')' | b'{' | b'}' | b'>' => {
                return Err(PdfError::new(
                    ErrorCode::InvalidToken,
                    Some(start),
                    "unexpected delimiter",
                ));
            }
            _ => self.read_keyword()?,
        };

        Ok(Some(SpannedToken {
            token,
            start,
            end: self.position,
        }))
    }

    pub(crate) const fn input(&self) -> &'a [u8] {
        self.input
    }

    pub(crate) fn set_position(&mut self, position: usize) -> PdfResult<()> {
        if position > self.input.len() {
            return Err(PdfError::new(
                ErrorCode::UnexpectedEof,
                Some(self.input.len()),
                "position is beyond input",
            ));
        }
        self.position = position;
        Ok(())
    }

    pub(crate) fn skip_whitespace_and_comments(&mut self) {
        loop {
            while self
                .input
                .get(self.position)
                .is_some_and(|byte| is_whitespace(*byte))
            {
                self.position += 1;
            }
            if self.input.get(self.position) != Some(&b'%') {
                break;
            }
            while let Some(byte) = self.input.get(self.position) {
                self.position += 1;
                if matches!(byte, b'\r' | b'\n') {
                    break;
                }
            }
        }
    }

    fn read_name(&mut self) -> PdfResult<Vec<u8>> {
        let start = self.position;
        self.position += 1;
        let mut output = Vec::new();
        while let Some(&byte) = self.input.get(self.position) {
            if is_whitespace(byte) || is_delimiter(byte) {
                break;
            }
            if byte == b'#' {
                let high = self.input.get(self.position + 1).copied();
                let low = self.input.get(self.position + 2).copied();
                let decoded = match (high.and_then(hex_value), low.and_then(hex_value)) {
                    (Some(high), Some(low)) => high * 16 + low,
                    _ => {
                        return Err(PdfError::new(
                            ErrorCode::InvalidHex,
                            Some(self.position),
                            "name escape must contain two hexadecimal digits",
                        ));
                    }
                };
                output.push(decoded);
                self.position += 3;
            } else {
                output.push(byte);
                self.position += 1;
            }
            Self::check_output_limit(output.len(), self.limits.max_name_bytes, start, "name")?;
        }
        Ok(output)
    }

    fn read_hex_string(&mut self) -> PdfResult<Vec<u8>> {
        let start = self.position;
        self.position += 1;
        let mut output = Vec::new();
        let mut high_nibble = None;
        loop {
            let Some(&byte) = self.input.get(self.position) else {
                return Err(PdfError::new(
                    ErrorCode::UnexpectedEof,
                    Some(self.position),
                    "unterminated hexadecimal string",
                ));
            };
            self.position += 1;
            if byte == b'>' {
                if let Some(high) = high_nibble {
                    output.push(high * 16);
                }
                return Ok(output);
            }
            if is_whitespace(byte) {
                continue;
            }
            let value = hex_value(byte).ok_or_else(|| {
                PdfError::new(
                    ErrorCode::InvalidHex,
                    Some(self.position - 1),
                    "invalid hexadecimal string digit",
                )
            })?;
            if let Some(high) = high_nibble.take() {
                output.push(high * 16 + value);
                Self::check_output_limit(
                    output.len(),
                    self.limits.max_string_bytes,
                    start,
                    "string",
                )?;
            } else {
                high_nibble = Some(value);
            }
        }
    }

    fn read_literal_string(&mut self) -> PdfResult<Vec<u8>> {
        let start = self.position;
        self.position += 1;
        let mut depth = 1_usize;
        let mut output = Vec::new();
        while let Some(&byte) = self.input.get(self.position) {
            self.position += 1;
            match byte {
                b'(' => {
                    depth = depth.checked_add(1).ok_or_else(|| {
                        PdfError::new(
                            ErrorCode::LimitExceeded,
                            Some(self.position - 1),
                            "literal string nesting overflow",
                        )
                    })?;
                    if depth > self.limits.max_object_depth {
                        return Err(PdfError::new(
                            ErrorCode::LimitExceeded,
                            Some(self.position - 1),
                            "literal string nesting limit exceeded",
                        ));
                    }
                    output.push(byte);
                }
                b')' => {
                    depth -= 1;
                    if depth == 0 {
                        return Ok(output);
                    }
                    output.push(byte);
                }
                b'\\' => self.read_string_escape(&mut output)?,
                b'\r' => {
                    if self.input.get(self.position) == Some(&b'\n') {
                        self.position += 1;
                    }
                    output.push(b'\n');
                }
                b'\n' => output.push(b'\n'),
                _ => output.push(byte),
            }
            Self::check_output_limit(output.len(), self.limits.max_string_bytes, start, "string")?;
        }
        Err(PdfError::new(
            ErrorCode::UnexpectedEof,
            Some(self.position),
            "unterminated literal string",
        ))
    }

    fn read_string_escape(&mut self, output: &mut Vec<u8>) -> PdfResult<()> {
        let Some(&escaped) = self.input.get(self.position) else {
            return Err(PdfError::new(
                ErrorCode::UnexpectedEof,
                Some(self.position),
                "unterminated string escape",
            ));
        };
        self.position += 1;
        match escaped {
            b'n' => output.push(b'\n'),
            b'r' => output.push(b'\r'),
            b't' => output.push(b'\t'),
            b'b' => output.push(0x08),
            b'f' => output.push(0x0c),
            b'\n' => {}
            b'\r' => {
                if self.input.get(self.position) == Some(&b'\n') {
                    self.position += 1;
                }
            }
            b'0'..=b'7' => {
                let mut value = escaped - b'0';
                for _ in 0..2 {
                    let Some(&next) = self.input.get(self.position) else {
                        break;
                    };
                    if !(b'0'..=b'7').contains(&next) {
                        break;
                    }
                    self.position += 1;
                    value = value.wrapping_mul(8).wrapping_add(next - b'0');
                }
                output.push(value);
            }
            _ => output.push(escaped),
        }
        Ok(())
    }

    fn read_number(&mut self) -> PdfResult<Token> {
        let start = self.position;
        while self
            .input
            .get(self.position)
            .is_some_and(|byte| matches!(byte, b'+' | b'-' | b'.' | b'0'..=b'9'))
        {
            self.position += 1;
        }
        let bytes = &self.input[start..self.position];
        let text = std::str::from_utf8(bytes).map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidToken,
                Some(start),
                "numeric token is not ASCII",
            )
        })?;
        if bytes.contains(&b'.') {
            text.parse::<f64>().map(Token::Real).map_err(|_| {
                PdfError::new(ErrorCode::InvalidToken, Some(start), "invalid real number")
            })
        } else {
            text.parse::<i64>()
                .map(Token::Integer)
                .map_err(|_| PdfError::new(ErrorCode::InvalidToken, Some(start), "invalid integer"))
        }
    }

    fn read_keyword(&mut self) -> PdfResult<Token> {
        let start = self.position;
        while self
            .input
            .get(self.position)
            .is_some_and(|byte| !is_whitespace(*byte) && !is_delimiter(*byte))
        {
            self.position += 1;
        }
        if self.position == start {
            return Err(PdfError::new(
                ErrorCode::InvalidToken,
                Some(start),
                "empty keyword",
            ));
        }
        let keyword = &self.input[start..self.position];
        Ok(match keyword {
            b"null" => Token::Null,
            b"true" => Token::Boolean(true),
            b"false" => Token::Boolean(false),
            _ => Token::Keyword(keyword.to_vec()),
        })
    }

    fn check_output_limit(
        actual: usize,
        maximum: usize,
        offset: usize,
        kind: &str,
    ) -> PdfResult<()> {
        if actual > maximum {
            Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(offset),
                format!("{kind} byte limit exceeded"),
            ))
        } else {
            Ok(())
        }
    }
}

pub(crate) const fn is_whitespace(byte: u8) -> bool {
    matches!(byte, 0x00 | b'\t' | b'\n' | 0x0c | b'\r' | b' ')
}

pub(crate) const fn is_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%'
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

    fn tokens(input: &[u8]) -> PdfResult<Vec<Token>> {
        let mut lexer = Lexer::new(input);
        let mut output = Vec::new();
        while let Some(token) = lexer.next_token()? {
            output.push(token.token);
        }
        Ok(output)
    }

    #[test]
    fn tokenizes_names_numbers_strings_and_comments() {
        let result = tokens(b"% note\n/Hello#20World -12 3.5 (a\\n\\050b\\051) <4142F>")
            .expect("valid tokens");
        assert_eq!(
            result,
            vec![
                Token::Name(PdfName(b"Hello World".to_vec())),
                Token::Integer(-12),
                Token::Real(3.5),
                Token::String(PdfString(b"a\n(b)".to_vec())),
                Token::String(PdfString(vec![0x41, 0x42, 0xf0])),
            ]
        );
    }

    #[test]
    fn enforces_string_limit() {
        let limits = ParseLimits {
            max_string_bytes: 2,
            ..ParseLimits::default()
        };
        let error = Lexer::with_limits(b"(abc)", limits)
            .next_token()
            .expect_err("must reject oversized string");
        assert_eq!(error.code, ErrorCode::LimitExceeded);
    }
}
