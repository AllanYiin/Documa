# Stage 12 Stage 7.3A BBox Fidelity Definition of Done

Status: Stage 7.3A complete; Stage 7.3B may begin; Stage 7.3C and Stage 7.4 remain forbidden

## Scope and contract

- Horizontal text BBoxes use effective text geometry after the text matrix and CTM.
- Valid FontDescriptor Ascent/Descent values define vertical extent. Missing or invalid
  values use bounded 800/-200 defaults; input metrics are finite and clamped.
- Text extraction carries one internal geometry sidecar per positioned glyph. Public
  PositionedGlyph and serialized V2 text DTO shapes are unchanged.
- LayoutSpace remains CropBox top-left, x right, y down, UserUnit points, with page
  Rotate unapplied. No second coordinate projection was added.
- Parser BBoxes preserve geometric content that partially crosses the CropBox. The
  private review overlay intersects display boxes with display bounds so reviewers
  only interact with the visible page; this presentation clipping does not rewrite IR.
- The stable warning code remains `layout_text_bbox_estimated`. Glyph outlines,
  Type3 FontMatrix, and true vertical-writing extents remain explicit boundaries.

## Public regression evidence

- `text_bbox_uses_effective_text_matrix_scale_and_font_vertical_metrics` proves a
  1 pt Tf scaled by a 12x text matrix produces a 12 pt vertical box from 750/-250
  font metrics.
- `text_bbox_uses_ctm_scale_and_bounded_default_vertical_metrics` proves text-matrix
  and CTM vertical scales compose and the bounded default metrics are applied.
- The order-review packet self-test proves partially visible boxes are clipped to
  display bounds and fully invisible boxes are rejected from the rendered overlay.
- Existing extraction text, source ordinals, marked-content alignment, and public
  serialization contracts remain covered by the full workspace suite.

## Private 7-document / 28-page evidence

The v6 and v7b packets use the same deterministic page sample. All derived pages,
HTML, and manifests remain private under `target/` and must not be committed.

| Metric | v6 baseline | v7b Stage 7.3A |
|---|---:|---:|
| Visible semantic nodes | 1,495 | 993 |
| Median nodes/page | 40.5 | 19.5 |
| Median bbox height as page percent | 0.126263% | 1.202020% |
| BBoxes below 0.5% page height | 1,313 (87.8261%) | 5 (0.5035%) |
| BBoxes below 1.0% page height | 1,365 (91.3043%) | 423 (42.5982%) |
| Rendered boxes outside display bounds | 11 | 0 |

Visual comparison covered a mixed chart/text page, the maximum-density 172-node
page, a complex table page, formulas, footnotes, headers, footers, and page numbers.
The former baseline-thin boxes now cover visible glyph height; no global axis flip
or second rotation was observed. Large paragraph/table envelopes remain intentional
semantic-node unions and will be handled as brush-selected blocks in Stage 7.3C.

- v7b packet-index SHA-256:
  `0a5b0f7b5b1e9d1cba7436d43f8b5cc2593a483303a54de58802277b966d1873`
- v7b draft-manifest SHA-256:
  `889c3d4339782b2db4f9bf600a6feb2e3352961763d3b9489acd36ee37fa84b4`
- Metadata privacy scan found no source PDF filename, absolute path, or file URL.

## Text quality and performance guard

The Stage 7.2 comparator was rerun on all 1,113 pages using the exact new wheel.
Aggregate non-whitespace character F1 remained exactly
`0.9989543801655596`. Rust layout-source throughput was 130.092516 pages/s
versus 113.087502 pages/s in the earlier calibration. This is a one-pass
no-regression guard, not a statistically controlled speedup claim.

- Privacy-safe report SHA-256:
  `8e788ab410b87396a288dfcf545df7fddfda2e718668d2ec3b13b4e6c40a4ee6`

## Gate decision

Stage 7.3A is accepted. Stage 7.3B may implement block-level gold schema v2,
validation, and scoring. The click-per-node v6 workbench is historical engineering
evidence only and must not be used for human review. Stage 7.3C brush UI cannot
begin until the schema v2 stage gate passes; Stage 7.4 remains forbidden until two
independent human reviews and adjudication are complete.
