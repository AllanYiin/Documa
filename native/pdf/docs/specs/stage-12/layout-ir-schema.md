# Stage 12 Layout IR Schema

Specification version: 1
Status: Stage 1B through Stage 5 complete contract

## Purpose

Layout IR is the versioned, parser-owned boundary between `pdf-core` and all
front ends. It exposes normalized page geometry and source-order text without
requiring Python, JavaScript, CLI, or Documa code to interpret PDF syntax.

Stage 1B establishes the schema and coordinate-safe text projection. Stage 2 adds
tagged structure metadata and order. Stage 3 adds inferred human reading order,
paragraph/list blocks, repeated page furniture, and main flow. Stage 4 adds tagged,
vector-lattice, borderless-text, and fused table topology with cell roles/spans,
LayoutSpace geometry, and source-node links. Stage 5 adds painted image occurrences,
Figure/caption relationships, Link annotations, named destinations, and outlines.
Final Documa mapping remains owned by Stage 6.

## Root contract

`DocumentLayout` contains:

- `schema_version = 1`;
- parser name, package version, and implementation stage;
- `coordinate_space = "layout_unrotated_top_left"`;
- the effective options and a stable SHA-256 options digest;
- explicit capability flags;
- source-order document text, pages, warnings, quality, and optional timings.

Timings are opt-in and excluded by default so identical input and options have a
deterministic serialized result. Debug glyphs are also opt-in. Normal output is
block/span level.

## Page contract

Every `PageLayout` contains the PDF page object id, canonical `PageGeometry`,
source-order text, semantic nodes, typed table and image-placement arrays, and
all four order arrays:

- `source_order`: populated with node ids in PDF source order;
- `tagged_order`: populated from validated Stage 2 structure associations;
- `inferred_order`: complete deterministic visual order from Stage 3, including furniture and Artifact;
- `main_flow`: inferred relative order excluding only confirmed Header, Footer, PageNumber, and Artifact.

An empty order is never interpreted as a fallback to another order. Consumers
must inspect capabilities and select an available order explicitly.

`PageLayout.visual_reading` is a separate optional attention graph, never another
order array. It references semantic node IDs through atomic visual blocks, marks
block-internal perception as `simultaneous`, exposes multiple focus candidates,
and represents `continue`, `skip_ahead`, and `regression` movements as weighted
edges. `may_be_skipped` preserves peripheral or artifact blocks without forcing
them into a human path. The weights are deterministic heuristic strengths rather
than probabilities. `capabilities.visual_reading` reports availability. Missing
`visual_reading` and the default-false capability remain deserializable for older
schema-version-1 payloads.

## Text nodes and spans

Stage 1B emits one conservative page text block containing bounded source-order
spans. Adjacent positioned glyphs may merge only when source ordinals, style,
MCID, writing mode, rotation, baseline, and geometry are compatible.

Every node and span records:

- a deterministic id;
- text and LayoutSpace geometry;
- confidence and a stable rule id;
- page object id, source-ordinal range, MCIDs, and text origins;
- font resource, size, writing mode, and rotation where applicable.

Glyph origins, advances, baselines, BBoxes, and Quads are projected through the
page's single `PageGeometry`. Horizontal text vertical extent uses the font
descriptor Ascent/Descent when valid, otherwise bounded 800/-200 defaults, and
applies the text matrix plus CTM before the one LayoutSpace projection. Glyph
outlines are not evaluated; Type3 FontMatrix and true vertical-writing extents
remain bounded estimates. The stable warning code is
`layout_text_bbox_estimated` because those boundaries still carry non-perfect
confidence.

## Stage 4 table contract

Tables coexist with semantic nodes and do not replace IDs in any of the four
node-order arrays. `LayoutTable` records tagged, vector-lattice, text-alignment,
or fused evidence; row/column counts; source-node links; optional structure
object; and geometry/provenance. Each cell records zero-based placement, positive
spans, data/header role, text, source-node links, and honest optional geometry for
a logically empty tagged cell.

Valid author topology has priority. Compatible vector evidence may refine BBoxes
and confidence but cannot overwrite author spans or header roles. Conflicting
lower-priority topology preserves the stronger table and emits
`table_evidence_conflict`. All table geometry is LayoutSpace.
`capabilities.tables = true` distinguishes an available detector with zero
accepted tables from an unavailable feature.

## Stage 5A image-placement contract

Each image `Do` occurrence is additive to the page and records a stable page-local
ID, zero-based paint ordinal, nested resource path, optional object ID, LayoutSpace
Quad/BBox, confidence, rule ID, and provenance. The image unit square is projected
from `(0,1)`, `(1,1)`, `(1,0)`, `(0,0)` after the active CTM and Form matrices,
then through `pdf_to_layout` exactly once. Page Rotate remains metadata only.

`capabilities.image_placements = true` means occurrence collection completed,
including the valid empty result. It does not claim pixel decoding, OCR, captions,
or navigation. Image provenance ordinals are in the page-local image-paint domain;
consumers must not compare them with text-glyph source ordinals. A malformed optional
placement is skipped with `image_placement_invalid`; limit breaches remain fatal.

## Stage 5B figure/caption contract

Painted occurrences preserve current marked-content tag, MCID, Alt, and Artifact
state. A validated StructTree Figure association supplies the authoritative
structure object and Alt text. Caption anchors are additive `source_node_ids`:
first author `Caption` roles, then explicit Figure/Fig./CJK caption prefixes within
48 pt and at least 50% horizontal compatibility. Table-owned, furniture, and
Artifact nodes are excluded. Equally plausible candidates remain unlinked with
`figure_caption_ambiguous`; an author figure without an anchor emits
`image_placement_unassigned`. Semantic nodes and all four order arrays are never
removed or rewritten.

## Stage 5C navigation contract

Each page carries bounded Link annotations with an optional LayoutSpace BBox/Quad
and a typed target. `DocumentLayout` carries named destinations and flattened
outline preorder. Targets are `uri`, `go_to`, or `unsupported`; GoTo may retain a
name and/or resolved page object/index plus fit parameters. JavaScript, Launch,
embedded-file execution, and unknown actions are never executed.

`capabilities.navigation = true` means annotation, destination, and outline
collection completed, including a valid empty result. Malformed optional data
emits `navigation_target_invalid`; unsupported actions emit
`navigation_action_unsupported`. Limits on annotations, named destinations,
outline items, and traversal depth are fatal when exceeded.

## Compatibility

- Existing `extract_text` and `extract_text_v2` output shapes remain unchanged.
- Existing `PositionedGlyph` remains native PDF user space.
- Rust adds `PdfDocument::extract_layout`.
- CLI adds `layout`.
- Python adds `extract_layout` backed by one native JSON serializer.
- WASM adds `extractLayout` backed by the same Rust DTO.
- Bindings only translate options, values, serialization errors, and parser errors.

## Stage 1B Definition of Done

- The schema is serializable with exact stable names and a deterministic digest.
- All page, block, span, and optional debug-glyph geometry is LayoutSpace.
- Four orders and capability states are explicit; unavailable semantics stay empty.
- Bounds, IDs, provenance, confidence, rule ids, and stable warnings have tests.
- Rust, CLI, Python, and WASM expose the same schema version and coordinate name.
- Legacy extraction JSON remains byte-shape compatible apart from nondeterministic
  map formatting that consumers must not depend on.
- Focused tests, formatting, Clippy with warnings denied, and workspace tests pass.
- A private-corpus schema/privacy/performance report is recorded without storing IR.
