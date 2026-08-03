use std::fmt;

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Stable machine-readable parser error codes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum ErrorCode {
    UnexpectedEof,
    InvalidToken,
    InvalidOption,
    InvalidObject,
    InvalidPageGeometry,
    InvalidReference,
    LimitExceeded,
    InvalidString,
    InvalidHex,
    InvalidStream,
    InvalidHeader,
    InvalidStartXref,
    InvalidXref,
    InvalidTrailer,
    ObjectNotFound,
    ObjectIdMismatch,
    UnsupportedFeature,
    Io,
}

impl ErrorCode {
    /// Return the stable lowercase code used by CLI and language bindings.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::UnexpectedEof => "unexpected_eof",
            Self::InvalidToken => "invalid_token",
            Self::InvalidOption => "invalid_option",
            Self::InvalidObject => "invalid_object",
            Self::InvalidPageGeometry => "invalid_page_geometry",
            Self::InvalidReference => "invalid_reference",
            Self::LimitExceeded => "limit_exceeded",
            Self::InvalidString => "invalid_string",
            Self::InvalidHex => "invalid_hex",
            Self::InvalidStream => "invalid_stream",
            Self::InvalidHeader => "invalid_header",
            Self::InvalidStartXref => "invalid_startxref",
            Self::InvalidXref => "invalid_xref",
            Self::InvalidTrailer => "invalid_trailer",
            Self::ObjectNotFound => "object_not_found",
            Self::ObjectIdMismatch => "object_id_mismatch",
            Self::UnsupportedFeature => "unsupported_feature",
            Self::Io => "io",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

/// Structured parser error shared by all front ends.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[error("{code} at {offset:?}: {message}")]
pub struct PdfError {
    pub code: ErrorCode,
    pub offset: Option<usize>,
    pub message: String,
}

impl PdfError {
    /// Construct a new structured parser error.
    #[must_use]
    pub fn new(code: ErrorCode, offset: Option<usize>, message: impl Into<String>) -> Self {
        Self {
            code,
            offset,
            message: message.into(),
        }
    }
}

/// Result type used throughout the parser core.
pub type PdfResult<T> = Result<T, PdfError>;
