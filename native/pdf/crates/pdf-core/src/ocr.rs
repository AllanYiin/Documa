use crate::{
    BBox, ErrorCode, ExtractedImage, ObjectId, PdfDocument, PdfError, PdfObject, PdfResult, Point,
    Quad, TextExtractionOptions,
};

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DirectOcrOptions {
    pub min_page_coverage: f64,
    pub max_native_text_chars: usize,
}

impl Default for DirectOcrOptions {
    fn default() -> Self {
        Self {
            min_page_coverage: 0.9,
            max_native_text_chars: 0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum DirectOcrReason {
    Eligible,
    NativeTextPresent,
    NoPaintedImage,
    MultiplePaintedImages,
    ImageDoesNotCoverPage,
    ImageResourceMissing,
    ImageMaskUnsupported,
    SoftMaskUnsupported,
    UnsupportedImageFormat,
    DecodeArrayUnsupported,
    ExplicitMaskUnsupported,
    ClippingUnsupported,
    GraphicsStateUnsupported,
    InlineImageUnsupported,
    OtherPageMarks,
    InvalidImageData,
    InvalidImagePlacement,
}

impl DirectOcrReason {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Eligible => "eligible",
            Self::NativeTextPresent => "native_text_present",
            Self::NoPaintedImage => "no_painted_image",
            Self::MultiplePaintedImages => "multiple_painted_images",
            Self::ImageDoesNotCoverPage => "image_does_not_cover_page",
            Self::ImageResourceMissing => "image_resource_missing",
            Self::ImageMaskUnsupported => "image_mask_unsupported",
            Self::SoftMaskUnsupported => "soft_mask_unsupported",
            Self::UnsupportedImageFormat => "unsupported_image_format",
            Self::DecodeArrayUnsupported => "decode_array_unsupported",
            Self::ExplicitMaskUnsupported => "explicit_mask_unsupported",
            Self::ClippingUnsupported => "clipping_unsupported",
            Self::GraphicsStateUnsupported => "graphics_state_unsupported",
            Self::InlineImageUnsupported => "inline_image_unsupported",
            Self::OtherPageMarks => "other_page_marks",
            Self::InvalidImageData => "invalid_image_data",
            Self::InvalidImagePlacement => "invalid_image_placement",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct OcrImageQuad {
    pub top_left: [f32; 2],
    pub top_right: [f32; 2],
    pub bottom_right: [f32; 2],
    pub bottom_left: [f32; 2],
}

#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct OcrPixelBlock {
    pub text: String,
    pub confidence: f32,
    pub quad: OcrImageQuad,
}

#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct OcrLayoutBlock {
    pub text: String,
    pub confidence: f32,
    pub bbox: BBox,
    pub quad: Quad,
}

#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DirectOcrCandidate {
    pub page_index: usize,
    pub placement_id: String,
    pub object_id: Option<ObjectId>,
    pub resource_name: String,
    pub page_coverage: f64,
    pub image_quad: Quad,
    pub image: ExtractedImage,
}

impl DirectOcrCandidate {
    /// Map a backend block into canonical `LayoutSpace`.
    ///
    /// # Errors
    ///
    /// Returns a stable geometry or limit error for invalid backend output.
    pub fn map_block(&self, block: &OcrPixelBlock) -> PdfResult<OcrLayoutBlock> {
        if block.text.len() > 1_048_576 {
            return Err(PdfError::new(
                ErrorCode::LimitExceeded,
                None,
                "OCR block text length limit exceeded",
            ));
        }
        if !block.confidence.is_finite() {
            return Err(invalid_ocr_geometry("OCR confidence must be finite"));
        }
        let quad = Quad {
            top_left: self.map_pixel(block.quad.top_left)?,
            top_right: self.map_pixel(block.quad.top_right)?,
            bottom_right: self.map_pixel(block.quad.bottom_right)?,
            bottom_left: self.map_pixel(block.quad.bottom_left)?,
        };
        Ok(OcrLayoutBlock {
            text: block.text.clone(),
            confidence: block.confidence.clamp(0.0, 1.0),
            bbox: quad.bounding_box()?,
            quad,
        })
    }

    fn map_pixel(&self, pixel: [f32; 2]) -> PdfResult<Point> {
        let x = f64::from(pixel[0]);
        let y = f64::from(pixel[1]);
        let width = f64::from(self.image.width);
        let height = f64::from(self.image.height);
        if !x.is_finite() || !y.is_finite() || width == 0.0 || height == 0.0 {
            return Err(invalid_ocr_geometry("OCR pixel coordinates must be finite"));
        }
        if x < 0.0 || y < 0.0 || x > width || y > height {
            return Err(invalid_ocr_geometry(
                "OCR pixel coordinates are outside the image",
            ));
        }
        let u = x / width;
        let v = y / height;
        let tl = self.image_quad.top_left;
        let tr = self.image_quad.top_right;
        let bl = self.image_quad.bottom_left;
        Point::try_new(
            tl.x + u * (tr.x - tl.x) + v * (bl.x - tl.x),
            tl.y + u * (tr.y - tl.y) + v * (bl.y - tl.y),
        )
    }
}

#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DirectOcrPage {
    pub page_index: usize,
    pub reason: DirectOcrReason,
    pub candidate: Option<DirectOcrCandidate>,
}

#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct DirectOcrAnalysis {
    pub pages: Vec<DirectOcrPage>,
}

impl PdfDocument {
    /// Analyze every page for the conservative direct-image OCR path.
    ///
    /// # Errors
    ///
    /// Returns a stable parser, geometry, option, image, or resource limit error.
    pub fn analyze_direct_ocr(&self, options: DirectOcrOptions) -> PdfResult<DirectOcrAnalysis> {
        self.analyze_direct_ocr_selected(options, &crate::PageSelection::all())
    }

    /// Collect selected candidates with an aggregate retained-image byte bound.
    /// Use the iterator for scans whose cumulative images exceed this bound.
    ///
    /// # Errors
    ///
    /// Returns parser, option or limit errors. Retained image bytes may not exceed
    /// the document's configured `max_total_decoded_bytes`.
    pub fn analyze_direct_ocr_selected(
        &self,
        options: DirectOcrOptions,
        selection: &crate::PageSelection,
    ) -> PdfResult<DirectOcrAnalysis> {
        let mut pages = Vec::new();
        let mut retained = 0_usize;
        for page in self.direct_ocr_pages_selected(options, selection)? {
            let page = page?;
            let bytes = page
                .candidate
                .as_ref()
                .map_or(0, |candidate| candidate.image.data.len());
            if bytes > self.limits.max_total_decoded_bytes.saturating_sub(retained) {
                return Err(PdfError::new(
                    ErrorCode::LimitExceeded,
                    None,
                    "collected OCR candidate image bytes exceed the document output budget",
                ));
            }
            retained += bytes;
            pages.push(page);
        }
        Ok(DirectOcrAnalysis { pages })
    }

    /// Analyze and decode only one painted image candidate at a time.
    /// Dropping the iterator cancels further page work. Errors terminate it.
    ///
    /// # Errors
    ///
    /// Returns option or page-tree errors now; later content errors are yielded.
    pub fn direct_ocr_pages(&self, options: DirectOcrOptions) -> PdfResult<DirectOcrPageProducer> {
        self.direct_ocr_pages_selected(options, &crate::PageSelection::all())
    }

    /// Lazily analyze selected pages, preserving original page indices.
    ///
    /// # Errors
    ///
    /// Returns selection, option or page-tree errors; later page errors are yielded.
    pub fn direct_ocr_pages_selected(
        &self,
        options: DirectOcrOptions,
        selection: &crate::PageSelection,
    ) -> PdfResult<DirectOcrPageProducer> {
        if !options.min_page_coverage.is_finite()
            || !(0.0..=1.0).contains(&options.min_page_coverage)
        {
            return Err(PdfError::new(
                ErrorCode::InvalidOption,
                None,
                "min_page_coverage must be finite and in [0, 1]",
            ));
        }
        let pages = selection.select(self.pages()?)?;
        Ok(DirectOcrPageProducer {
            document: self.clone(),
            text_pages: self.extract_text_page_producer(
                &pages,
                TextExtractionOptions {
                    normalize_unicode: false,
                    layout: false,
                },
                false,
                true,
            ),
            options,
            done: false,
        })
    }
}

/// Owned, fallible direct-image candidate iterator. Retains no accepted page payload.
pub struct DirectOcrPageProducer {
    document: PdfDocument,
    text_pages: crate::text::TextPageProducer,
    options: DirectOcrOptions,
    done: bool,
}

impl Iterator for DirectOcrPageProducer {
    type Item = PdfResult<DirectOcrPage>;

    fn next(&mut self) -> Option<Self::Item> {
        if self.done {
            return None;
        }
        let Some(page) = self.text_pages.next() else {
            self.done = true;
            return None;
        };
        let result = page.and_then(|page| analyze_page(&self.document, page, self.options));
        if result.is_err() {
            self.done = true;
        }
        Some(result)
    }
}

impl std::iter::FusedIterator for DirectOcrPageProducer {}

fn analyze_page(
    document: &PdfDocument,
    extracted: crate::text::ExtractedPage,
    options: DirectOcrOptions,
) -> PdfResult<DirectOcrPage> {
    let page = extracted.source_page;
    let reject = |reason| Ok(rejected(page.index, reason));
    if extracted
        .page
        .text
        .chars()
        .filter(|ch| !ch.is_whitespace())
        .count()
        > options.max_native_text_chars
    {
        return reject(DirectOcrReason::NativeTextPresent);
    }
    let paths = extracted.vector_paths.transpose()?.unwrap_or_default();
    if let Some(reason) = paths.direct_ocr_rejection {
        return reject(reason);
    }
    if paths.image_placement_error.is_some() {
        return reject(DirectOcrReason::InvalidImagePlacement);
    }
    let mut placements = paths
        .image_placements
        .into_iter()
        .filter(|placement| !placement.artifact);
    let Some(placement) = placements.next() else {
        return reject(DirectOcrReason::NoPaintedImage);
    };
    if placements.next().is_some() {
        return reject(DirectOcrReason::MultiplePaintedImages);
    }
    let bounds = page.geometry.layout_bounds;
    let coverage = visible_quad_area(placement.quad, bounds) / (bounds.width() * bounds.height());
    if coverage < options.min_page_coverage || coverage <= 0.0 {
        return reject(DirectOcrReason::ImageDoesNotCoverPage);
    }
    let Some(object) = placement.object else {
        return reject(DirectOcrReason::ImageResourceMissing);
    };
    let PdfObject::Stream(stream) = document.object(object)?.value else {
        return reject(DirectOcrReason::ImageResourceMissing);
    };
    let image = crate::images::extract_image(
        document,
        page.index,
        placement.resource_name,
        Some(object),
        &stream,
    )?;
    let has_warning = |code: &str| image.warnings.iter().any(|warning| warning.code == code);
    let reason = if image.image_mask {
        DirectOcrReason::ImageMaskUnsupported
    } else if image.has_soft_mask {
        DirectOcrReason::SoftMaskUnsupported
    } else if has_warning("image_explicit_mask_unsupported") {
        DirectOcrReason::ExplicitMaskUnsupported
    } else if has_warning("image_decode_array_unsupported") {
        DirectOcrReason::DecodeArrayUnsupported
    } else if image.width == 0
        || image.height == 0
        || has_warning("jpeg_dimension_mismatch")
        || has_warning("raw_sample_length_mismatch")
    {
        DirectOcrReason::InvalidImageData
    } else if !supported_samples(&image) {
        DirectOcrReason::UnsupportedImageFormat
    } else {
        DirectOcrReason::Eligible
    };
    if reason != DirectOcrReason::Eligible {
        return reject(reason);
    }
    Ok(DirectOcrPage {
        page_index: page.index,
        reason,
        candidate: Some(DirectOcrCandidate {
            page_index: page.index,
            placement_id: placement.id,
            object_id: image.object_id,
            resource_name: image.resource_name.clone(),
            page_coverage: coverage.min(1.0),
            image_quad: placement.quad,
            image,
        }),
    })
}

fn supported_samples(image: &ExtractedImage) -> bool {
    match image.format {
        crate::ImageDataFormat::EncodedUnknown => false,
        crate::ImageDataFormat::Jpeg => matches!(
            image.color_space.as_deref(),
            Some("DeviceGray" | "DeviceRGB" | "DeviceCMYK")
        ),
        crate::ImageDataFormat::RawSamples => matches!(
            (image.color_space.as_deref(), image.bits_per_component),
            (Some("DeviceGray"), 1 | 8) | (Some("DeviceRGB" | "DeviceCMYK"), 8)
        ),
    }
}

fn rejected(page_index: usize, reason: DirectOcrReason) -> DirectOcrPage {
    DirectOcrPage {
        page_index,
        reason,
        candidate: None,
    }
}

fn visible_quad_area(quad: Quad, bounds: BBox) -> f64 {
    let mut points = vec![
        quad.top_left,
        quad.top_right,
        quad.bottom_right,
        quad.bottom_left,
    ];
    // Clip the convex image quad against all four page edges. A bounding-box
    // intersection overestimates coverage for rotated or skewed images.
    // Four clipping passes over a quad produce at most eight vertices.
    for (axis, boundary, keep_greater) in [
        (0, bounds.x0, true),
        (0, bounds.x1, false),
        (1, bounds.y0, true),
        (1, bounds.y1, false),
    ] {
        let coordinate = |point: Point| if axis == 0 { point.x } else { point.y };
        let inside = |point: Point| {
            if keep_greater {
                coordinate(point) >= boundary
            } else {
                coordinate(point) <= boundary
            }
        };
        let Some(&last) = points.last() else {
            return 0.0;
        };
        let mut clipped = Vec::with_capacity(8);
        let mut previous = last;
        for &current in &points {
            if inside(previous) != inside(current) {
                let fraction = (boundary - coordinate(previous))
                    / (coordinate(current) - coordinate(previous));
                clipped.push(Point {
                    x: previous.x + fraction * (current.x - previous.x),
                    y: previous.y + fraction * (current.y - previous.y),
                });
            }
            if inside(current) {
                clipped.push(current);
            }
            previous = current;
        }
        points = clipped;
    }
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .take(points.len())
        .map(|(a, b)| a.x * b.y - b.x * a.y)
        .sum::<f64>()
        .abs()
        / 2.0
}

fn invalid_ocr_geometry(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidPageGeometry, None, message)
}
