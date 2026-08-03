use std::{collections::BTreeMap, mem::size_of, sync::Arc};

use crate::{ErrorCode, ObjectId, PdfError, PdfResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct ObjectStreamCacheKey {
    pub object_id: ObjectId,
    pub revision_identity: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CachedObjectMember {
    pub number: u32,
    pub start: usize,
    pub end: usize,
}

#[derive(Debug)]
pub(crate) struct CachedObjectStream {
    pub decoded: Arc<[u8]>,
    pub members: Arc<[CachedObjectMember]>,
    pub container_start: usize,
    pub container_end: usize,
    weight: usize,
}

impl CachedObjectStream {
    pub(crate) fn new(
        decoded: Vec<u8>,
        members: Vec<CachedObjectMember>,
        container_start: usize,
        container_end: usize,
    ) -> PdfResult<Self> {
        let index_bytes = members
            .len()
            .checked_mul(size_of::<CachedObjectMember>())
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::LimitExceeded,
                    Some(container_start),
                    "object stream cache index size overflow",
                )
            })?;
        let weight = decoded.len().checked_add(index_bytes).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                Some(container_start),
                "object stream cache entry size overflow",
            )
        })?;
        Ok(Self {
            decoded: Arc::from(decoded),
            members: Arc::from(members),
            container_start,
            container_end,
            weight,
        })
    }

    pub(crate) const fn weight(&self) -> usize {
        self.weight
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct ObjectStreamCacheSnapshot {
    pub hits: usize,
    pub misses: usize,
    pub evictions: usize,
    pub current_bytes: usize,
    pub peak_bytes: usize,
    pub current_entries: usize,
    pub peak_entries: usize,
}

#[derive(Debug)]
struct CacheRecord {
    entry: Arc<CachedObjectStream>,
    last_used: u64,
}

#[derive(Debug)]
pub(crate) struct ObjectStreamCache {
    entries: BTreeMap<ObjectStreamCacheKey, CacheRecord>,
    current_bytes: usize,
    peak_bytes: usize,
    peak_entries: usize,
    hits: usize,
    misses: usize,
    evictions: usize,
    clock: u64,
}

impl ObjectStreamCache {
    pub(crate) const fn new() -> Self {
        Self {
            entries: BTreeMap::new(),
            current_bytes: 0,
            peak_bytes: 0,
            peak_entries: 0,
            hits: 0,
            misses: 0,
            evictions: 0,
            clock: 0,
        }
    }

    pub(crate) fn get(
        &mut self,
        key: ObjectStreamCacheKey,
    ) -> PdfResult<Option<Arc<CachedObjectStream>>> {
        let Some(record) = self.entries.get_mut(&key) else {
            self.misses =
                checked_increment_usize(self.misses, "object stream cache miss count overflow")?;
            return Ok(None);
        };
        self.hits = checked_increment_usize(self.hits, "object stream cache hit count overflow")?;
        self.clock = checked_increment_u64(self.clock, "object stream cache clock overflow")?;
        record.last_used = self.clock;
        Ok(Some(Arc::clone(&record.entry)))
    }

    pub(crate) fn insert(
        &mut self,
        key: ObjectStreamCacheKey,
        entry: Arc<CachedObjectStream>,
        max_bytes: usize,
        max_entries: usize,
    ) -> PdfResult<()> {
        let weight = entry.weight();
        if max_entries == 0 || weight > max_bytes {
            return Ok(());
        }

        if let Some(previous) = self.entries.remove(&key) {
            self.current_bytes = self
                .current_bytes
                .checked_sub(previous.entry.weight())
                .ok_or_else(cache_accounting_error)?;
        }

        while self.entries.len() >= max_entries
            || self
                .current_bytes
                .checked_add(weight)
                .is_none_or(|bytes| bytes > max_bytes)
        {
            self.evict_lru()?;
        }

        self.clock = checked_increment_u64(self.clock, "object stream cache clock overflow")?;
        self.current_bytes = self
            .current_bytes
            .checked_add(weight)
            .ok_or_else(cache_accounting_error)?;
        self.entries.insert(
            key,
            CacheRecord {
                entry,
                last_used: self.clock,
            },
        );
        self.peak_bytes = self.peak_bytes.max(self.current_bytes);
        self.peak_entries = self.peak_entries.max(self.entries.len());
        Ok(())
    }

    fn evict_lru(&mut self) -> PdfResult<()> {
        let key = self
            .entries
            .iter()
            .min_by_key(|(key, record)| (record.last_used, **key))
            .map(|(key, _)| *key)
            .ok_or_else(cache_accounting_error)?;
        let removed = self
            .entries
            .remove(&key)
            .ok_or_else(cache_accounting_error)?;
        self.current_bytes = self
            .current_bytes
            .checked_sub(removed.entry.weight())
            .ok_or_else(cache_accounting_error)?;
        self.evictions = checked_increment_usize(
            self.evictions,
            "object stream cache eviction count overflow",
        )?;
        Ok(())
    }

    pub(crate) fn snapshot(&self) -> ObjectStreamCacheSnapshot {
        ObjectStreamCacheSnapshot {
            hits: self.hits,
            misses: self.misses,
            evictions: self.evictions,
            current_bytes: self.current_bytes,
            peak_bytes: self.peak_bytes,
            current_entries: self.entries.len(),
            peak_entries: self.peak_entries,
        }
    }
}

fn checked_increment_usize(value: usize, message: &'static str) -> PdfResult<usize> {
    value
        .checked_add(1)
        .ok_or_else(|| PdfError::new(ErrorCode::LimitExceeded, None, message))
}

fn checked_increment_u64(value: u64, message: &'static str) -> PdfResult<u64> {
    value
        .checked_add(1)
        .ok_or_else(|| PdfError::new(ErrorCode::LimitExceeded, None, message))
}

fn cache_accounting_error() -> PdfError {
    PdfError::new(
        ErrorCode::LimitExceeded,
        None,
        "object stream cache accounting invariant failed",
    )
}
