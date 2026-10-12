#![forbid(unsafe_code)]

use image::{DynamicImage, GrayImage, Rgb, RgbImage};
use pdf_core::{
    DirectOcrOptions, DirectOcrReason, ImageDataFormat, OcrLayoutBlock, OcrPixelBlock, PdfDocument,
};
use serde::{Deserialize, Serialize};

#[cfg(feature = "rapidocr-onnx")]
const MAX_MODEL_BYTES: usize = 512 * 1024 * 1024;
#[cfg(feature = "rapidocr-onnx")]
use pdf_core::OcrImageQuad;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OcrErrorCode {
    Pdf,
    ImageDecode,
    Model,
    Inference,
    LimitExceeded,
    UnsupportedImage,
}

impl OcrErrorCode {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pdf => "ocr_pdf",
            Self::ImageDecode => "ocr_image_decode",
            Self::Model => "ocr_model",
            Self::Inference => "ocr_inference",
            Self::LimitExceeded => "ocr_limit_exceeded",
            Self::UnsupportedImage => "ocr_unsupported_image",
        }
    }
}
impl std::fmt::Display for OcrErrorCode {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error, Serialize, Deserialize)]
#[error("{code}: {message}")]
pub struct OcrError {
    pub code: OcrErrorCode,
    pub message: String,
}

impl OcrError {
    fn new(code: OcrErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl From<pdf_core::PdfError> for OcrError {
    fn from(error: pdf_core::PdfError) -> Self {
        Self::new(
            OcrErrorCode::Pdf,
            format!("{}: {}", error.code, error.message),
        )
    }
}

pub trait ImageOcrEngine {
    /// Recognize bounded text regions in one decoded image.
    ///
    /// # Errors
    ///
    /// Returns a stable inference or limit error for backend failure or excess output.
    fn recognize(
        &self,
        image: &DynamicImage,
        max_blocks: usize,
    ) -> Result<Vec<OcrPixelBlock>, OcrError>;
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct OcrOptions {
    pub direct_image: DirectOcrOptions,
    pub max_blocks_per_page: usize,
    pub max_total_text_bytes_per_page: usize,
    pub min_confidence: f32,
}

impl Default for OcrOptions {
    fn default() -> Self {
        Self {
            direct_image: DirectOcrOptions::default(),
            max_blocks_per_page: 4_096,
            max_total_text_bytes_per_page: 16 * 1024 * 1024,
            min_confidence: 0.0,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OcrPageResult {
    pub page_index: usize,
    pub reason: DirectOcrReason,
    pub blocks: Vec<OcrLayoutBlock>,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OcrDocumentResult {
    pub pages: Vec<OcrPageResult>,
    pub text: String,
}

/// Run direct-image OCR over every eligible page.
///
/// # Errors
///
/// Returns a stable PDF, image decode, model inference, or configured limit error.
pub fn ocr_document(
    document: &PdfDocument,
    engine: &impl ImageOcrEngine,
    options: OcrOptions,
) -> Result<OcrDocumentResult, OcrError> {
    validate_options(options)?;
    let analysis = document.analyze_direct_ocr(options.direct_image)?;
    let mut pages = Vec::with_capacity(analysis.pages.len());
    let mut document_text = String::new();

    for page in analysis.pages {
        let Some(candidate) = page.candidate else {
            pages.push(OcrPageResult {
                page_index: page.page_index,
                reason: page.reason,
                blocks: Vec::new(),
                text: String::new(),
            });
            continue;
        };
        let image = decode_candidate_image(&candidate.image)?;
        let pixel_blocks = engine.recognize(&image, options.max_blocks_per_page)?;
        if pixel_blocks.len() > options.max_blocks_per_page {
            return Err(OcrError::new(
                OcrErrorCode::LimitExceeded,
                "OCR backend returned too many blocks",
            ));
        }
        let mut text_bytes = 0_usize;
        let mut blocks = Vec::with_capacity(pixel_blocks.len());
        for block in pixel_blocks {
            if !block.confidence.is_finite() {
                return Err(OcrError::new(
                    OcrErrorCode::Inference,
                    "OCR backend returned non-finite confidence",
                ));
            }
            if block.confidence < options.min_confidence || block.text.is_empty() {
                continue;
            }
            text_bytes = text_bytes.checked_add(block.text.len()).ok_or_else(|| {
                OcrError::new(OcrErrorCode::LimitExceeded, "OCR text length overflow")
            })?;
            if text_bytes > options.max_total_text_bytes_per_page {
                return Err(OcrError::new(
                    OcrErrorCode::LimitExceeded,
                    "OCR page text length limit exceeded",
                ));
            }
            blocks.push(candidate.map_block(&block)?);
        }
        let text = blocks
            .iter()
            .map(|block| block.text.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        if !text.is_empty() {
            if !document_text.is_empty() {
                document_text.push('\n');
            }
            document_text.push_str(&text);
        }
        pages.push(OcrPageResult {
            page_index: page.page_index,
            reason: DirectOcrReason::Eligible,
            blocks,
            text,
        });
    }
    Ok(OcrDocumentResult {
        pages,
        text: document_text,
    })
}

fn validate_options(options: OcrOptions) -> Result<(), OcrError> {
    if options.max_blocks_per_page == 0 || options.max_blocks_per_page > 65_536 {
        return Err(OcrError::new(
            OcrErrorCode::LimitExceeded,
            "max_blocks_per_page must be in 1..=65536",
        ));
    }
    if options.max_total_text_bytes_per_page == 0
        || options.max_total_text_bytes_per_page > 64 * 1024 * 1024
    {
        return Err(OcrError::new(
            OcrErrorCode::LimitExceeded,
            "max_total_text_bytes_per_page must be in 1..=67108864",
        ));
    }
    if !options.min_confidence.is_finite() || !(0.0..=1.0).contains(&options.min_confidence) {
        return Err(OcrError::new(
            OcrErrorCode::Inference,
            "min_confidence must be finite and in [0, 1]",
        ));
    }
    Ok(())
}

/// Decode one extracted JPEG or supported raw sample buffer for OCR.
///
/// # Errors
///
/// Returns a stable decode, unsupported-image, or limit error.
pub fn decode_candidate_image(image: &pdf_core::ExtractedImage) -> Result<DynamicImage, OcrError> {
    match image.format {
        ImageDataFormat::Jpeg => {
            let reader = || {
                image::ImageReader::with_format(
                    std::io::Cursor::new(&image.data),
                    image::ImageFormat::Jpeg,
                )
            };
            let decode_error = |error| {
                OcrError::new(
                    OcrErrorCode::ImageDecode,
                    format!("invalid JPEG candidate: {error}"),
                )
            };
            let dimensions = reader().into_dimensions().map_err(decode_error)?;
            if dimensions != (image.width, image.height) {
                return Err(OcrError::new(
                    OcrErrorCode::ImageDecode,
                    "JPEG dimensions do not match candidate geometry",
                ));
            }
            reader().decode().map_err(decode_error)
        }
        ImageDataFormat::RawSamples => decode_raw_samples(image),
        ImageDataFormat::EncodedUnknown => Err(OcrError::new(
            OcrErrorCode::UnsupportedImage,
            "the PDF image codec is not decoded",
        )),
    }
}

fn decode_raw_samples(image: &pdf_core::ExtractedImage) -> Result<DynamicImage, OcrError> {
    let pixels = usize::try_from(image.width)
        .ok()
        .and_then(|width| {
            usize::try_from(image.height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .ok_or_else(|| OcrError::new(OcrErrorCode::LimitExceeded, "image size overflow"))?;
    match (image.color_space.as_deref(), image.bits_per_component) {
        (Some("DeviceGray") | None, 1) => {
            let width = usize::try_from(image.width)
                .map_err(|_| OcrError::new(OcrErrorCode::LimitExceeded, "image width overflow"))?;
            let row_bytes = width.div_ceil(8);
            let expected = usize::try_from(image.height)
                .ok()
                .and_then(|height| row_bytes.checked_mul(height))
                .ok_or_else(|| {
                    OcrError::new(OcrErrorCode::LimitExceeded, "sample size overflow")
                })?;
            ensure_sample_length(&image.data, expected)?;
            let mut decoded = Vec::with_capacity(pixels);
            for index in 0..pixels {
                let row = index / width;
                let column = index % width;
                let bit = (image.data[row * row_bytes + column / 8] >> (7 - column % 8)) & 1;
                decoded.push(if bit == 0 { 0 } else { 255 });
            }
            GrayImage::from_raw(image.width, image.height, decoded)
                .map(DynamicImage::ImageLuma8)
                .ok_or_else(|| OcrError::new(OcrErrorCode::ImageDecode, "invalid gray image"))
        }
        (Some("DeviceGray") | None, 8) => {
            ensure_sample_length(&image.data, pixels)?;
            GrayImage::from_raw(image.width, image.height, image.data.clone())
                .map(DynamicImage::ImageLuma8)
                .ok_or_else(|| OcrError::new(OcrErrorCode::ImageDecode, "invalid gray image"))
        }
        (Some("DeviceRGB"), 8) => {
            let expected = pixels.checked_mul(3).ok_or_else(|| {
                OcrError::new(OcrErrorCode::LimitExceeded, "RGB sample size overflow")
            })?;
            ensure_sample_length(&image.data, expected)?;
            RgbImage::from_raw(image.width, image.height, image.data.clone())
                .map(DynamicImage::ImageRgb8)
                .ok_or_else(|| OcrError::new(OcrErrorCode::ImageDecode, "invalid RGB image"))
        }
        (Some("DeviceCMYK"), 8) => {
            let expected = pixels.checked_mul(4).ok_or_else(|| {
                OcrError::new(OcrErrorCode::LimitExceeded, "CMYK sample size overflow")
            })?;
            ensure_sample_length(&image.data, expected)?;
            let mut rgb = RgbImage::new(image.width, image.height);
            for (pixel, cmyk) in rgb.pixels_mut().zip(image.data.chunks_exact(4)) {
                let c = u16::from(cmyk[0]);
                let m = u16::from(cmyk[1]);
                let y = u16::from(cmyk[2]);
                let k = u16::from(cmyk[3]);
                *pixel = Rgb([
                    u8::try_from(255_u16.saturating_sub((c + k).min(255))).expect("bounded"),
                    u8::try_from(255_u16.saturating_sub((m + k).min(255))).expect("bounded"),
                    u8::try_from(255_u16.saturating_sub((y + k).min(255))).expect("bounded"),
                ]);
            }
            Ok(DynamicImage::ImageRgb8(rgb))
        }
        (color_space, bits) => Err(OcrError::new(
            OcrErrorCode::UnsupportedImage,
            format!("unsupported direct OCR samples: color_space={color_space:?}, bits={bits}"),
        )),
    }
}

fn ensure_sample_length(data: &[u8], expected: usize) -> Result<(), OcrError> {
    if data.len() == expected {
        Ok(())
    } else {
        Err(OcrError::new(
            OcrErrorCode::ImageDecode,
            format!(
                "raw sample length {} does not match expected {expected}",
                data.len()
            ),
        ))
    }
}

#[cfg(feature = "rapidocr-onnx")]
pub struct RapidOcrOnnxEngine {
    inner: paddleocr_rs_onnx::OcrEngine,
}

#[cfg(feature = "rapidocr-onnx")]
impl RapidOcrOnnxEngine {
    /// Create a CPU-first RapidOCR-compatible ONNX engine from bounded model bytes.
    ///
    /// # Errors
    ///
    /// Returns a stable model error when inputs are empty, too large, or invalid.
    pub fn new(
        detection_model: &[u8],
        recognition_model: &[u8],
        dictionary: &[u8],
    ) -> Result<Self, OcrError> {
        if detection_model.is_empty()
            || recognition_model.is_empty()
            || dictionary.is_empty()
            || detection_model.len() > MAX_MODEL_BYTES
            || recognition_model.len() > MAX_MODEL_BYTES
            || dictionary.len() > 16 * 1024 * 1024
        {
            return Err(OcrError::new(
                OcrErrorCode::Model,
                "OCR models/dictionary are empty or exceed configured limits",
            ));
        }
        paddleocr_rs_onnx::OcrEngine::new(detection_model, recognition_model, dictionary)
            .map(|inner| Self { inner })
            .map_err(|error| OcrError::new(OcrErrorCode::Model, error.to_string()))
    }
}

#[cfg(feature = "rapidocr-onnx")]
impl ImageOcrEngine for RapidOcrOnnxEngine {
    fn recognize(
        &self,
        image: &DynamicImage,
        max_blocks: usize,
    ) -> Result<Vec<OcrPixelBlock>, OcrError> {
        let regions = self
            .inner
            .detect_text_regions(image)
            .map_err(|error| OcrError::new(OcrErrorCode::Inference, error.to_string()))?;
        if regions.len() > max_blocks {
            return Err(OcrError::new(
                OcrErrorCode::LimitExceeded,
                "detector returned too many text regions",
            ));
        }
        let mut blocks = Vec::with_capacity(regions.len());
        for region in regions {
            let decoded = self
                .inner
                .recognize_text(image, &region)
                .map_err(|error| OcrError::new(OcrErrorCode::Inference, error.to_string()))?;
            if !decoded.text.is_empty() {
                blocks.push(OcrPixelBlock {
                    text: decoded.text,
                    confidence: decoded.score,
                    quad: OcrImageQuad {
                        top_left: region.bbox[0],
                        top_right: region.bbox[1],
                        bottom_right: region.bbox[2],
                        bottom_left: region.bbox[3],
                    },
                });
            }
        }
        blocks.sort_by(|left, right| {
            left.quad.top_left[1]
                .total_cmp(&right.quad.top_left[1])
                .then_with(|| left.quad.top_left[0].total_cmp(&right.quad.top_left[0]))
        });
        Ok(blocks)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pdf_core::{ExtractedImage, ImageWarning};

    fn raw_image(color_space: Option<&str>, bits: u8, data: Vec<u8>) -> ExtractedImage {
        ExtractedImage {
            page_index: 0,
            resource_name: "Im0".to_owned(),
            object_id: None,
            width: 2,
            height: 1,
            bits_per_component: bits,
            image_mask: false,
            has_soft_mask: false,
            color_space: color_space.map(str::to_owned),
            filter: None,
            format: ImageDataFormat::RawSamples,
            data,
            warnings: Vec::<ImageWarning>::new(),
        }
    }

    #[test]
    fn decodes_gray_rgb_cmyk_and_one_bit_samples() {
        assert_eq!(
            decode_candidate_image(&raw_image(Some("DeviceGray"), 8, vec![0, 255]))
                .unwrap()
                .to_luma8()
                .into_raw(),
            vec![0, 255]
        );
        assert_eq!(
            decode_candidate_image(&raw_image(Some("DeviceRGB"), 8, vec![1, 2, 3, 4, 5, 6],))
                .unwrap()
                .to_rgb8()
                .into_raw(),
            vec![1, 2, 3, 4, 5, 6]
        );
        assert_eq!(
            decode_candidate_image(&raw_image(
                Some("DeviceCMYK"),
                8,
                vec![0, 0, 0, 0, 255, 0, 0, 0],
            ))
            .unwrap()
            .to_rgb8()
            .into_raw(),
            vec![255, 255, 255, 0, 255, 255]
        );
        assert_eq!(
            decode_candidate_image(&raw_image(Some("DeviceGray"), 1, vec![0b0100_0000]))
                .unwrap()
                .to_luma8()
                .into_raw(),
            vec![0, 255]
        );
    }

    #[test]
    fn rejects_wrong_raw_sample_length() {
        let error = decode_candidate_image(&raw_image(Some("DeviceRGB"), 8, vec![1])).unwrap_err();
        assert_eq!(error.code, OcrErrorCode::ImageDecode);
    }

    #[test]
    fn one_bit_rows_skip_padding_bits() {
        let mut image = raw_image(Some("DeviceGray"), 1, vec![0b0101_1111, 0b1010_0000]);
        image.width = 4;
        image.height = 2;
        assert_eq!(
            decode_candidate_image(&image)
                .unwrap()
                .to_luma8()
                .into_raw(),
            vec![0, 255, 0, 255, 255, 0, 255, 0]
        );
        image.data.truncate(1);
        assert_eq!(
            decode_candidate_image(&image).unwrap_err().code,
            OcrErrorCode::ImageDecode
        );
    }

    #[test]
    fn jpeg_dimensions_must_match_candidate_geometry() {
        let mut bytes = Vec::new();
        image::codecs::jpeg::JpegEncoder::new(&mut bytes)
            .encode(&[0, 255], 2, 1, image::ExtendedColorType::L8)
            .unwrap();
        let mut image = raw_image(Some("DeviceGray"), 8, bytes);
        image.format = ImageDataFormat::Jpeg;
        assert_eq!(decode_candidate_image(&image).unwrap().width(), 2);
        image.width = 1;
        assert_eq!(
            decode_candidate_image(&image).unwrap_err().code,
            OcrErrorCode::ImageDecode
        );
    }
}
