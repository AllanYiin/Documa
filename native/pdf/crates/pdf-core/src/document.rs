use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, Mutex, MutexGuard},
};

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

use crate::{
    ErrorCode, ObjectId, ParseLimits, PdfDictionary, PdfError, PdfName, PdfObject, PdfResult,
    PdfStream, XrefEntry, XrefKind,
    decode_budget::{DecodeBudget, DecodeBudgetSnapshot},
    object_stream_cache::{CachedObjectStream, ObjectStreamCache, ObjectStreamCacheKey},
    parser::IndirectObject,
    xref::parse_xref_section,
};

/// Parsed PDF header version.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PdfVersion {
    pub major: u8,
    pub minor: u8,
}

/// Cheap document metadata that does not eagerly resolve every object.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DocumentSummary {
    pub version: PdfVersion,
    pub file_bytes: usize,
    pub xref_entries: usize,
    pub in_use_objects: usize,
    pub revisions: usize,
    pub startxref: usize,
    pub root: ObjectId,
}

/// Document-lifetime decode and object-stream cache diagnostics.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DecodeMetrics {
    pub decoded_bytes: usize,
    pub decode_operations: usize,
    pub object_stream_cache_hits: usize,
    pub object_stream_cache_misses: usize,
    pub object_stream_cache_evictions: usize,
    pub object_stream_cache_bytes: usize,
    pub peak_object_stream_cache_bytes: usize,
    pub object_stream_cache_entries: usize,
    pub peak_object_stream_cache_entries: usize,
}

#[derive(Debug)]
struct DocumentRuntime {
    decode_budget: DecodeBudget,
    object_stream_cache: ObjectStreamCache,
}

impl DocumentRuntime {
    const fn new(decode_budget: DecodeBudget) -> Self {
        Self {
            decode_budget,
            object_stream_cache: ObjectStreamCache::new(),
        }
    }
}

/// Read-only PDF document backed by one shared byte buffer.
///
/// Clones share one monotonic decode budget and one bounded object-stream cache.
#[derive(Debug, Clone)]
pub struct PdfDocument {
    pub(crate) bytes: Arc<[u8]>,
    version: PdfVersion,
    pub(crate) xref: BTreeMap<u32, XrefEntry>,
    trailer: PdfDictionary,
    startxref: usize,
    revisions: usize,
    pub(crate) limits: ParseLimits,
    runtime: Arc<Mutex<DocumentRuntime>>,
}

impl PdfDocument {
    /// Parse a PDF using default resource limits.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed headers, xref tables, trailers, or limits.
    pub fn parse(input: &[u8]) -> PdfResult<Self> {
        Self::parse_with_limits(input, ParseLimits::default())
    }

    /// Parse a PDF using explicit resource limits.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed headers, xref tables, trailers, or limits.
    pub fn parse_with_limits(input: &[u8], limits: ParseLimits) -> PdfResult<Self> {
        if input.len() > limits.max_file_bytes {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(0),
                "input byte limit exceeded",
            ));
        }
        let version = parse_header(input)?;
        let startxref = find_startxref(input)?;
        let mut current_offset = startxref;
        let mut seen_offsets = BTreeSet::new();
        let mut xref = BTreeMap::new();
        let mut newest_trailer = None;
        let mut revisions = 0_usize;
        let mut decode_budget = DecodeBudget::new(limits.max_total_decoded_bytes);

        loop {
            if revisions >= limits.max_incremental_updates {
                return Err(PdfError::new(
                    ErrorCode::LimitExceeded,
                    Some(current_offset),
                    "incremental update limit exceeded",
                ));
            }
            if !seen_offsets.insert(current_offset) {
                return Err(PdfError::new(
                    ErrorCode::InvalidXref,
                    Some(current_offset),
                    "cyclic Prev chain",
                ));
            }
            let section = parse_xref_section(input, current_offset, &limits, &mut decode_budget)?;
            validate_trailer_size(&section.trailer, &limits, current_offset)?;
            if newest_trailer.is_none() {
                newest_trailer = Some(section.trailer.clone());
            }
            for (number, entry) in section.entries {
                if xref.len() >= limits.max_xref_entries && !xref.contains_key(&number) {
                    return Err(PdfError::new(
                        ErrorCode::LimitExceeded,
                        Some(current_offset),
                        "merged xref entry limit exceeded",
                    ));
                }
                xref.entry(number).or_insert(entry);
            }
            revisions += 1;
            let Some(previous) = trailer_integer(&section.trailer, b"Prev") else {
                break;
            };
            current_offset = usize::try_from(previous).map_err(|_| {
                PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(current_offset),
                    "Prev offset is negative or out of range",
                )
            })?;
            if current_offset >= input.len() {
                return Err(PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(current_offset),
                    "Prev offset is outside the input",
                ));
            }
        }

        let trailer = newest_trailer.ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidTrailer,
                Some(startxref),
                "missing trailer",
            )
        })?;
        let document = Self {
            bytes: Arc::from(input),
            version,
            xref,
            trailer,
            startxref,
            revisions,
            limits,
            runtime: Arc::new(Mutex::new(DocumentRuntime::new(decode_budget))),
        };
        document.root_id()?;
        Ok(document)
    }

    /// Return immutable raw input bytes.
    #[must_use]
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// Return the parsed header version.
    #[must_use]
    pub const fn version(&self) -> PdfVersion {
        self.version
    }

    /// Return the newest trailer dictionary.
    #[must_use]
    pub const fn trailer(&self) -> &PdfDictionary {
        &self.trailer
    }

    /// Return the merged cross-reference map.
    #[must_use]
    pub const fn xref_entries(&self) -> &BTreeMap<u32, XrefEntry> {
        &self.xref
    }

    /// Return the catalog object reference from the newest trailer.
    ///
    /// # Errors
    ///
    /// Returns `invalid_trailer` when `/Root` is absent or not an indirect reference.
    pub fn root_id(&self) -> PdfResult<ObjectId> {
        self.trailer
            .get(&PdfName(b"Root".to_vec()))
            .and_then(PdfObject::as_reference)
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::InvalidTrailer,
                    Some(self.startxref),
                    "newest trailer has no indirect Root",
                )
            })
    }

    /// Lazily parse one in-use indirect object.
    ///
    /// # Errors
    ///
    /// Returns a structured error when the xref entry is absent, free, mismatched, or malformed.
    pub fn object(&self, id: ObjectId) -> PdfResult<IndirectObject> {
        let mut stack = BTreeSet::new();
        crate::object_stream::resolve_object(self, id, &mut stack)
    }

    /// Return a point-in-time snapshot of document decode/cache diagnostics.
    #[must_use]
    pub fn decode_metrics(&self) -> DecodeMetrics {
        let runtime = self.lock_runtime();
        let DecodeBudgetSnapshot {
            decoded_bytes,
            decode_operations,
        } = runtime.decode_budget.snapshot();
        let cache = runtime.object_stream_cache.snapshot();
        DecodeMetrics {
            decoded_bytes,
            decode_operations,
            object_stream_cache_hits: cache.hits,
            object_stream_cache_misses: cache.misses,
            object_stream_cache_evictions: cache.evictions,
            object_stream_cache_bytes: cache.current_bytes,
            peak_object_stream_cache_bytes: cache.peak_bytes,
            object_stream_cache_entries: cache.current_entries,
            peak_object_stream_cache_entries: cache.peak_entries,
        }
    }

    pub(crate) fn decode_stream(&self, stream: &PdfStream) -> PdfResult<Vec<u8>> {
        let mut runtime = self.lock_runtime();
        crate::filter::decode_stream_with_budget(stream, &self.limits, &mut runtime.decode_budget)
    }

    pub(crate) fn with_cached_object_stream<T, B, U>(
        &self,
        key: ObjectStreamCacheKey,
        build: B,
        use_entry: U,
    ) -> PdfResult<T>
    where
        B: FnOnce(&mut DecodeBudget) -> PdfResult<CachedObjectStream>,
        U: FnOnce(&CachedObjectStream) -> PdfResult<T>,
    {
        let mut runtime = self.lock_runtime();
        if let Some(entry) = runtime.object_stream_cache.get(key)? {
            return use_entry(&entry);
        }

        let entry = Arc::new(build(&mut runtime.decode_budget)?);
        runtime.object_stream_cache.insert(
            key,
            Arc::clone(&entry),
            self.limits.max_cached_object_stream_bytes,
            self.limits.max_cached_object_streams,
        )?;
        use_entry(&entry)
    }

    fn lock_runtime(&self) -> MutexGuard<'_, DocumentRuntime> {
        self.runtime
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Resolve and validate the document catalog.
    ///
    /// # Errors
    ///
    /// Returns an object or syntax error if `/Root` cannot be resolved to a dictionary.
    pub fn catalog(&self) -> PdfResult<IndirectObject> {
        let root = self.object(self.root_id()?)?;
        if root.value.as_dictionary().is_none() {
            return Err(PdfError::new(
                ErrorCode::InvalidObject,
                Some(root.start),
                "Root object is not a dictionary",
            ));
        }
        Ok(root)
    }

    /// Build a summary without eagerly resolving all objects.
    ///
    /// # Errors
    ///
    /// Returns `invalid_trailer` when the root reference is invalid.
    pub fn summary(&self) -> PdfResult<DocumentSummary> {
        Ok(DocumentSummary {
            version: self.version,
            file_bytes: self.bytes.len(),
            xref_entries: self.xref.len(),
            in_use_objects: self
                .xref
                .values()
                .filter(|entry| entry.kind != XrefKind::Free)
                .count(),
            revisions: self.revisions,
            startxref: self.startxref,
            root: self.root_id()?,
        })
    }
}

fn parse_header(input: &[u8]) -> PdfResult<PdfVersion> {
    let search_end = input.len().min(1024);
    let header_offset = find_subslice(&input[..search_end], b"%PDF-").ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidHeader,
            Some(0),
            "PDF header not found in first 1024 bytes",
        )
    })?;
    let version = input
        .get(header_offset + 5..header_offset + 8)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidHeader,
                Some(header_offset),
                "truncated PDF version",
            )
        })?;
    if !version[0].is_ascii_digit() || version[1] != b'.' || !version[2].is_ascii_digit() {
        return Err(PdfError::new(
            ErrorCode::InvalidHeader,
            Some(header_offset + 5),
            "PDF version must match digit.digit",
        ));
    }
    Ok(PdfVersion {
        major: version[0] - b'0',
        minor: version[2] - b'0',
    })
}

fn find_startxref(input: &[u8]) -> PdfResult<usize> {
    let marker = b"startxref";
    let marker_offset = rfind_subslice(input, marker).ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidStartXref,
            Some(input.len()),
            "startxref marker not found",
        )
    })?;
    let mut position = marker_offset + marker.len();
    while input
        .get(position)
        .is_some_and(|byte| matches!(byte, 0x00 | b'\t' | b'\n' | 0x0c | b'\r' | b' '))
    {
        position += 1;
    }
    let digits_start = position;
    while input.get(position).is_some_and(u8::is_ascii_digit) {
        position += 1;
    }
    if position == digits_start {
        return Err(PdfError::new(
            ErrorCode::InvalidStartXref,
            Some(position),
            "startxref has no decimal offset",
        ));
    }
    let text = std::str::from_utf8(&input[digits_start..position]).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStartXref,
            Some(digits_start),
            "startxref offset is not ASCII",
        )
    })?;
    let offset = text.parse::<usize>().map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStartXref,
            Some(digits_start),
            "startxref offset is out of range",
        )
    })?;
    if offset >= input.len() {
        return Err(PdfError::new(
            ErrorCode::InvalidStartXref,
            Some(digits_start),
            "startxref offset is outside the input",
        ));
    }
    Ok(offset)
}

fn validate_trailer_size(
    trailer: &PdfDictionary,
    limits: &ParseLimits,
    offset: usize,
) -> PdfResult<()> {
    let size = trailer_integer(trailer, b"Size").ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidTrailer,
            Some(offset),
            "trailer has no integer Size",
        )
    })?;
    let size = usize::try_from(size).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidTrailer,
            Some(offset),
            "trailer Size is negative or out of range",
        )
    })?;
    if size > limits.max_xref_entries {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(offset),
            "trailer Size exceeds xref entry limit",
        ));
    }
    Ok(())
}

fn trailer_integer(trailer: &PdfDictionary, name: &[u8]) -> Option<i64> {
    trailer
        .get(&PdfName(name.to_vec()))
        .and_then(PdfObject::as_integer)
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn rfind_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .rposition(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal_pdf() -> Vec<u8> {
        let mut pdf = b"%PDF-1.7\n".to_vec();
        let object_offset = pdf.len();
        pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");
        let xref_offset = pdf.len();
        pdf.extend_from_slice(
            format!(
                "xref\n0 2\n0000000000 65535 f\n{object_offset:010} 00000 n\n\
                 trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
            )
            .as_bytes(),
        );
        pdf
    }

    #[test]
    fn parses_and_lazily_resolves_minimal_document() {
        let document = PdfDocument::parse(&minimal_pdf()).expect("valid PDF");
        let summary = document.summary().expect("valid summary");
        assert_eq!(summary.version, PdfVersion { major: 1, minor: 7 });
        assert_eq!(summary.root, ObjectId::new(1, 0));
        assert_eq!(document.catalog().expect("valid catalog").id, summary.root);
    }

    #[test]
    fn rejects_bad_startxref() {
        let error = PdfDocument::parse(b"%PDF-1.7\nstartxref\n999\n%%EOF")
            .expect_err("offset must be bounded");
        assert_eq!(error.code, ErrorCode::InvalidStartXref);
    }
}
