# Stage 12 Stage 3 Human Reading Order Contract

Specification version: 1
Status: Complete (2026-07-29)

## Goal

Produce deterministic paragraph blocks and a human-oriented reading order from
LayoutSpace geometry while preserving independent source and tagged orders.
Classify repeated headers, footers, and page numbers without deleting their text,
and expose a main-flow order that excludes confirmed page furniture and Artifact
content.

## Ownership boundary

`pdf-core` owns bounded, explainable document-layout rules: line clustering,
column detection, paragraph grouping, repeated-furniture matching, order arrays,
confidence, provenance, warnings, and deterministic tie breaks. Bindings only
serialize the shared Layout IR. Documa continues to own topic semantics, section
interpretation, chunking, search, citations, domain policy, and LLM reasoning.
Stage 3 adds no LLM or embedding call to Rust.

## Non-goals

- No table cell topology; Stage 4 owns tables.
- No image placement, link, outline, destination, or OBJR order; Stage 5 owns them.
- No OCR, rendering, decryption, damaged-xref repair, or page raster analysis.
- No topic, heading-content, section, or document-domain semantic classifier.
- No silent deletion of headers, footers, page numbers, artifacts, or ambiguous text.
- No Documa default-provider change.

## Coordinate and precedence rules

All decisions use `layout_unrotated_top_left`: x grows right, y grows down, units
are points after UserUnit, CropBox is the origin, and page Rotate is not applied.
DisplaySpace and PDF-user-space values must never enter the heuristic.

The four orders remain independent:

1. `source_order` is complete and sorted by minimum source ordinal.
2. `tagged_order` remains author-structure order from Stage 2.
3. `inferred_order` contains every inferred text node, including page furniture
   and Artifact nodes, in deterministic visual reading order.
4. `main_flow` preserves inferred relative order but excludes nodes classified as
   Header, Footer, PageNumber, or Artifact.

A non-empty tagged order does not overwrite inferred order. Consumers choose an
order explicitly from capability flags.

## Additive visual-attention graph

`PageLayout.visual_reading` is an additive, non-linear interpretation layer. It
is not a fifth order, does not replace any of the four arrays, and does not claim
a single human scanpath. The current attention unit is one semantic text block.
Each block carries `internal_order = simultaneous`, so consumers must not infer a
serial order among spans merely from their array position.

The graph contains:

- up to three `focus_candidates`, allowing several plausible entry points;
- weighted `continue`, `skip_ahead`, and `regression` transitions, allowing
  branching, block omission, and return movement;
- `may_be_skipped` on Artifact, page-furniture, and peripheral-margin blocks;
- stable cues such as heading, relative large text, top entry, central placement,
  table anchor, image anchor, and peripheral content.

Salience and transition weights are deterministic layout-heuristic strengths in
[0,1], not calibrated gaze probabilities or human ground truth. They use geometry,
font size, roles, table source links, and figure/caption anchors already owned by
`pdf-core`; bindings only serialize the DTO. The implementation emits at most a
constant number of transitions per block, so it remains O(n) after paragraph
construction and is bounded by existing parser limits.

This representation is deliberately additive while the Stage 7.3 real-human gold
gate remains blocked. It does not authorize Stage 7.4 ordering changes, change the
default provider, or claim that the heuristic passed human scanpath validation.

## Stage 3 pipeline

### 1. Span normalization

Flatten Stage 2 node spans per page while retaining tag, Alt, ActualText,
Artifact, MCIDs, style, geometry, source ordinals, confidence, and provenance.
Never split one span or synthesize glyph-level data when debug glyphs are absent.
Only finite LayoutSpace BBoxes participate.

### 2. Line clustering

For horizontal, unrotated spans, cluster into a line when baseline/vertical
center distance is within a font-relative tolerance and vertical overlap is
compatible. Sort line members by x0, then y0, then source ordinal, then stable ID.
Insert a deterministic space between adjacent Latin-like spans only when geometry
shows a word gap and neither side already carries whitespace. CJK adjacency does
not receive a synthetic general gap space.

Vertical or rotated groups remain bounded and deterministic but use conservative
source-order fallback with `reading_order_ambiguous` until a dedicated rule has
sufficient confidence.

### 3. Column and region detection

Use a bounded recursive XY-cut over line BBoxes. Prefer a vertical whitespace cut
only when the gap is materially larger than the local median character/line
extent and separates non-trivial groups; otherwise prefer a horizontal cut.
Tie breaks are gap size descending, axis preference from writing mode, coordinate
ascending, then minimum source ordinal. Overlapping or weak cuts stay in one
region and emit an aggregated ambiguity warning rather than forcing columns.

The visual order is top-to-bottom for regions and lines, with left-to-right
columns for horizontal Latin/CJK pages. Tagged order never acts as a hidden
fallback for untagged nodes.

### 4. Paragraph grouping

Consecutive lines in one region join a paragraph when all applicable checks pass:

- compatible writing mode and rotation;
- vertical gap no greater than a font/line-height-relative threshold;
- horizontal overlap or indentation compatible with the prior line;
- no strong font-size, role, Artifact, tag, MCID, or column boundary;
- no list-marker or heading boundary that requires a new block.

A larger gap, first-line indentation after a completed line, hanging list marker,
role transition, or column change starts a new paragraph. Text joins with one
newline between source lines. IDs are deterministic from page index and inferred
block ordinal. Provenance covers the exact source-ordinal range, MCID union, page
object, origins, and the Stage 3 rule ID.

### 5. Repeated page furniture

Evaluate top and bottom margin candidates only after paragraph construction.
Candidate bands default to the outer 12% of LayoutSpace page height. Build a
privacy-internal fingerprint using Unicode case folding, whitespace collapse,
and digit-run replacement; do not store the fingerprint in public Layout IR.

A Header or Footer requires the same normalized fingerprint and compatible
relative vertical band on at least two pages and at least half of eligible pages.
A PageNumber requires a strict Arabic/Roman/page-label pattern plus a top/bottom
margin position; repeated digit-normalized context raises confidence. First-page
special headers do not become repeated furniture without evidence.

Furniture text remains in nodes, source order, and inferred order. Only confirmed
furniture is excluded from `main_flow`. Ambiguous candidates remain
`Unclassified`, stay in main flow, and may emit `page_furniture_ambiguous`.
Explicit Stage 2 Artifact remains Artifact and is excluded from main flow without
requiring repetition.

## Roles, capabilities, confidence, and rules

Stage 3 adds Header, Footer, and PageNumber roles. Existing semantic roles remain
when stronger author structure exists, except Artifact always wins. Geometry may
classify an otherwise unclassified or generic paragraph node as furniture; it
must not replace Table, Figure, Formula, Form, or author Heading/List roles.

`inferred_order` capability is true when at least one page has an inferred node.
`main_flow` capability is true when inference ran successfully, including the
valid case where all content is furniture and main flow is empty. Each node keeps
a confidence in [0,1] and a stable rule ID. Rule IDs identify line, paragraph,
column, fallback, repeated-header/footer, and page-number decisions.

## Bounds and complexity

Use existing `max_text_spans`, `max_pages`, and `max_object_depth` as hard bounds
for flattened spans, derived lines/blocks, recursion, and document-wide
fingerprint work. Derived collection growth uses checked arithmetic. XY-cut must
be bounded by both item count and recursion depth. The intended complexity is
O(n log n) per page plus O(n) document furniture aggregation; no glyph-by-page,
association-by-node, or page-pair quadratic scan is allowed.

Resource-limit breaches are fatal `limit_exceeded`. Geometry ambiguity is a
recoverable warning and must preserve source text and deterministic order.

## Stable warnings

- `reading_order_ambiguous`: weak/overlapping columns, rotated/vertical fallback,
  or a paragraph boundary that cannot be decided at required confidence;
- `page_furniture_ambiguous`: a margin candidate lacks repetition, position, or
  label evidence for safe exclusion from main flow;
- existing Stage 1/2 warnings remain unchanged and aggregated.

Warnings aggregate by page and condition class; consumers branch on code, never
human-readable message.

## Validation metrics

### Redistributable gold

Generated fixtures provide exact node text, paragraph boundaries, source order,
inferred order, main flow, and furniture roles for single column, two column,
three column, mixed font, list, CJK, rotated fallback, and repeated-page cases.
Required results are 1.0 pairwise order accuracy, 1.0 paragraph-boundary F1, and
1.0 furniture precision/recall on the supported synthetic contract.

### Frozen-corpus proxy

The private report stores only counts, hashes, warning-code counts, durations,
and aggregate scores. It never stores text, fingerprints, nodes, Alt, ActualText,
or tags. On pages with usable tagged order, compare common node IDs and report
pairwise inferred-vs-tagged order accuracy without treating author tags as
perfect human gold. Report paragraph counts, multi-column pages, furniture roles,
main-flow coverage, ambiguity counts, deterministic hashes, throughput, and
serialized size.

The cutover target remains reading-order score >= 0.95 with no regression. A
proxy below target does not permit changing the default provider even if Stage 3
implementation is otherwise complete.

## Compatibility and front ends

Layout IR schema remains version 1 with additive enum variants and populated
`inferred_order`/`main_flow`. Empty Stage 3-only optional data is omitted where
possible. Legacy text DTOs and Stage 2 tagged semantics remain unchanged. Rust,
CLI, Python, and WASM expose the same shared result without binding-side layout
logic.

## Test strategy

- exact line, XY-cut, paragraph, list, CJK, rotated, and tie-break fixtures;
- two/three-column source-order permutations with fixed inferred order;
- three or more pages with repeated header/footer/page-number variants;
- single-page and non-repeated margin text false-positive regressions;
- Artifact and author-role precedence; tagged/inferred/main-flow independence;
- exact collection/depth boundaries, malformed geometry, and warning aggregation;
- deterministic JSON and CLI/Python/WASM parity;
- frozen-corpus privacy, performance, and tagged-order proxy report.

## Definition of Done

- [x] The coordinate/order/role precedence contract is executable and tested.
- [x] Lines and paragraphs are deterministic with exact provenance and stable rules.
- [x] Multi-column inferred order passes supported synthetic gold at 1.0.
- [x] Header, footer, and page-number text is preserved and classified.
- [x] Main flow excludes only confirmed furniture and Artifact nodes.
- [x] Ambiguous/rotated/vertical cases preserve text and warn without unsafe guesses.
- [x] Work is bounded and avoids quadratic page/node scans.
- [x] Tagged, inferred, source, and main-flow orders remain independent.
- [x] Rust, CLI, Python, and WASM expose the same additive schema.
- [x] Frozen corpus report is deterministic, privacy-safe, and records proxy quality.
- [x] Focused tests, formatting, denied-warning Clippy, and workspace tests pass.

The frozen-corpus tagged-order proxy is 0.940546, below the 0.95 cutover target.
This does not invalidate the completed Stage 3 implementation contract, but it
independently forbids changing the default provider. Default-provider cutover remains forbidden.
Table, Documa shadow
integration, final quality, rollout, and rollback gates also remain open.