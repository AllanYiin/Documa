use crate::{
    ErrorCode, Lexer, ObjectId, ParseLimits, PdfDictionary, PdfError, PdfName, PdfObject,
    PdfResult, PdfStream, SpannedToken, Token,
};

/// Parsed indirect object with its declared identifier and byte range.
#[derive(Debug, Clone, PartialEq)]
pub struct IndirectObject {
    pub id: ObjectId,
    pub value: PdfObject,
    pub start: usize,
    pub end: usize,
}

/// Parse one direct PDF object with default resource limits.
///
/// # Errors
///
/// Returns a structured error when the object syntax is malformed or a limit is exceeded.
pub fn parse_object(input: &[u8]) -> PdfResult<PdfObject> {
    parse_object_with_limits(input, &ParseLimits::default())
}

/// Parse one direct PDF object with explicit resource limits.
///
/// # Errors
///
/// Returns a structured error when the object syntax is malformed or a limit is exceeded.
pub fn parse_object_with_limits(input: &[u8], limits: &ParseLimits) -> PdfResult<PdfObject> {
    if input.len() > limits.max_file_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(0),
            "input byte limit exceeded",
        ));
    }
    let mut parser = ObjectParser::new(input, limits.clone());
    let object = parser.parse_value(0)?;
    if let Some(extra) = parser.next()? {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            Some(extra.start),
            "trailing token after direct object",
        ));
    }
    Ok(object)
}

pub(crate) struct ObjectParser<'a> {
    lexer: Lexer<'a>,
    limits: ParseLimits,
}

impl<'a> ObjectParser<'a> {
    pub(crate) fn new(input: &'a [u8], limits: ParseLimits) -> Self {
        Self {
            lexer: Lexer::with_limits(input, limits.clone()),
            limits,
        }
    }

    pub(crate) fn at(input: &'a [u8], offset: usize, limits: ParseLimits) -> PdfResult<Self> {
        let mut parser = Self::new(input, limits);
        parser.lexer.set_position(offset)?;
        Ok(parser)
    }

    pub(crate) const fn position(&self) -> usize {
        self.lexer.position()
    }

    pub(crate) fn parse_value(&mut self, depth: usize) -> PdfResult<PdfObject> {
        if depth > self.limits.max_object_depth {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(self.position()),
                "object nesting limit exceeded",
            ));
        }
        let token = self.next_required("expected PDF object")?;
        match token.token {
            Token::Null => Ok(PdfObject::Null),
            Token::Boolean(value) => Ok(PdfObject::Boolean(value)),
            Token::Integer(value) => self.parse_integer_or_reference(value, depth),
            Token::Real(value) => Ok(PdfObject::Real(value)),
            Token::Name(value) => Ok(PdfObject::Name(value)),
            Token::String(value) => Ok(PdfObject::String(value)),
            Token::StartArray => self.parse_array(depth + 1),
            Token::StartDictionary => self.parse_dictionary(depth + 1),
            Token::EndArray | Token::EndDictionary | Token::Keyword(_) => Err(PdfError::new(
                ErrorCode::InvalidObject,
                Some(token.start),
                "unexpected token where object was required",
            )),
        }
    }

    pub(crate) fn parse_indirect(self) -> PdfResult<IndirectObject> {
        self.parse_indirect_with_length_resolver(|_| {
            Err(PdfError::new(
                ErrorCode::UnsupportedFeature,
                None,
                "indirect stream Length requires document resolution",
            ))
        })
    }

    pub(crate) fn parse_indirect_with_length_resolver<F>(
        mut self,
        mut resolve_length: F,
    ) -> PdfResult<IndirectObject>
    where
        F: FnMut(ObjectId) -> PdfResult<usize>,
    {
        let start = self.position();
        let number_token = self.next_required("expected indirect object number")?;
        let generation_token = self.next_required("expected indirect object generation")?;
        let object_keyword = self.next_required("expected obj keyword")?;
        let number = integer_to_u32(&number_token, "object number")?;
        let generation = integer_to_u16(&generation_token, "generation number")?;
        expect_keyword(&object_keyword, b"obj", ErrorCode::InvalidObject)?;

        let mut value = self.parse_value(0)?;
        if matches!(value, PdfObject::Dictionary(_))
            && self
                .peek()?
                .is_some_and(|token| token.token == Token::Keyword(b"stream".to_vec()))
        {
            let stream_keyword = self.next_required("expected stream keyword")?;
            value = self.parse_stream(value, &stream_keyword, &mut resolve_length)?;
        }
        let end_object = self.next_required("expected endobj keyword")?;
        expect_keyword(&end_object, b"endobj", ErrorCode::InvalidObject)?;
        Ok(IndirectObject {
            id: ObjectId::new(number, generation),
            value,
            start,
            end: end_object.end,
        })
    }

    fn parse_integer_or_reference(&mut self, value: i64, _depth: usize) -> PdfResult<PdfObject> {
        let mut lookahead = self.lexer.clone();
        let Some(second) = lookahead.next_token()? else {
            return Ok(PdfObject::Integer(value));
        };
        let Token::Integer(generation) = second.token else {
            return Ok(PdfObject::Integer(value));
        };
        let Some(reference_keyword) = lookahead.next_token()? else {
            return Ok(PdfObject::Integer(value));
        };
        if reference_keyword.token != Token::Keyword(b"R".to_vec()) {
            return Ok(PdfObject::Integer(value));
        }
        let number = u32::try_from(value).map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidReference,
                Some(second.start),
                "indirect reference object number is out of range",
            )
        })?;
        let generation = u16::try_from(generation).map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidReference,
                Some(second.start),
                "indirect reference generation is out of range",
            )
        })?;
        self.lexer = lookahead;
        Ok(PdfObject::Reference(ObjectId::new(number, generation)))
    }

    fn parse_array(&mut self, depth: usize) -> PdfResult<PdfObject> {
        let mut values = Vec::new();
        loop {
            let token = self.peek()?.ok_or_else(|| {
                PdfError::new(
                    ErrorCode::UnexpectedEof,
                    Some(self.position()),
                    "unterminated array",
                )
            })?;
            if token.token == Token::EndArray {
                self.next()?;
                return Ok(PdfObject::Array(values));
            }
            if values.len() >= self.limits.max_array_items {
                return Err(PdfError::new(
                    ErrorCode::LimitExceeded,
                    Some(token.start),
                    "array item limit exceeded",
                ));
            }
            values.push(self.parse_value(depth)?);
        }
    }

    fn parse_dictionary(&mut self, depth: usize) -> PdfResult<PdfObject> {
        let mut dictionary = PdfDictionary::new();
        loop {
            let token = self.next_required("unterminated dictionary")?;
            if token.token == Token::EndDictionary {
                return Ok(PdfObject::Dictionary(dictionary));
            }
            let Token::Name(name) = token.token else {
                return Err(PdfError::new(
                    ErrorCode::InvalidObject,
                    Some(token.start),
                    "dictionary key must be a name",
                ));
            };
            if dictionary.len() >= self.limits.max_dictionary_entries {
                return Err(PdfError::new(
                    ErrorCode::LimitExceeded,
                    Some(token.start),
                    "dictionary entry limit exceeded",
                ));
            }
            let value = self.parse_value(depth)?;
            dictionary.insert(name, value);
        }
    }

    fn parse_stream<F>(
        &mut self,
        dictionary_object: PdfObject,
        stream_keyword: &SpannedToken,
        resolve_length: &mut F,
    ) -> PdfResult<PdfObject>
    where
        F: FnMut(ObjectId) -> PdfResult<usize>,
    {
        let PdfObject::Dictionary(dictionary) = dictionary_object else {
            unreachable!("caller checks dictionary variant");
        };
        let length_object = dictionary
            .get(&PdfName(b"Length".to_vec()))
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::InvalidStream,
                    Some(stream_keyword.start),
                    "stream dictionary has no Length",
                )
            })?;
        let length = match length_object {
            PdfObject::Integer(value) => usize::try_from(*value).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidStream,
                    Some(stream_keyword.start),
                    "stream Length is negative or out of range",
                )
            })?,
            PdfObject::Reference(id) => resolve_length(*id)?,
            _ => {
                return Err(PdfError::new(
                    ErrorCode::InvalidStream,
                    Some(stream_keyword.start),
                    "stream Length must be an integer or reference",
                ));
            }
        };
        if length > self.limits.max_stream_bytes {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(stream_keyword.start),
                "raw stream byte limit exceeded",
            ));
        }

        let input = self.lexer.input();
        let mut data_start = self.position();
        while input
            .get(data_start)
            .is_some_and(|byte| matches!(byte, b' ' | b'\t'))
        {
            data_start += 1;
        }
        match input.get(data_start) {
            Some(b'\n') => data_start += 1,
            Some(b'\r') => {
                data_start += 1;
                if input.get(data_start) == Some(&b'\n') {
                    data_start += 1;
                }
            }
            _ => {
                return Err(PdfError::new(
                    ErrorCode::InvalidStream,
                    Some(data_start),
                    "stream keyword must be followed by an end-of-line marker",
                ));
            }
        }
        let data_end = data_start.checked_add(length).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                Some(data_start),
                "stream range overflow",
            )
        })?;
        let data = input.get(data_start..data_end).ok_or_else(|| {
            PdfError::new(
                ErrorCode::UnexpectedEof,
                Some(data_start),
                "stream data is shorter than Length",
            )
        })?;
        self.lexer.set_position(data_end)?;
        let end_stream = self.next_required("expected endstream keyword")?;
        expect_keyword(&end_stream, b"endstream", ErrorCode::InvalidStream)?;
        Ok(PdfObject::Stream(PdfStream {
            dictionary,
            data: data.to_vec(),
        }))
    }

    fn next(&mut self) -> PdfResult<Option<SpannedToken>> {
        self.lexer.next_token()
    }

    fn next_required(&mut self, message: &str) -> PdfResult<SpannedToken> {
        self.next()?.ok_or_else(|| {
            PdfError::new(
                ErrorCode::UnexpectedEof,
                Some(self.position()),
                message.to_owned(),
            )
        })
    }

    fn peek(&self) -> PdfResult<Option<SpannedToken>> {
        let mut lookahead = self.lexer.clone();
        lookahead.next_token()
    }
}

fn expect_keyword(token: &SpannedToken, expected: &[u8], code: ErrorCode) -> PdfResult<()> {
    if token.token == Token::Keyword(expected.to_vec()) {
        Ok(())
    } else {
        Err(PdfError::new(
            code,
            Some(token.start),
            format!("expected {} keyword", String::from_utf8_lossy(expected)),
        ))
    }
}

fn integer_to_u32(token: &SpannedToken, label: &str) -> PdfResult<u32> {
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    u32::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidObject,
            Some(token.start),
            format!("{label} is out of range"),
        )
    })
}

fn integer_to_u16(token: &SpannedToken, label: &str) -> PdfResult<u16> {
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    u16::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidObject,
            Some(token.start),
            format!("{label} is out of range"),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_nested_objects_and_references() {
        let object = parse_object(b"<< /Type /Page /Parent 2 0 R /Box [0 0 612 792] >>")
            .expect("valid dictionary");
        assert_eq!(
            object.get(b"Parent").and_then(PdfObject::as_reference),
            Some(ObjectId::new(2, 0))
        );
        assert!(matches!(object.get(b"Box"), Some(PdfObject::Array(values)) if values.len() == 4));
    }

    #[test]
    fn parses_direct_length_stream() {
        let parser = ObjectParser::at(
            b"1 0 obj << /Length 5 >> stream\r\nhello\r\nendstream endobj",
            0,
            ParseLimits::default(),
        )
        .expect("valid offset");
        let indirect = parser.parse_indirect().expect("valid stream");
        assert!(matches!(indirect.value, PdfObject::Stream(ref stream) if stream.data == b"hello"));
    }

    #[test]
    fn enforces_nesting_limit() {
        let limits = ParseLimits {
            max_object_depth: 2,
            ..ParseLimits::default()
        };
        let error =
            parse_object_with_limits(b"[[[0]]]", &limits).expect_err("must reject excess nesting");
        assert_eq!(error.code, ErrorCode::LimitExceeded);
    }
}
