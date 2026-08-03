# Stage 12 Stage 5 Image Placement and Navigation Contract

Specification version: 1
Status: Complete; Stages 5A through 5D accepted

## Goal

Expose each painted image occurrence, its exact page geometry, its relationship to
nearby or tagged figure text, and navigation metadata needed by Documa. Preserve
the existing image-byte extraction API while adding layout occurrences to the
shared Rust Layout IR.

## Ownership boundary

`pdf-core` owns PDF content operators, graphics/Form state, image XObject
resolution, annotations, destinations, outlines, tagged Figure associations,
geometry, limits, stable IDs, confidence, and warnings. CLI, Python, and WASM only
serialize the shared DTO. Documa owns document-level caption semantics, chunking,
search, citation presentation, cross-page reasoning, and LLM interpretation.

Stage 5 does not add OCR, raster rendering, image classification, object
detection, chart understanding, or an LLM to Rust.

## Canonical coordinate contract

Every public image placement uses `layout_unrotated_top_left`. A PDF image XObject
paints the unit square. Its object-relative corners are:

```text
top_left     = (0, 1)
top_right    = (1, 1)
bottom_right = (1, 0)
bottom_left  = (0, 0)
```

Apply the active content CTM, including nested Form matrices and `q/Q/cm`, then
apply `PageGeometry.pdf_to_layout` exactly once. Preserve corner identity in the
serialized Quad; derive BBox only as the min/max envelope. Page Rotate remains
unapplied, matching every other Layout IR field.

## Substages

### Stage 5A: painted image occurrences

Collect image `Do` occurrences during the same bounded page/Form graphics
traversal used by vector paths. A resource referenced multiple times produces
multiple placements. Record stable page-local ID, resource path, optional object
ID, LayoutSpace Quad/BBox, paint ordinal, confidence, rule ID, and page-object
provenance. Do not decode image pixels merely to determine placement.

Support inherited Form resources, Form matrices, direct/indirect XObjects,
repeated placements, mirroring, rotation, and shear. Soft masks and thumbnails
are not independent placements unless painted by a content operator. Inline images
are an explicit later extension because the current content lexer skips their
payload as one bounded token.

### Stage 5B: figure and caption flow

Retain marked-content MCID/Artifact context for painted occurrences. A valid
tagged Figure association has precedence for figure role, Alt text, and reading
position. Geometry may propose a nearby caption only when it is outside a table,
not furniture/artifact, horizontally compatible, and separated by a bounded gap.
Ambiguous candidates remain ordinary text and emit one aggregated warning.

Stage 5B links placements to preserved `source_node_ids`; it never deletes or
rewrites semantic nodes or any of the four node-order arrays. Consumers insert a
figure at its explicit tagged position or the nearest compatible source node.

### Stage 5C: navigation metadata

Expose bounded Link annotations, URI/GoTo actions, named destinations, and outline
entries with stable targets. Annotation rectangles/quads use LayoutSpace.
JavaScript, Launch, embedded-file execution, and unknown actions are retained only
as warned unsupported metadata and are never executed.

### Stage 5D: parity and benchmark

Validate nonempty image placements and navigation DTOs through Rust, CLI, Python,
and WASM. Run the frozen 7-document privacy-safe benchmark with one warm-up and
three measured release runs. Report occurrences, unique image objects, tagged
figures, caption links, navigation counts, durations, peak RSS, serialized size,
warnings, and deterministic hashes without storing text, image bytes, URLs, or
private IR.

## Layout IR contract

`LayoutImagePlacement` contains:

- stable ID and zero-based page paint ordinal;
- resource path and optional image object ID;
- LayoutSpace Quad and normalized BBox;
- ordered `source_node_ids` when Stage 5B establishes a relationship;
- optional structure object and Alt text only when author metadata supplies them;
- confidence, stable rule ID, and provenance.

Image provenance ordinal fields use the page-local image-paint domain. They are
stable across skipped invalid placements but must not be compared numerically with
text-glyph source_ordinal values; paint_ordinal is the explicit public key.

`capabilities.image_placements = true` means occurrence collection completed,
including documents with zero painted images. It does not mean pixel decoding,
OCR, caption inference, or navigation are available.

## Bounds and recovery

- Reuse `max_images` as the document-wide painted-occurrence limit and
  `max_image_pixels` only for byte extraction/decoding.
- Reuse content-operation, Form-depth, object-depth, stream, and document decode
  budgets. All ordinals and derived counts use checked arithmetic.
- `max_annotations`, `max_named_destinations`, and `max_outline_items` bound
  document-wide navigation growth; outline/name-tree depth reuses `max_object_depth`.
- A limit breach is fatal `limit_exceeded`.
- Malformed optional placement/navigation data preserves text and other layout
  data with an aggregated stable warning.

Stable warning codes introduced by Stage 5 are:

- `image_placement_invalid`;
- `image_placement_unassigned`;
- `figure_caption_ambiguous`;
- `navigation_target_invalid`;
- `navigation_action_unsupported`.

## Validation

Redistributable fixtures cover direct and Form-nested images, repeated resource
use, q/Q restoration, translation/scale/rotation/shear/mirroring, CropBox,
UserUnit, page Rotate metadata, direct/indirect resources, malformed/cyclic Forms,
exact and one-over occurrence limits, tagged Figure/Alt/Artifact, caption positive
and negative cases, annotation geometry, URI/GoTo destinations, outlines, and
unsupported actions.

## Frozen-corpus result

The Stage 5D run covered 7 PDFs / 1,113 pages with one warm-up and three
measured release runs. It reached 195.707651 pages/s and 26.930568x the frozen
complete Documa adapter. Versus Stage 4, throughput was 0.951276x, peak RSS was
1.000650x, and serialized size was 1.290143x. All schema, privacy, and byte-
determinism audits passed. The output-size increase is carried into Stage 6 as a
page-level or streaming adapter requirement.

## Definition of Done

- [x] Coordinate direction, corner identity, transform order, ownership, and
  non-goals are specified before implementation.
- [x] Painted image occurrences pass exact Quad/BBox/object/resource/ordinal tests.
- [x] Repeated and nested Form placements are deterministic and bounded.
- [x] Tagged figures and conservative caption links preserve all source nodes/orders.
- [x] Link, destination, and outline metadata is safe and bounded.
- [x] Rust, CLI, Python, and WASM expose identical additive data.
- [x] Frozen-corpus report is deterministic, privacy-safe, and records speed,
  memory, size, counts, warnings, and hashes.
- [x] Focused tests, formatting, denied-warning Clippy, and workspace tests pass.
- [x] Default-provider cutover remains closed unless every global Go/No-Go gate passes.