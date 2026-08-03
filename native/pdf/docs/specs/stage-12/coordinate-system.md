# Stage 12 Coordinate-System Contract

Specification version: 1  
Status: implemented and verified in Stage 1A; mandatory for Stage 1B onward

## Public default

Every public Layout IR point, BBox, Quad, baseline, advance, and page dimension
uses this convention:

```text
coordinate_space = "layout_unrotated_top_left"
unit = "pt"
x_direction = "right"
y_direction = "down"
rotation_applied = false
origin_box = "crop_box"
```

If CropBox is absent, use MediaBox. Never mix page dimensions and object boxes
from different coordinate spaces in one `PageLayout`.

## Three named spaces

### PdfUserSpace

- Native space used by PDF content streams, text matrices, and the CTM.
- X is normally right and Y normally up; a CTM may change either direction.
- Kept for provenance and diagnostics, not as Documa's public BBox space.

### LayoutSpace

- CropBox top-left is `(0, 0)`; X goes right; Y goes down.
- Page `/Rotate` is not applied.
- All public Layout IR geometry and Documa BBox values use this space.

### DisplaySpace

- Displayed page top-left is `(0, 0)`; X goes right; Y goes down.
- Normalized page `/Rotate` is applied.
- Reading order, visual zoning, and preview overlays may use this derived space.

## Units and page boxes

- PDF `/UserUnit` defaults to `1.0`; public values are converted to pt.
- Normalize MediaBox and CropBox to `x0 <= x1` and `y0 <= y1`.
- Reordered box coordinates emit `page_box_reordered`.
- Zero-area, non-finite, or non-invertible geometry returns the stable
  `invalid_page_geometry` error.

For normalized CropBox `[cx0, cy0, cx1, cy1]` and UserUnit `u`:

```text
x_layout = (x_pdf - cx0) * u
y_layout = (cy1 - y_pdf) * u
layout_width = (cx1 - cx0) * u
layout_height = (cy1 - cy0) * u
```

## Rotation

- Normalize `/Rotate` to `0 | 90 | 180 | 270`; accept equivalent multiples.
- Reject rotations that are not multiples of 90 degrees.
- Public BBox remains in LayoutSpace; preserve `rotation` independently.
- `layout_to_display` computes DisplaySpace; 90/270 swap display dimensions.
- Layout algorithms, including reading order and table reconstruction, consume
  LayoutSpace points, Quad, baseline, and writing direction. DisplaySpace is a
  derived preview/overlay space only; public serialization remains LayoutSpace.

## Geometry types

```rust
pub struct Point { pub x: f64, pub y: f64 }
pub struct BBox { pub x0: f64, pub y0: f64, pub x1: f64, pub y1: f64 }
pub struct Quad {
    pub top_left: Point,
    pub top_right: Point,
    pub bottom_right: Point,
    pub bottom_left: Point,
}
```

- BBox is axis-aligned and normalized.
- Rotated text, images, and paths retain Quad or path geometry; BBox is only the
  enclosing rectangle.
- All public numbers must be finite.
- Internal matrix round-trip tolerance is `1e-6 pt`.
- PyMuPDF baseline BBox parity tolerance is `0.5 pt`, and a tolerance must never
  conceal a coordinate-space or direction mismatch.

## Required per-page transforms

```rust
pub struct PageGeometry {
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
}
```

All four matrices must be explicit and invertible. Text, table, image, link,
annotation, and citation geometry must reuse the same `PageGeometry`.

## Stage 1A Definition of Done

- Point, Quad, and BBox round trips pass for rotations 0/90/180/270.
- CropBox offsets, UserUnit, indirect/inherited page values, and reordered boxes
  have direct regression coverage.
- Page dimensions, PDF/Layout transforms, and Layout/Display transforms are
  serialized by the CLI with the exact public coordinate-space name.
- The frozen 7-document, 1,113-page corpus has zero PyMuPDF parity mismatches at
  0.5 pt tolerance; the privacy-safe report contains no extracted content.
- Legacy glyph coordinates remain explicitly native until Stage 1B introduces
  Layout IR. The Documa adapter reuses this contract when implemented in Stage 6.
- Focused tests, formatting, Clippy with warnings denied, and workspace tests pass.

Completion evidence: `tests/fixtures/stage12/stage1a-dod.md`.
