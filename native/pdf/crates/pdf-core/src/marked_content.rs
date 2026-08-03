use std::collections::BTreeSet;

use crate::{
    ErrorCode, ObjectId, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    PdfString,
};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct MarkedContentProperties {
    pub tag: Option<String>,
    pub actual_text: Option<String>,
    pub alt_text: Option<String>,
    pub mcid: Option<i64>,
    pub artifact: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct MarkedContentResolution {
    pub properties: MarkedContentProperties,
    pub invalid_actual_text: bool,
    pub invalid_other: bool,
}

impl MarkedContentProperties {
    pub(crate) fn for_tag(tag: &PdfName) -> Self {
        Self {
            tag: Some(String::from_utf8_lossy(tag.as_bytes()).into_owned()),
            artifact: tag.is(b"Artifact"),
            ..Self::default()
        }
    }

    pub(crate) fn inherit_context(
        mut self,
        parent_is_artifact: bool,
        parent_alt_text: Option<String>,
    ) -> Self {
        self.artifact |= parent_is_artifact;
        if self.alt_text.is_none() {
            self.alt_text = parent_alt_text;
        }
        self
    }
}

pub(crate) fn resolve_marked_content_properties(
    document: &PdfDocument,
    resources: &PdfDictionary,
    tag: &PdfName,
    operand: &PdfObject,
) -> PdfResult<MarkedContentResolution> {
    let property_object = match operand {
        PdfObject::Dictionary(_) | PdfObject::Reference(_) => operand.clone(),
        PdfObject::Name(name) => {
            let properties = resources
                .get(&PdfName(b"Properties".to_vec()))
                .ok_or_else(|| invalid("marked-content Properties resource is missing"))?;
            let dictionary = resolve_dictionary(document, properties)?;
            dictionary.get(name).cloned().ok_or_else(|| {
                invalid("named marked-content property is missing from Properties resources")
            })?
        }
        _ => return Err(invalid("BDC property operand must be a dictionary or name")),
    };
    let dictionary = resolve_dictionary(document, &property_object)?;
    let mut resolution = MarkedContentResolution {
        properties: MarkedContentProperties::for_tag(tag),
        ..MarkedContentResolution::default()
    };
    if let Some(value) = dictionary.get(&PdfName(b"ActualText".to_vec())) {
        match resolve_text(document, value, "ActualText") {
            Ok(value) => resolution.properties.actual_text = Some(value),
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(_) => resolution.invalid_actual_text = true,
        }
    }
    if let Some(value) = dictionary.get(&PdfName(b"Alt".to_vec())) {
        match resolve_text(document, value, "Alt") {
            Ok(value) => resolution.properties.alt_text = Some(value),
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(_) => resolution.invalid_other = true,
        }
    }
    if let Some(value) = dictionary.get(&PdfName(b"MCID".to_vec())) {
        match resolve_mcid(document, value) {
            Ok(value) => resolution.properties.mcid = Some(value),
            Err(error) if error.code == ErrorCode::LimitExceeded => return Err(error),
            Err(_) => resolution.invalid_other = true,
        }
    }
    Ok(resolution)
}

pub(crate) fn resolve_text(
    document: &PdfDocument,
    object: &PdfObject,
    field: &str,
) -> PdfResult<String> {
    let value = resolve_value(document, object)?;
    let PdfObject::String(string) = value else {
        return Err(invalid(&format!("{field} must be a PDF string")));
    };
    decode_pdf_text_string(&string, field)
}

fn resolve_mcid(document: &PdfDocument, object: &PdfObject) -> PdfResult<i64> {
    let value = resolve_value(document, object)?;
    let PdfObject::Integer(value) = value else {
        return Err(invalid("MCID must be an integer"));
    };
    if value < 0 {
        return Err(invalid("MCID must be non-negative"));
    }
    Ok(value)
}

fn resolve_dictionary(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfDictionary> {
    let value = resolve_value(document, object)?;
    let PdfObject::Dictionary(dictionary) = value else {
        return Err(invalid(
            "marked-content property list must resolve to a dictionary",
        ));
    };
    Ok(dictionary)
}

pub(crate) fn resolve_value(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfObject> {
    let mut value = object.clone();
    let mut visited = BTreeSet::<ObjectId>::new();
    loop {
        let PdfObject::Reference(id) = value else {
            return Ok(value);
        };
        if visited.len() >= document.limits.max_object_depth {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "marked-content property resolution depth exceeded",
            ));
        }
        if !visited.insert(id) {
            return Err(PdfError::new(
                ErrorCode::InvalidReference,
                None,
                "cyclic marked-content property reference",
            ));
        }
        value = document.object(id)?.value;
    }
}

pub(crate) fn decode_pdf_text_string(string: &PdfString, field: &str) -> PdfResult<String> {
    let bytes = string.0.as_slice();
    if let Some(payload) = bytes.strip_prefix(&[0xfe, 0xff]) {
        return decode_utf16(payload, u16::from_be_bytes, field);
    }
    if let Some(payload) = bytes.strip_prefix(&[0xff, 0xfe]) {
        return decode_utf16(payload, u16::from_le_bytes, field);
    }
    if bytes.iter().all(u8::is_ascii) {
        return Ok(bytes.iter().map(|byte| char::from(*byte)).collect());
    }
    Err(invalid(&format!(
        "{field} without a Unicode BOM contains unsupported PDFDocEncoding bytes"
    )))
}

fn decode_utf16(payload: &[u8], decode: fn([u8; 2]) -> u16, field: &str) -> PdfResult<String> {
    if !payload.len().is_multiple_of(2) {
        return Err(invalid(&format!(
            "{field} UTF-16 payload has odd byte length"
        )));
    }
    let units = payload
        .chunks_exact(2)
        .map(|pair| decode([pair[0], pair[1]]))
        .collect::<Vec<_>>();
    String::from_utf16(&units).map_err(|_| invalid(&format!("{field} contains invalid UTF-16")))
}

fn invalid(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidObject, None, message)
}
