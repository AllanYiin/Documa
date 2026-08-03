use std::collections::BTreeSet;

use crate::{
    ErrorCode, Lexer, ObjectId, ParseLimits, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    Token, XrefKind,
    decode_budget::DecodeBudget,
    object_stream_cache::{CachedObjectMember, CachedObjectStream, ObjectStreamCacheKey},
    parse_object_with_limits,
    parser::{IndirectObject, ObjectParser},
};

pub(crate) fn resolve_object(
    document: &PdfDocument,
    id: ObjectId,
    stack: &mut BTreeSet<ObjectId>,
) -> PdfResult<IndirectObject> {
    if stack.len() >= document.limits.max_object_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "indirect object resolution depth exceeded",
        ));
    }
    if !stack.insert(id) {
        return Err(PdfError::new(
            ErrorCode::InvalidReference,
            None,
            format!(
                "cyclic indirect object reference while resolving {} {}",
                id.number, id.generation
            ),
        ));
    }
    let result = resolve_object_inner(document, id, stack);
    stack.remove(&id);
    result
}

fn resolve_object_inner(
    document: &PdfDocument,
    id: ObjectId,
    stack: &mut BTreeSet<ObjectId>,
) -> PdfResult<IndirectObject> {
    let entry = document.xref.get(&id.number).copied().ok_or_else(|| {
        PdfError::new(
            ErrorCode::ObjectNotFound,
            None,
            format!("object {} {} is absent from xref", id.number, id.generation),
        )
    })?;
    match entry.kind {
        XrefKind::Free => Err(PdfError::new(
            ErrorCode::ObjectNotFound,
            Some(entry.offset),
            format!("object {} {} is free", id.number, id.generation),
        )),
        XrefKind::InUse => {
            if entry.generation != id.generation {
                return Err(PdfError::new(
                    ErrorCode::ObjectNotFound,
                    Some(entry.offset),
                    format!("object {} {} is not in use", id.number, id.generation),
                ));
            }
            let parser = ObjectParser::at(&document.bytes, entry.offset, document.limits.clone())?;
            let object = parser.parse_indirect_with_length_resolver(|length_id| {
                let length_object = resolve_object(document, length_id, stack)?;
                let PdfObject::Integer(length) = length_object.value else {
                    return Err(PdfError::new(
                        ErrorCode::InvalidStream,
                        Some(length_object.start),
                        "indirect stream Length does not resolve to an integer",
                    ));
                };
                usize::try_from(length).map_err(|_| {
                    PdfError::new(
                        ErrorCode::InvalidStream,
                        Some(length_object.start),
                        "indirect stream Length is negative or out of range",
                    )
                })
            })?;
            if object.id != id {
                return Err(PdfError::new(
                    ErrorCode::ObjectIdMismatch,
                    Some(entry.offset),
                    format!(
                        "xref requested {} {}, object declares {} {}",
                        id.number, id.generation, object.id.number, object.id.generation
                    ),
                ));
            }
            Ok(object)
        }
        XrefKind::Compressed => resolve_compressed(document, id, entry, stack),
    }
}

#[allow(clippy::too_many_lines)] // Validation is intentionally linear and mirrors the object-stream wire format.
fn resolve_compressed(
    document: &PdfDocument,
    id: ObjectId,
    entry: crate::XrefEntry,
    stack: &mut BTreeSet<ObjectId>,
) -> PdfResult<IndirectObject> {
    if id.generation != 0 {
        return Err(PdfError::new(
            ErrorCode::ObjectNotFound,
            None,
            "compressed objects must use generation zero",
        ));
    }
    let stream_number = entry.object_stream.ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "compressed xref entry has no object stream number",
        )
    })?;
    let target_index = entry.object_index.ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidXref,
            None,
            "compressed xref entry has no object stream index",
        )
    })?;
    let container_id = ObjectId::new(stream_number, 0);
    let container = resolve_object(document, container_id, stack)?;
    let PdfObject::Stream(stream) = &container.value else {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            Some(container.start),
            "object stream xref entry does not resolve to a stream",
        ));
    };
    if !matches!(
        stream.dictionary.get(&PdfName(b"Type".to_vec())),
        Some(PdfObject::Name(name)) if name.is(b"ObjStm")
    ) {
        return Err(PdfError::new(
            ErrorCode::InvalidObject,
            Some(container.start),
            "object stream dictionary must have /Type /ObjStm",
        ));
    }
    let object_count = required_usize(stream, b"N", container.start)?;
    let first = required_usize(stream, b"First", container.start)?;
    if object_count > document.limits.max_xref_entries {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(container.start),
            "object stream object count limit exceeded",
        ));
    }
    let target_index = usize::try_from(target_index).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidXref,
            Some(container.start),
            "object stream index is out of range",
        )
    })?;
    if target_index >= object_count {
        return Err(PdfError::new(
            ErrorCode::InvalidXref,
            Some(container.start),
            "object stream index exceeds N",
        ));
    }

    let key = ObjectStreamCacheKey {
        object_id: container_id,
        revision_identity: container.start,
    };
    document.with_cached_object_stream(
        key,
        |budget| {
            build_cached_object_stream(
                stream,
                object_count,
                first,
                container.start,
                container.end,
                &document.limits,
                budget,
            )
        },
        |cached| parse_cached_member(cached, id, target_index, &document.limits),
    )
}

fn build_cached_object_stream(
    stream: &crate::PdfStream,
    object_count: usize,
    first: usize,
    container_start: usize,
    container_end: usize,
    limits: &ParseLimits,
    budget: &mut DecodeBudget,
) -> PdfResult<CachedObjectStream> {
    // A verified /ObjStm may relax only the expansion-ratio heuristic. Its absolute stream limit
    // and the document-lifetime DecodeBudget remain authoritative.
    let decoded = crate::filter::decode_stream_with_structural_budget(
        stream,
        limits,
        limits.max_decoded_stream_bytes,
        budget,
    )?;
    let header = decoded.get(..first).ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidStream,
            Some(container_start),
            "object stream First exceeds decoded length",
        )
    })?;
    let index = parse_object_stream_header(header, object_count, limits)?;
    let data_len = decoded.len() - first;
    let mut members = Vec::with_capacity(object_count);
    for (member_index, &(number, relative_start)) in index.iter().enumerate() {
        let relative_end = index
            .get(member_index + 1)
            .map_or(data_len, |(_, offset)| *offset);
        if relative_start > relative_end {
            return Err(PdfError::new(
                ErrorCode::InvalidStream,
                Some(container_start),
                "object stream offsets are not monotonic",
            ));
        }
        let start = first.checked_add(relative_start).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                Some(container_start),
                "object stream offset overflow",
            )
        })?;
        let end = first.checked_add(relative_end).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                Some(container_start),
                "object stream offset overflow",
            )
        })?;
        if decoded.get(start..end).is_none() {
            return Err(PdfError::new(
                ErrorCode::InvalidStream,
                Some(container_start),
                "object stream member range exceeds decoded length",
            ));
        }
        members.push(CachedObjectMember { number, start, end });
    }
    CachedObjectStream::new(decoded, members, container_start, container_end)
}

fn parse_cached_member(
    cached: &CachedObjectStream,
    id: ObjectId,
    target_index: usize,
    limits: &ParseLimits,
) -> PdfResult<IndirectObject> {
    let member = cached.members.get(target_index).ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidXref,
            Some(cached.container_start),
            "object stream index exceeds validated member index",
        )
    })?;
    if member.number != id.number {
        return Err(PdfError::new(
            ErrorCode::ObjectIdMismatch,
            Some(cached.container_start),
            format!(
                "compressed xref requested object {}, object stream index declares {}",
                id.number, member.number
            ),
        ));
    }
    let object_bytes = cached
        .decoded
        .get(member.start..member.end)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidStream,
                Some(cached.container_start),
                "cached object stream member range exceeds decoded length",
            )
        })?;
    let value = parse_object_with_limits(object_bytes, limits)?;
    Ok(IndirectObject {
        id,
        value,
        start: cached.container_start,
        end: cached.container_end,
    })
}

fn required_usize(stream: &crate::PdfStream, name: &[u8], offset: usize) -> PdfResult<usize> {
    let value = stream
        .dictionary
        .get(&PdfName(name.to_vec()))
        .and_then(PdfObject::as_integer)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidStream,
                Some(offset),
                format!(
                    "object stream has no integer {}",
                    String::from_utf8_lossy(name)
                ),
            )
        })?;
    usize::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStream,
            Some(offset),
            format!(
                "object stream {} is negative or out of range",
                String::from_utf8_lossy(name)
            ),
        )
    })
}

fn parse_object_stream_header(
    header: &[u8],
    object_count: usize,
    limits: &ParseLimits,
) -> PdfResult<Vec<(u32, usize)>> {
    let mut lexer = Lexer::with_limits(header, limits.clone());
    let mut index = Vec::with_capacity(object_count);
    for _ in 0..object_count {
        let number = next_nonnegative_integer(&mut lexer, "object stream object number")?;
        let offset = next_nonnegative_integer(&mut lexer, "object stream member offset")?;
        index.push((
            u32::try_from(number).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidStream,
                    None,
                    "object stream object number is out of range",
                )
            })?,
            offset,
        ));
    }
    if lexer.next_token()?.is_some() {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "object stream header contains more than N pairs",
        ));
    }
    Ok(index)
}

fn next_nonnegative_integer(lexer: &mut Lexer<'_>, label: &str) -> PdfResult<usize> {
    let token = lexer.next_token()?.ok_or_else(|| {
        PdfError::new(
            ErrorCode::UnexpectedEof,
            Some(lexer.position()),
            format!("missing {label}"),
        )
    })?;
    let Token::Integer(value) = token.token else {
        return Err(PdfError::new(
            ErrorCode::InvalidStream,
            Some(token.start),
            format!("{label} must be an integer"),
        ));
    };
    usize::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStream,
            Some(token.start),
            format!("{label} is negative or out of range"),
        )
    })
}
