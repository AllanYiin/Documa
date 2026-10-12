use crate::{ErrorCode, PdfError, PdfPage, PdfResult};

/// One-based inclusive page ranges, emitted once each in original document order.
/// Use `"all"` or `"1,3-5"`. Empty, reversed and out-of-document ranges are errors.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PageSelection {
    ranges: Vec<(usize, usize)>,
}

impl PageSelection {
    /// Select all pages (the default).
    #[must_use]
    pub fn all() -> Self {
        Self::default()
    }

    /// Resolve page ranges into sorted, unique zero-based source indices.
    ///
    /// # Errors
    ///
    /// Returns `invalid_option` when a requested page does not exist.
    pub fn indices(&self, page_count: usize) -> PdfResult<Vec<usize>> {
        if self.ranges.is_empty() {
            return Ok((0..page_count).collect());
        }
        let mut selected = vec![false; page_count];
        for &(start, end) in &self.ranges {
            if end > page_count {
                return Err(invalid("selected page exceeds the document page count"));
            }
            selected[start - 1..end].fill(true);
        }
        Ok(selected
            .into_iter()
            .enumerate()
            .filter_map(|(index, selected)| selected.then_some(index))
            .collect())
    }

    pub(crate) fn select(&self, pages: Vec<PdfPage>) -> PdfResult<Vec<PdfPage>> {
        let indices = self.indices(pages.len())?;
        let mut wanted = indices.into_iter().peekable();
        Ok(pages
            .into_iter()
            .filter(|page| {
                if wanted.peek() == Some(&page.index) {
                    wanted.next();
                    true
                } else {
                    false
                }
            })
            .collect())
    }
}

impl std::str::FromStr for PageSelection {
    type Err = PdfError;

    fn from_str(value: &str) -> PdfResult<Self> {
        // Limit option parsing independently of PDF bytes and page-tree limits.
        if value.len() > 65_536 {
            return Err(invalid("page selection exceeds 65536 bytes"));
        }
        let value = value.trim();
        if value == "all" {
            return Ok(Self::all());
        }
        let mut ranges = Vec::new();
        for part in value.split(',') {
            let part = part.trim();
            let (start, end) = part.split_once('-').unwrap_or((part, part));
            let page = |value: &str| {
                let value = value.trim();
                if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
                    return Err(invalid(
                        "pages must be positive decimal numbers or inclusive ranges",
                    ));
                }
                value
                    .parse::<usize>()
                    .ok()
                    .filter(|&page| page > 0)
                    .ok_or_else(|| invalid("page number is zero or out of range"))
            };
            let (start, end) = (page(start)?, page(end)?);
            if start > end {
                return Err(invalid("page range end precedes start"));
            }
            ranges.push((start, end));
        }
        // Coalesce ranges so resolution does not repeatedly visit overlapping pages.
        ranges.sort_unstable();
        let mut merged: Vec<(usize, usize)> = Vec::new();
        for (start, end) in ranges {
            if let Some(last) = merged.last_mut()
                && start <= last.1.saturating_add(1)
            {
                last.1 = last.1.max(end);
            } else {
                merged.push((start, end));
            }
        }
        Ok(Self { ranges: merged })
    }
}

fn invalid(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidOption, None, message)
}
