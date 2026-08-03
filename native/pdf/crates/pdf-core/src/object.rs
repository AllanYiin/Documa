use std::collections::BTreeMap;

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Identifier of an indirect PDF object.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ObjectId {
    pub number: u32,
    pub generation: u16,
}

impl ObjectId {
    #[must_use]
    pub const fn new(number: u32, generation: u16) -> Self {
        Self { number, generation }
    }
}

/// A PDF name after `#xx` escape decoding, without the leading slash.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PdfName(pub Vec<u8>);

impl PdfName {
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.0
    }

    #[must_use]
    pub fn is(&self, value: &[u8]) -> bool {
        self.0 == value
    }
}

/// A PDF literal or hexadecimal string as decoded bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PdfString(pub Vec<u8>);

/// A PDF dictionary with byte-preserving names.
pub type PdfDictionary = BTreeMap<PdfName, PdfObject>;

/// An unfiltered PDF stream.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PdfStream {
    pub dictionary: PdfDictionary,
    pub data: Vec<u8>,
}

/// Parsed PDF object syntax.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub enum PdfObject {
    Null,
    Boolean(bool),
    Integer(i64),
    Real(f64),
    Name(PdfName),
    String(PdfString),
    Array(Vec<Self>),
    Dictionary(PdfDictionary),
    Stream(PdfStream),
    Reference(ObjectId),
}

impl PdfObject {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::Null => "null",
            Self::Boolean(_) => "boolean",
            Self::Integer(_) => "integer",
            Self::Real(_) => "real",
            Self::Name(_) => "name",
            Self::String(_) => "string",
            Self::Array(_) => "array",
            Self::Dictionary(_) => "dictionary",
            Self::Stream(_) => "stream",
            Self::Reference(_) => "reference",
        }
    }

    #[must_use]
    pub fn as_dictionary(&self) -> Option<&PdfDictionary> {
        match self {
            Self::Dictionary(dictionary) => Some(dictionary),
            Self::Stream(stream) => Some(&stream.dictionary),
            _ => None,
        }
    }

    #[must_use]
    pub fn get(&self, key: &[u8]) -> Option<&Self> {
        self.as_dictionary()
            .and_then(|dictionary| dictionary.get(&PdfName(key.to_vec())))
    }

    #[must_use]
    pub const fn as_integer(&self) -> Option<i64> {
        if let Self::Integer(value) = self {
            Some(*value)
        } else {
            None
        }
    }

    #[must_use]
    pub const fn as_reference(&self) -> Option<ObjectId> {
        if let Self::Reference(id) = self {
            Some(*id)
        } else {
            None
        }
    }
}
