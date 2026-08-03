use crate::{ErrorCode, PdfError, PdfResult};

#[cfg(feature = "serde")]
use serde::{Deserialize, Serialize};

/// Stable public name for native PDF user space.
pub const PDF_USER_SPACE: &str = "pdf_user_space";
/// Stable public name for unrotated, CropBox-relative, top-left layout space.
pub const LAYOUT_SPACE: &str = "layout_unrotated_top_left";
/// Stable public name for rotated, top-left display space.
pub const DISPLAY_SPACE: &str = "display_rotated_top_left";

const MAX_USER_UNIT: f64 = 75_000.0;
const MAX_PAGE_DIMENSION_PT: f64 = 1_000_000_000.0;

/// A named coordinate space. Serialized values are stable binding contracts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub enum CoordinateSpace {
    #[cfg_attr(feature = "serde", serde(rename = "pdf_user_space"))]
    PdfUserSpace,
    #[cfg_attr(feature = "serde", serde(rename = "layout_unrotated_top_left"))]
    LayoutSpace,
    #[cfg_attr(feature = "serde", serde(rename = "display_rotated_top_left"))]
    DisplaySpace,
}

impl CoordinateSpace {
    /// Return the stable public string for this space.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PdfUserSpace => PDF_USER_SPACE,
            Self::LayoutSpace => LAYOUT_SPACE,
            Self::DisplaySpace => DISPLAY_SPACE,
        }
    }
}

/// One finite two-dimensional point.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    /// Construct a point after rejecting non-finite input.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` if either coordinate is not finite.
    pub fn try_new(x: f64, y: f64) -> PdfResult<Self> {
        if x.is_finite() && y.is_finite() {
            Ok(Self { x, y })
        } else {
            Err(invalid_geometry("point coordinates must be finite"))
        }
    }
}

/// An axis-aligned rectangle in the coordinate space named by its owner.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct BBox {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

impl BBox {
    /// Construct a normalized, non-empty finite rectangle.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` for non-finite or zero-area input.
    pub fn try_new(x0: f64, y0: f64, x1: f64, y1: f64) -> PdfResult<Self> {
        normalize_box([x0, y0, x1, y1]).map(|value| value.0)
    }

    /// Rectangle width in the owning coordinate space.
    #[must_use]
    pub fn width(self) -> f64 {
        self.x1 - self.x0
    }

    /// Rectangle height in the owning coordinate space.
    #[must_use]
    pub fn height(self) -> f64 {
        self.y1 - self.y0
    }

    /// Return the numeric min/max corners in winding order.
    #[must_use]
    pub fn corners(self) -> [Point; 4] {
        [
            Point {
                x: self.x0,
                y: self.y0,
            },
            Point {
                x: self.x1,
                y: self.y0,
            },
            Point {
                x: self.x1,
                y: self.y1,
            },
            Point {
                x: self.x0,
                y: self.y1,
            },
        ]
    }

    /// Transform all corners and return their normalized axis-aligned enclosure.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` if transformed values are not finite.
    pub fn transformed(self, matrix: AffineMatrix) -> PdfResult<Self> {
        let points = self.corners().map(|point| matrix.transform_point(point));
        enclosing_box(&points)
    }
}

/// Four object-relative corners retained for rotated or skewed geometry.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct Quad {
    pub top_left: Point,
    pub top_right: Point,
    pub bottom_right: Point,
    pub bottom_left: Point,
}

impl Quad {
    /// Apply one affine transform while preserving object-relative corner identity.
    #[must_use]
    pub fn transformed(self, matrix: AffineMatrix) -> Self {
        Self {
            top_left: matrix.transform_point(self.top_left),
            top_right: matrix.transform_point(self.top_right),
            bottom_right: matrix.transform_point(self.bottom_right),
            bottom_left: matrix.transform_point(self.bottom_left),
        }
    }

    /// Return the normalized axis-aligned enclosure of this quad.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` if a point is not finite or the enclosure is empty.
    pub fn bounding_box(self) -> PdfResult<BBox> {
        enclosing_box(&[
            self.top_left,
            self.top_right,
            self.bottom_right,
            self.bottom_left,
        ])
    }
}

/// A PDF-style affine matrix using `[a b c d e f]` coefficients.
#[derive(Debug, Clone, Copy, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct AffineMatrix {
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub d: f64,
    pub e: f64,
    pub f: f64,
}

impl AffineMatrix {
    pub const IDENTITY: Self = Self {
        a: 1.0,
        b: 0.0,
        c: 0.0,
        d: 1.0,
        e: 0.0,
        f: 0.0,
    };

    /// Construct a finite affine matrix.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` when any coefficient is not finite.
    #[allow(clippy::many_single_char_names)]
    pub fn try_new(a: f64, b: f64, c: f64, d: f64, e: f64, f: f64) -> PdfResult<Self> {
        let value = Self { a, b, c, d, e, f };
        if value.is_finite() {
            Ok(value)
        } else {
            Err(invalid_geometry("affine matrix entries must be finite"))
        }
    }

    /// Apply this matrix to one point.
    #[must_use]
    pub fn transform_point(self, point: Point) -> Point {
        Point {
            x: point.x * self.a + point.y * self.c + self.e,
            y: point.x * self.b + point.y * self.d + self.f,
        }
    }

    /// Return the inverse matrix.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` for non-finite or singular matrices.
    pub fn inverse(self) -> PdfResult<Self> {
        if !self.is_finite() {
            return Err(invalid_geometry("affine matrix entries must be finite"));
        }
        let determinant = self.a * self.d - self.b * self.c;
        if !determinant.is_finite() || determinant == 0.0 {
            return Err(invalid_geometry("affine matrix must be invertible"));
        }
        Self::try_new(
            self.d / determinant,
            -self.b / determinant,
            -self.c / determinant,
            self.a / determinant,
            (self.c * self.f - self.d * self.e) / determinant,
            (self.b * self.e - self.a * self.f) / determinant,
        )
    }

    const fn is_finite(self) -> bool {
        self.a.is_finite()
            && self.b.is_finite()
            && self.c.is_finite()
            && self.d.is_finite()
            && self.e.is_finite()
            && self.f.is_finite()
    }
}

/// A recoverable page-geometry normalization event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum PageGeometryWarning {
    MediaBoxReordered,
    CropBoxReordered,
}

impl PageGeometryWarning {
    /// Stable warning code shared by both page-box variants.
    #[must_use]
    pub const fn code(self) -> &'static str {
        "page_box_reordered"
    }
}

/// Canonical geometry and all reversible transforms for one PDF page.
#[derive(Debug, Clone, PartialEq)]
#[cfg_attr(feature = "serde", derive(Serialize, Deserialize))]
pub struct PageGeometry {
    pub coordinate_space: CoordinateSpace,
    pub media_box_pdf: BBox,
    pub crop_box_pdf: BBox,
    pub user_unit: f64,
    pub rotation: i32,
    pub layout_bounds: BBox,
    pub display_bounds: BBox,
    pub pdf_to_layout: AffineMatrix,
    pub layout_to_pdf: AffineMatrix,
    pub layout_to_display: AffineMatrix,
    pub display_to_layout: AffineMatrix,
    pub warnings: Vec<PageGeometryWarning>,
}

impl PageGeometry {
    /// Build canonical page geometry from PDF page dictionary values.
    ///
    /// # Errors
    ///
    /// Returns `invalid_page_geometry` for missing validity, unsupported rotation,
    /// invalid `UserUnit`, empty boxes, excessive dimensions, or non-invertible transforms.
    pub fn new(
        media_box: [f64; 4],
        crop_box: Option<[f64; 4]>,
        user_unit: f64,
        rotation: i32,
    ) -> PdfResult<Self> {
        if !user_unit.is_finite() || !(0.0..=MAX_USER_UNIT).contains(&user_unit) || user_unit == 0.0
        {
            return Err(invalid_geometry(
                "UserUnit must be finite and in (0, 75000]",
            ));
        }
        if rotation % 90 != 0 {
            return Err(invalid_geometry(
                "page rotation must be a multiple of 90 degrees",
            ));
        }
        let rotation = rotation.rem_euclid(360);
        let (media_box_pdf, media_reordered) = normalize_box(media_box)?;
        let (crop_box_pdf, crop_reordered) = normalize_box(crop_box.unwrap_or(media_box))?;
        let width = crop_box_pdf.width() * user_unit;
        let height = crop_box_pdf.height() * user_unit;
        validate_page_dimension(width)?;
        validate_page_dimension(height)?;

        let layout_bounds = BBox::try_new(0.0, 0.0, width, height)?;
        let pdf_to_layout = AffineMatrix::try_new(
            user_unit,
            0.0,
            0.0,
            -user_unit,
            -crop_box_pdf.x0 * user_unit,
            crop_box_pdf.y1 * user_unit,
        )?;
        let layout_to_pdf = pdf_to_layout.inverse()?;
        let layout_to_display = display_matrix(rotation, width, height)?;
        let display_to_layout = layout_to_display.inverse()?;
        let display_bounds = if matches!(rotation, 90 | 270) {
            BBox::try_new(0.0, 0.0, height, width)?
        } else {
            layout_bounds
        };
        let mut warnings = Vec::with_capacity(2);
        if media_reordered {
            warnings.push(PageGeometryWarning::MediaBoxReordered);
        }
        if crop_reordered {
            warnings.push(PageGeometryWarning::CropBoxReordered);
        }
        Ok(Self {
            coordinate_space: CoordinateSpace::LayoutSpace,
            media_box_pdf,
            crop_box_pdf,
            user_unit,
            rotation,
            layout_bounds,
            display_bounds,
            pdf_to_layout,
            layout_to_pdf,
            layout_to_display,
            display_to_layout,
            warnings,
        })
    }

    /// Convert a native PDF point to canonical `LayoutSpace`.
    #[must_use]
    pub fn pdf_point_to_layout(&self, point: Point) -> Point {
        self.pdf_to_layout.transform_point(point)
    }

    /// Convert a `LayoutSpace` point back to native PDF user space.
    #[must_use]
    pub fn layout_point_to_pdf(&self, point: Point) -> Point {
        self.layout_to_pdf.transform_point(point)
    }

    /// Convert an unrotated `LayoutSpace` point to rotated `DisplaySpace`.
    #[must_use]
    pub fn layout_point_to_display(&self, point: Point) -> Point {
        self.layout_to_display.transform_point(point)
    }

    /// Convert a rotated `DisplaySpace` point back to `LayoutSpace`.
    #[must_use]
    pub fn display_point_to_layout(&self, point: Point) -> Point {
        self.display_to_layout.transform_point(point)
    }
}

fn display_matrix(rotation: i32, width: f64, height: f64) -> PdfResult<AffineMatrix> {
    match rotation {
        0 => Ok(AffineMatrix::IDENTITY),
        90 => AffineMatrix::try_new(0.0, 1.0, -1.0, 0.0, height, 0.0),
        180 => AffineMatrix::try_new(-1.0, 0.0, 0.0, -1.0, width, height),
        270 => AffineMatrix::try_new(0.0, -1.0, 1.0, 0.0, 0.0, width),
        _ => Err(invalid_geometry("normalized page rotation is invalid")),
    }
}

fn normalize_box(values: [f64; 4]) -> PdfResult<(BBox, bool)> {
    if values.iter().any(|value| !value.is_finite()) {
        return Err(invalid_geometry("page box entries must be finite"));
    }
    let reordered = values[0] > values[2] || values[1] > values[3];
    let value = BBox {
        x0: values[0].min(values[2]),
        y0: values[1].min(values[3]),
        x1: values[0].max(values[2]),
        y1: values[1].max(values[3]),
    };
    if value.width() <= 0.0 || value.height() <= 0.0 {
        return Err(invalid_geometry("page box must have positive area"));
    }
    Ok((value, reordered))
}

fn enclosing_box(points: &[Point]) -> PdfResult<BBox> {
    if points.is_empty()
        || points
            .iter()
            .any(|point| !point.x.is_finite() || !point.y.is_finite())
    {
        return Err(invalid_geometry(
            "geometry points must be finite and non-empty",
        ));
    }
    let x0 = points
        .iter()
        .map(|point| point.x)
        .fold(f64::INFINITY, f64::min);
    let y0 = points
        .iter()
        .map(|point| point.y)
        .fold(f64::INFINITY, f64::min);
    let x1 = points
        .iter()
        .map(|point| point.x)
        .fold(f64::NEG_INFINITY, f64::max);
    let y1 = points
        .iter()
        .map(|point| point.y)
        .fold(f64::NEG_INFINITY, f64::max);
    BBox::try_new(x0, y0, x1, y1)
}

fn validate_page_dimension(value: f64) -> PdfResult<()> {
    if value.is_finite() && value > 0.0 && value <= MAX_PAGE_DIMENSION_PT {
        Ok(())
    } else {
        Err(invalid_geometry(
            "page dimensions must be finite, positive, and bounded",
        ))
    }
}

fn invalid_geometry(message: &str) -> PdfError {
    PdfError::new(ErrorCode::InvalidPageGeometry, None, message)
}
