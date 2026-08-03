# Stage 12 Stage 1A Definition of Done

Audit date: 2026-07-29 (Asia/Taipei)
Status: PASS

## Delivered contract

- `crates/pdf-core` owns `Point`, normalized `BBox`, `Quad`, `AffineMatrix`,
  `CoordinateSpace`, and `PageGeometry`.
- The public canonical name is exactly `layout_unrotated_top_left`.
- `PageGeometry` materializes MediaBox, CropBox fallback/inheritance, UserUnit,
  normalized quarter-turn rotation, LayoutSpace bounds, DisplaySpace bounds, and
  four explicit inverse transforms.
- Invalid, non-finite, empty, excessive, or non-invertible geometry returns the
  stable `invalid_page_geometry` code. Reordered page boxes emit the stable
  `page_box_reordered` warning.
- The `rust-pdf geometry <file> --json` diagnostic serializes the canonical
  coordinate contract and per-page transforms without extracting document text.

The legacy `PositionedGlyph` API intentionally remains in native PDF user space
through Stage 1A. Stage 1B owns the new public Layout IR and must convert text,
table, image, link, annotation, and citation geometry through the same
`PageGeometry`. This preserves compatibility without mixing coordinate spaces.
The Documa adapter is not changed in Stage 1A; its mapping is implemented in
Stage 6.

## Synthetic coverage

The focused geometry suite proves:

- Point, BBox, and Quad round trips for rotations 0, 90, 180, and 270 degrees,
  including equivalent negative and greater-than-360 rotations.
- CropBox offsets, non-default UserUnit, PDF Y-up to LayoutSpace Y-down, and
  reversible PDF/Layout transforms.
- Reordered boxes, missing MediaBox, invalid rotation, zero-area geometry,
  non-finite values, and stable public codes.
- Inherited and indirect MediaBox, CropBox, and Rotate values, plus page-local
  indirect UserUnit.
- Repeated Form XObject placements reuse one page-level geometry conversion.
- The CLI JSON schema keeps page dimensions and matrices in the declared space.

Six legacy generated fixtures that omitted mandatory MediaBox values were
corrected. The parser remains strict; no undefined fallback page size was added.

## Formal real-corpus parity

The release CLI was rebuilt from the current source and compared with PyMuPDF on
the frozen private corpus:

| Measure | Result |
|---|---:|
| documents | 7 |
| pages | 1,113 |
| tolerance | 0.5 pt |
| mismatches | 0 |
| max layout width delta | 0.000029297 pt |
| max layout height delta | 0 pt |
| max display width delta | 0.000029297 pt |
| max display height delta | 0.000029297 pt |

Formal report:

```text
target/stage12-coordinate-parity/report.json
SHA-256 ebbaba38b3f818369b53895a24e7b25784e774f9a8f2c834f081cd484de36b13
```

The report records scalar geometry deltas only. Its
`contains_extracted_content` value is `false`; no private text, blocks, images,
or IR are stored in the repository.

## Executed gate

```text
python -m py_compile tools\stage12_coordinate_parity.py                 PASS
python tools\stage12_coordinate_parity.py --self-test                  PASS
cargo test -p pdf-core --test stage12_geometry                         7 passed
cargo test -p pdf-core --test stage12_contract                         4 passed
cargo test -p pdf-cli --test geometry                                  1 passed
cargo fmt --all --check                                                 PASS
cargo clippy --workspace --all-targets -- -D warnings                   PASS
cargo test --workspace                                                  PASS
```

The workspace run includes all Rust unit, integration, binding, and doctests.
One pre-existing manual Stage 11 benchmark remains intentionally ignored.

## Completion audit

- [x] Coordinate spaces, units, origin, axes, page boxes, and rotation are explicit.
- [x] All public transforms are finite, invertible, bounded, and directly tested.
- [x] BBox and Quad semantics survive every supported page rotation.
- [x] Inheritance, indirect values, UserUnit, CropBox offsets, and malformed inputs are covered.
- [x] CLI serialization and stable error/warning codes are executable contracts.
- [x] The full frozen corpus matches PyMuPDF page geometry within 0.5 pt.
- [x] Privacy-safe reporting and all stage gates pass.
- [x] Legacy glyph compatibility is explicit; Stage 1B owns Layout IR conversion.

Stage 1A is complete. Stage 1B may begin. Default-provider cutover remains
forbidden until all later quality and integration gates pass.
