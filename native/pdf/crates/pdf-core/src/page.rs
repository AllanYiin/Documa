use std::collections::BTreeSet;

use crate::PageGeometry;
use crate::{
    ErrorCode, ObjectId, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    PdfStream,
};

/// One leaf page with inherited page-tree attributes materialized.
#[derive(Debug, Clone, PartialEq)]
pub struct PdfPage {
    pub index: usize,
    pub id: ObjectId,
    pub dictionary: PdfDictionary,
    pub resources: PdfDictionary,
    pub media_box: Option<[f64; 4]>,
    pub crop_box: Option<[f64; 4]>,
    pub user_unit: f64,
    pub geometry: PageGeometry,
    pub rotate: i32,
}

#[derive(Debug, Clone, Default)]
struct InheritedPageAttributes {
    resources: Option<PdfDictionary>,
    media_box: Option<[f64; 4]>,
    crop_box: Option<[f64; 4]>,
    rotate: i32,
}

impl PdfDocument {
    /// Traverse the page tree in declared `/Kids` order.
    ///
    /// # Errors
    ///
    /// Returns a structured error for cycles, malformed page nodes, references, or limits.
    pub fn pages(&self) -> PdfResult<Vec<PdfPage>> {
        let catalog = self.catalog()?;
        let pages_id = catalog
            .value
            .get(b"Pages")
            .and_then(PdfObject::as_reference)
            .ok_or_else(|| {
                PdfError::new(
                    ErrorCode::InvalidObject,
                    Some(catalog.start),
                    "catalog has no indirect Pages root",
                )
            })?;
        let mut pages = Vec::new();
        let mut stack = BTreeSet::new();
        walk_page_tree(
            self,
            pages_id,
            InheritedPageAttributes::default(),
            &mut stack,
            &mut pages,
        )?;
        Ok(pages)
    }

    /// Decode and concatenate one page's `/Contents` streams in array order.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed content references, filters, or size limits.
    pub fn decoded_page_content(&self, page: &PdfPage) -> PdfResult<Vec<u8>> {
        let Some(contents) = page.dictionary.get(&PdfName(b"Contents".to_vec())) else {
            return Ok(Vec::new());
        };
        let mut output = Vec::new();
        append_content_object(self, contents, &mut output)?;
        Ok(output)
    }
}

fn walk_page_tree(
    document: &PdfDocument,
    id: ObjectId,
    inherited: InheritedPageAttributes,
    stack: &mut BTreeSet<ObjectId>,
    pages: &mut Vec<PdfPage>,
) -> PdfResult<()> {
    if stack.len() >= document.limits.max_object_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "page tree depth limit exceeded",
        ));
    }
    if !stack.insert(id) {
        return Err(PdfError::new(
            ErrorCode::InvalidReference,
            None,
            "cyclic page tree",
        ));
    }
    let result = walk_page_tree_inner(document, id, inherited, stack, pages);
    stack.remove(&id);
    result
}

fn walk_page_tree_inner(
    document: &PdfDocument,
    id: ObjectId,
    mut inherited: InheritedPageAttributes,
    stack: &mut BTreeSet<ObjectId>,
    pages: &mut Vec<PdfPage>,
) -> PdfResult<()> {
    let node = document.object(id)?;
    let dictionary = node.value.as_dictionary().ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            Some(node.start),
            "page tree node is not a dictionary",
        )
    })?;
    if let Some(resources) = dictionary.get(&PdfName(b"Resources".to_vec())) {
        inherited.resources = Some(resolve_dictionary(document, resources)?);
    }
    if let Some(media_box) = dictionary.get(&PdfName(b"MediaBox".to_vec())) {
        inherited.media_box = Some(parse_box(document, media_box, node.start)?);
    }
    if let Some(crop_box) = dictionary.get(&PdfName(b"CropBox".to_vec())) {
        inherited.crop_box = Some(parse_box(document, crop_box, node.start)?);
    }
    if let Some(rotate) = dictionary.get(&PdfName(b"Rotate".to_vec())) {
        let rotate = parse_page_integer(document, rotate, node.start, "Rotate")?;
        inherited.rotate = i32::try_from(rotate).map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidPageGeometry,
                Some(node.start),
                "page Rotate is out of range",
            )
        })?;
    }

    let is_page = matches!(
        dictionary.get(&PdfName(b"Type".to_vec())),
        Some(PdfObject::Name(name)) if name.is(b"Page")
    );
    if is_page {
        if pages.len() >= document.limits.max_pages {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(node.start),
                "page count limit exceeded",
            ));
        }
        let media_box = inherited.media_box.ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidPageGeometry,
                Some(node.start),
                "page has no inherited MediaBox",
            )
        })?;
        let crop_box = inherited.crop_box.unwrap_or(media_box);
        let user_unit = dictionary
            .get(&PdfName(b"UserUnit".to_vec()))
            .map(|value| parse_page_number(document, value, node.start, "UserUnit"))
            .transpose()?
            .unwrap_or(1.0);
        let geometry = PageGeometry::new(media_box, Some(crop_box), user_unit, inherited.rotate)?;
        pages.push(PdfPage {
            index: pages.len(),
            id,
            dictionary: dictionary.clone(),
            resources: inherited.resources.unwrap_or_default(),
            media_box: Some(media_box),
            crop_box: Some(crop_box),
            user_unit,
            rotate: geometry.rotation,
            geometry,
        });
        return Ok(());
    }

    let kids = dictionary
        .get(&PdfName(b"Kids".to_vec()))
        .and_then(|object| {
            if let PdfObject::Array(values) = object {
                Some(values)
            } else {
                None
            }
        })
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidObject,
                Some(node.start),
                "Pages node has no Kids array",
            )
        })?;
    if kids.len() > document.limits.max_pages {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            Some(node.start),
            "page-tree Kids limit exceeded",
        ));
    }
    for kid in kids {
        let kid_id = kid.as_reference().ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidReference,
                Some(node.start),
                "page-tree Kids entries must be indirect references",
            )
        })?;
        walk_page_tree(document, kid_id, inherited.clone(), stack, pages)?;
    }
    Ok(())
}

fn resolve_dictionary(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfDictionary> {
    let value = if let Some(id) = object.as_reference() {
        document.object(id)?.value
    } else {
        object.clone()
    };
    value
        .as_dictionary()
        .cloned()
        .ok_or_else(|| PdfError::new(ErrorCode::InvalidObject, None, "expected dictionary"))
}

#[allow(clippy::cast_precision_loss)]
fn parse_box(document: &PdfDocument, object: &PdfObject, offset: usize) -> PdfResult<[f64; 4]> {
    let resolved = resolve_page_attribute(document, object, offset)?;
    let PdfObject::Array(values) = resolved else {
        return Err(PdfError::new(
            ErrorCode::InvalidPageGeometry,
            Some(offset),
            "page box must be an array",
        ));
    };
    if values.len() != 4 {
        return Err(PdfError::new(
            ErrorCode::InvalidPageGeometry,
            Some(offset),
            "page box must contain four numbers",
        ));
    }
    let mut output = [0.0_f64; 4];
    for (index, value) in values.iter().enumerate() {
        output[index] = parse_page_number(document, value, offset, "box entry")?;
    }
    Ok(output)
}

#[allow(clippy::cast_precision_loss)]
fn parse_page_number(
    document: &PdfDocument,
    object: &PdfObject,
    offset: usize,
    name: &str,
) -> PdfResult<f64> {
    match resolve_page_attribute(document, object, offset)? {
        PdfObject::Integer(value) => Ok(value as f64),
        PdfObject::Real(value) => Ok(value),
        _ => Err(PdfError::new(
            ErrorCode::InvalidPageGeometry,
            Some(offset),
            format!("page {name} must be a number"),
        )),
    }
}

fn parse_page_integer(
    document: &PdfDocument,
    object: &PdfObject,
    offset: usize,
    name: &str,
) -> PdfResult<i64> {
    match resolve_page_attribute(document, object, offset)? {
        PdfObject::Integer(value) => Ok(value),
        _ => Err(PdfError::new(
            ErrorCode::InvalidPageGeometry,
            Some(offset),
            format!("page {name} must be an integer"),
        )),
    }
}

fn resolve_page_attribute(
    document: &PdfDocument,
    object: &PdfObject,
    offset: usize,
) -> PdfResult<PdfObject> {
    let mut current = object.clone();
    let mut visited = BTreeSet::new();
    loop {
        let Some(id) = current.as_reference() else {
            return Ok(current);
        };
        if visited.len() >= document.limits.max_object_depth {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                Some(offset),
                "page attribute reference depth limit exceeded",
            ));
        }
        if !visited.insert(id) {
            return Err(PdfError::new(
                ErrorCode::InvalidReference,
                Some(offset),
                "cyclic page attribute reference",
            ));
        }
        current = document.object(id)?.value;
    }
}

fn append_content_object(
    document: &PdfDocument,
    object: &PdfObject,
    output: &mut Vec<u8>,
) -> PdfResult<()> {
    let value = if let Some(id) = object.as_reference() {
        document.object(id)?.value
    } else {
        object.clone()
    };
    match value {
        PdfObject::Stream(stream) => append_decoded_stream(document, &stream, output),
        PdfObject::Array(values) => {
            for value in values {
                append_content_object(document, &value, output)?;
            }
            Ok(())
        }
        _ => Err(PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "Contents must resolve to a stream or array of streams",
        )),
    }
}

fn append_decoded_stream(
    document: &PdfDocument,
    stream: &PdfStream,
    output: &mut Vec<u8>,
) -> PdfResult<()> {
    let decoded = document.decode_stream(stream)?;
    let separator = usize::from(!output.is_empty());
    let new_len = output
        .len()
        .checked_add(separator)
        .and_then(|length| length.checked_add(decoded.len()))
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "concatenated page content size overflow",
            )
        })?;
    if new_len > document.limits.max_total_decoded_bytes {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "concatenated page content byte limit exceeded",
        ));
    }
    if separator == 1 {
        output.push(b'\n');
    }
    output.extend_from_slice(&decoded);
    Ok(())
}
