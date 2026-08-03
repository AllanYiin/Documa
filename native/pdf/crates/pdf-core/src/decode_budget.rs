use crate::{ErrorCode, PdfError, PdfResult};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct DecodeBudgetSnapshot {
    pub decoded_bytes: usize,
    pub decode_operations: usize,
}

#[derive(Debug)]
pub(crate) struct DecodeBudget {
    limit: usize,
    decoded_bytes: usize,
    decode_operations: usize,
}

impl DecodeBudget {
    pub(crate) const fn new(limit: usize) -> Self {
        Self {
            limit,
            decoded_bytes: 0,
            decode_operations: 0,
        }
    }

    pub(crate) const fn remaining(&self) -> usize {
        self.limit - self.decoded_bytes
    }

    pub(crate) fn begin_decode(&mut self) -> PdfResult<()> {
        self.decode_operations = self.decode_operations.checked_add(1).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "document decode operation count overflow",
            )
        })?;
        Ok(())
    }

    pub(crate) fn charge(&mut self, bytes: usize) -> PdfResult<()> {
        let decoded_bytes = self.decoded_bytes.checked_add(bytes).ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "document decoded byte count overflow",
            )
        })?;
        if decoded_bytes > self.limit {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "document decoded byte budget exceeded",
            ));
        }
        self.decoded_bytes = decoded_bytes;
        Ok(())
    }

    pub(crate) const fn snapshot(&self) -> DecodeBudgetSnapshot {
        DecodeBudgetSnapshot {
            decoded_bytes: self.decoded_bytes,
            decode_operations: self.decode_operations,
        }
    }
}
