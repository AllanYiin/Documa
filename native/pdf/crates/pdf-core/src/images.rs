use std::{collections::BTreeSet, io::Cursor};

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

use crate::{
    ErrorCode, ObjectId, PdfDictionary, PdfDocument, PdfError, PdfName, PdfObject, PdfResult,
    PdfStream,
};

/// Encoding of extracted image bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum ImageDataFormat {
    /// Original validated JPEG bitstream from `DCTDecode`.
    Jpeg,
    /// Decoded component samples for Flate or unfiltered images.
    RawSamples,
    /// Original bytes for a codec not decoded by the first release.
    EncodedUnknown,
}

/// Non-fatal image metadata or codec mismatch.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ImageWarning {
    pub code: String,
    pub message: String,
}

/// One image `XObject` occurrence on a page.
#[derive(Debug, Clone, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct ExtractedImage {
    pub page_index: usize,
    pub resource_name: String,
    pub object_id: Option<ObjectId>,
    pub width: u32,
    pub height: u32,
    pub bits_per_component: u8,
    pub color_space: Option<String>,
    pub filter: Option<String>,
    pub format: ImageDataFormat,
    pub data: Vec<u8>,
    pub warnings: Vec<ImageWarning>,
}

impl PdfDocument {
    /// Extract image `XObjects` without rendering pages.
    ///
    /// JPEG bytes are preserved, Flate streams become raw samples, and unsupported encoded
    /// formats remain available with an explicit warning.
    ///
    /// # Errors
    ///
    /// Returns a structured error for malformed image dictionaries, references, or limits.
    pub fn extract_images(&self) -> PdfResult<Vec<ExtractedImage>> {
        let pages = self.pages()?;
        let mut images = Vec::new();
        for page in pages {
            let mut stack = BTreeSet::new();
            walk_resources(
                self,
                page.index,
                "",
                &page.resources,
                &mut stack,
                &mut images,
                0,
            )?;
        }
        Ok(images)
    }
}

#[allow(clippy::too_many_arguments)]
fn walk_resources(
    document: &PdfDocument,
    page_index: usize,
    prefix: &str,
    resources: &PdfDictionary,
    stack: &mut BTreeSet<ObjectId>,
    images: &mut Vec<ExtractedImage>,
    depth: usize,
) -> PdfResult<()> {
    if depth > document.limits.max_object_depth {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "image/Form XObject nesting limit exceeded",
        ));
    }
    let Some(xobjects) = resources.get(&PdfName(b"XObject".to_vec())) else {
        return Ok(());
    };
    let xobjects = resolve_dictionary(document, xobjects)?;
    for (name, object) in xobjects {
        let component = String::from_utf8_lossy(name.as_bytes());
        let resource_name = if prefix.is_empty() {
            component.into_owned()
        } else {
            format!("{prefix}/{component}")
        };
        let id = object.as_reference();
        if let Some(id) = id
            && !stack.insert(id)
        {
            return Err(PdfError::new(
                ErrorCode::InvalidReference,
                None,
                "cyclic image/Form XObject reference",
            ));
        }
        let result = (|| {
            let value = if let Some(id) = id {
                document.object(id)?.value
            } else {
                object.clone()
            };
            let PdfObject::Stream(stream) = value else {
                return Ok(());
            };
            match stream.dictionary.get(&PdfName(b"Subtype".to_vec())) {
                Some(PdfObject::Name(subtype)) if subtype.is(b"Image") => {
                    if images.len() >= document.limits.max_images {
                        return Err(PdfError::new(
                            ErrorCode::LimitExceeded,
                            None,
                            "extracted image count limit exceeded",
                        ));
                    }
                    images.push(extract_image(
                        document,
                        page_index,
                        resource_name,
                        id,
                        &stream,
                    )?);
                }
                Some(PdfObject::Name(subtype)) if subtype.is(b"Form") => {
                    if let Some(form_resources) =
                        stream.dictionary.get(&PdfName(b"Resources".to_vec()))
                    {
                        let form_resources = resolve_dictionary(document, form_resources)?;
                        walk_resources(
                            document,
                            page_index,
                            &resource_name,
                            &form_resources,
                            stack,
                            images,
                            depth + 1,
                        )?;
                    }
                }
                _ => {}
            }
            Ok(())
        })();
        if let Some(id) = id {
            stack.remove(&id);
        }
        result?;
    }
    Ok(())
}

fn extract_image(
    document: &PdfDocument,
    page_index: usize,
    resource_name: String,
    object_id: Option<ObjectId>,
    stream: &PdfStream,
) -> PdfResult<ExtractedImage> {
    let width = required_u32(stream, b"Width")?;
    let height = required_u32(stream, b"Height")?;
    let pixels = usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .ok_or_else(|| {
            PdfError::new(ErrorCode::LimitExceeded, None, "image pixel count overflow")
        })?;
    if pixels > document.limits.max_image_pixels {
        return Err(PdfError::new(
            ErrorCode::LimitExceeded,
            None,
            "image pixel count limit exceeded",
        ));
    }
    let image_mask = matches!(
        stream.dictionary.get(&PdfName(b"ImageMask".to_vec())),
        Some(PdfObject::Boolean(true))
    );
    let bits_per_component = stream
        .dictionary
        .get(&PdfName(b"BitsPerComponent".to_vec()))
        .and_then(PdfObject::as_integer)
        .map(u8::try_from)
        .transpose()
        .map_err(|_| {
            PdfError::new(
                ErrorCode::InvalidStream,
                None,
                "image BitsPerComponent is out of range",
            )
        })?
        .unwrap_or(if image_mask { 1 } else { 8 });
    let color_space = stream
        .dictionary
        .get(&PdfName(b"ColorSpace".to_vec()))
        .and_then(color_space_name);
    let filters = filter_names(stream)?;
    let filter = (!filters.is_empty()).then(|| filters.join(","));
    let mut warnings = Vec::new();

    let (format, data) = if filters.is_empty() {
        (ImageDataFormat::RawSamples, stream.data.clone())
    } else if filters.len() == 1 && matches!(filters[0].as_str(), "DCTDecode" | "DCT") {
        validate_jpeg_dimensions(&stream.data, width, height, &mut warnings)?;
        (ImageDataFormat::Jpeg, stream.data.clone())
    } else if filters
        .iter()
        .all(|name| matches!(name.as_str(), "FlateDecode" | "Fl"))
    {
        (ImageDataFormat::RawSamples, document.decode_stream(stream)?)
    } else {
        warnings.push(ImageWarning {
            code: "unsupported_image_codec".to_owned(),
            message: format!(
                "encoded bytes were preserved without decoding filter chain {}",
                filters.join(",")
            ),
        });
        (ImageDataFormat::EncodedUnknown, stream.data.clone())
    };

    if format == ImageDataFormat::RawSamples {
        validate_raw_sample_length(
            data.len(),
            pixels,
            bits_per_component,
            color_space.as_deref(),
            &mut warnings,
        );
    }
    Ok(ExtractedImage {
        page_index,
        resource_name,
        object_id,
        width,
        height,
        bits_per_component,
        color_space,
        filter,
        format,
        data,
        warnings,
    })
}

fn validate_jpeg_dimensions(
    data: &[u8],
    width: u32,
    height: u32,
    warnings: &mut Vec<ImageWarning>,
) -> PdfResult<()> {
    let dimensions = image::ImageReader::with_format(Cursor::new(data), image::ImageFormat::Jpeg)
        .into_dimensions()
        .map_err(|error| {
            PdfError::new(
                ErrorCode::InvalidStream,
                None,
                format!("DCTDecode JPEG header is invalid: {error}"),
            )
        })?;
    if dimensions != (width, height) {
        warnings.push(ImageWarning {
            code: "jpeg_dimension_mismatch".to_owned(),
            message: format!(
                "PDF dictionary declares {width}x{height}, JPEG header declares {}x{}",
                dimensions.0, dimensions.1
            ),
        });
    }
    Ok(())
}

fn validate_raw_sample_length(
    actual: usize,
    pixels: usize,
    bits_per_component: u8,
    color_space: Option<&str>,
    warnings: &mut Vec<ImageWarning>,
) {
    let components = match color_space {
        Some("DeviceGray") | None => 1_usize,
        Some("DeviceRGB") => 3,
        Some("DeviceCMYK") => 4,
        _ => return,
    };
    let expected_bits = pixels
        .checked_mul(components)
        .and_then(|samples| samples.checked_mul(usize::from(bits_per_component)));
    let expected = expected_bits
        .and_then(|bits| bits.checked_add(7))
        .map(|bits| bits / 8);
    if expected.is_some_and(|expected| expected != actual) {
        warnings.push(ImageWarning {
            code: "raw_sample_length_mismatch".to_owned(),
            message: format!(
                "decoded sample length {actual} does not match expected {}",
                expected.unwrap_or_default()
            ),
        });
    }
}

fn filter_names(stream: &PdfStream) -> PdfResult<Vec<String>> {
    let Some(filter) = stream
        .dictionary
        .get(&PdfName(b"Filter".to_vec()))
        .or_else(|| stream.dictionary.get(&PdfName(b"F".to_vec())))
    else {
        return Ok(Vec::new());
    };
    match filter {
        PdfObject::Name(name) => Ok(vec![String::from_utf8_lossy(name.as_bytes()).into_owned()]),
        PdfObject::Array(values) => values
            .iter()
            .map(|value| {
                if let PdfObject::Name(name) = value {
                    Ok(String::from_utf8_lossy(name.as_bytes()).into_owned())
                } else {
                    Err(PdfError::new(
                        ErrorCode::InvalidStream,
                        None,
                        "image Filter array entries must be names",
                    ))
                }
            })
            .collect(),
        _ => Err(PdfError::new(
            ErrorCode::InvalidStream,
            None,
            "image Filter must be a name or array",
        )),
    }
}

fn color_space_name(object: &PdfObject) -> Option<String> {
    match object {
        PdfObject::Name(name) => Some(String::from_utf8_lossy(name.as_bytes()).into_owned()),
        PdfObject::Array(values) => values.first().and_then(|value| {
            if let PdfObject::Name(name) = value {
                Some(String::from_utf8_lossy(name.as_bytes()).into_owned())
            } else {
                None
            }
        }),
        _ => None,
    }
}

fn required_u32(stream: &PdfStream, name: &[u8]) -> PdfResult<u32> {
    let value = stream
        .dictionary
        .get(&PdfName(name.to_vec()))
        .and_then(PdfObject::as_integer)
        .ok_or_else(|| {
            PdfError::new(
                ErrorCode::InvalidStream,
                None,
                format!("image has no integer {}", String::from_utf8_lossy(name)),
            )
        })?;
    u32::try_from(value).map_err(|_| {
        PdfError::new(
            ErrorCode::InvalidStream,
            None,
            format!(
                "image {} is negative or out of range",
                String::from_utf8_lossy(name)
            ),
        )
    })
}

fn resolve_dictionary(document: &PdfDocument, object: &PdfObject) -> PdfResult<PdfDictionary> {
    let value = if let Some(id) = object.as_reference() {
        document.object(id)?.value
    } else {
        object.clone()
    };
    value.as_dictionary().cloned().ok_or_else(|| {
        PdfError::new(
            ErrorCode::InvalidObject,
            None,
            "expected resource dictionary",
        )
    })
}
