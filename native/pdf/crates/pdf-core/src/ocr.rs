use crate::{
    BBox, ErrorCode, ExtractedImage, LayoutExtractionOptions, ObjectId, PdfDocument, PdfError,
    PdfResult, Point, Quad,
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
        if !options.min_page_coverage.is_finite()
            || !(0.0..=1.0).contains(&options.min_page_coverage)
        {
            return Err(PdfError::new(
                ErrorCode::InvalidOption,
                None,
                "min_page_coverage must be finite and in [0, 1]",
            ));
        }
        let layout = self.extract_layout(LayoutExtractionOptions::default())?;
        let images = self.extract_images()?;
        let mut pages = Vec::with_capacity(layout.pages.len());
        for page in layout.pages {
            let native_chars = page.text.chars().filter(|ch| !ch.is_whitespace()).count();
            if native_chars > options.max_native_text_chars {
                pages.push(rejected(
                    page.page_index,
                    DirectOcrReason::NativeTextPresent,
                ));
                continue;
            }
            let placements: Vec<_> = page
                .image_placements
                .iter()
                .filter(|placement| !placement.artifact)
                .collect();
            let placement = match placements.as_slice() {
                [] => {
                    pages.push(rejected(page.page_index, DirectOcrReason::NoPaintedImage));
                    continue;
                }
                [placement] => *placement,
                _ => {
                    pages.push(rejected(
                        page.page_index,
                        DirectOcrReason::MultiplePaintedImages,
                    ));
                    continue;
                }
            };
            let page_area =
                page.geometry.layout_bounds.width() * page.geometry.layout_bounds.height();
            let coverage =
                visible_quad_area(placement.quad, page.geometry.layout_bounds) / page_area;
            if coverage < options.min_page_coverage {
                pages.push(rejected(
                    page.page_index,
                    DirectOcrReason::ImageDoesNotCoverPage,
                ));
                continue;
            }
            let image = images.iter().find(|image| {
                image.page_index == page.page_index
                    && match (placement.object, image.object_id) {
                        (Some(left), Some(right)) => left == right,
                        _ => image.resource_name == placement.resource_name,
                    }
            });
            let Some(image) = image else {
                pages.push(rejected(
                    page.page_index,
                    DirectOcrReason::ImageResourceMissing,
                ));
                continue;
            };
            let reason = if image.image_mask {
                DirectOcrReason::ImageMaskUnsupported
            } else if image.has_soft_mask {
                DirectOcrReason::SoftMaskUnsupported
            } else if matches!(image.format, crate::ImageDataFormat::EncodedUnknown) {
                DirectOcrReason::UnsupportedImageFormat
            } else {
                DirectOcrReason::Eligible
            };
            let candidate = (reason == DirectOcrReason::Eligible).then(|| DirectOcrCandidate {
                page_index: page.page_index,
                placement_id: placement.id.clone(),
                object_id: image.object_id,
                resource_name: image.resource_name.clone(),
                page_coverage: coverage.min(1.0),
                image_quad: placement.quad,
                image: image.clone(),
            });
            pages.push(DirectOcrPage {
                page_index: page.page_index,
                reason,
                candidate,
            });
        }
        Ok(DirectOcrAnalysis { pages })
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
