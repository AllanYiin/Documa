# Stage 12 Stage 4 Table Reconstruction Contract

Specification version: 1
Status: Complete (Stages 4A-4D)

## Goal

Produce deterministic, bounded table topology from author tags, vector rules,
and conservative text alignment. Preserve every source text node, link each
accepted cell to its source nodes, and expose rows, columns, row spans, column
spans, header semantics, confidence, provenance, and stable rule IDs through the
shared Layout IR.

## Ownership boundary

`pdf-core` owns all PDF-aware structure traversal, graphics-state and path
interpretation, coordinate projection, table candidate generation, topology,
cell assignment, fusion, confidence, limits, and warnings. Bindings serialize
the shared result only. Documa maps accepted tables into Documa IR and continues
to own section semantics, chunking, search, and LLM reasoning.

Stage 4 does not add an LLM, embedding model, PDF renderer, or third-party PDF
parser to Rust. Semantic interpretation of cell contents remains outside the
parser.

## Non-goals

- No OCR, raster-line detection, or neural table model.
- No cross-page table merge or repeated-header merge.
- No spreadsheet formula, type, unit, or domain inference.
- No destructive removal or rewriting of source semantic nodes.
- No default-provider change in Documa.
- No forced topology when evidence is ambiguous or internally inconsistent.

## Canonical coordinate contract

All public and heuristic table geometry uses `LayoutSpace`:

```text
coordinate_space = "layout_unrotated_top_left"
origin = normalized CropBox top-left
x_direction = right
y_direction = down
unit = pt after UserUnit
page_rotate_applied = false
bbox = [x0, y0, x1, y1], where x0 <= x1 and y0 <= y1
```

PDF path operands begin in native `PdfUserSpace`. The active CTM, including
nested Form matrices and `q`/`Q` restoration, is applied first. The resulting
page-space points are projected exactly once through `PageGeometry.pdf_to_layout`.
No detector may consume native PDF coordinates, infer a Y direction, apply page
rotation, or perform a second axis flip.

Directed path segments retain their transformed endpoints internally. Candidate
table edges are normalized after projection:

- horizontal edge: endpoints ordered by increasing x;
- vertical edge: endpoints ordered by increasing y;
- BBoxes are min/max envelopes and therefore never encode direction;
- skewed, curved, clipped, or degenerate paths do not become lattice edges
  unless a later contract explicitly supports them.

LayoutSpace is the stable serialized and inference space. DisplaySpace is a
derived preview/overlay space only and must not alter table topology.

## Evidence precedence and substages

### Stage 4A: tagged topology

Preserve the bounded structure hierarchy required for
`Table -> TR -> TH | TD`, including RoleMap resolution, page association,
structure object IDs, ordered MCIDs, and table attributes. Read `RowSpan` and
`ColSpan` from valid structure attributes; default each to one. Header cells
retain `TH` and supported `Scope` semantics.

Accept a tagged table only when rows and cells form one deterministic rectangular
grid after spans are placed. Missing page content may leave an empty cell but
must warn. Overlap, a zero span, mixed-page topology, or impossible placement
does not silently produce a table.

### Stage 4B: vector lattice

Collect bounded path operations with the same graphics-state and Form recursion
rules as text extraction. Support straight segments from `m`, `l`, `re`, and
close-path, painted by stroke-capable path operators. Normalize nearly
horizontal/vertical edges, join collinear fragments within a point tolerance,
find intersections, and derive closed rectangular cells.

Fill-only paths, decorative short rules, isolated boxes, and dense charts are
not tables without compatible text and grid topology. Dashed or fragmented
rules may join only when the maximum gap and alignment tolerances are satisfied.

### Stage 4C: borderless text alignment

Generate candidates from repeated line bands and stable x alignments. Require at
least two rows and two columns, consistent column occupancy, bounded gaps, and a
score above the acceptance threshold. Prose paragraphs, lists, multi-column page
layout, forms, and key-value pairs are explicit false-positive regressions.

This detector is conservative: an uncertain candidate remains ordinary text and
emits at most one aggregated `table_detection_ambiguous` warning per page.

### Stage 4D: fusion and benchmark

Evidence precedence is:

1. valid author-tagged topology;
2. compatible vector lattice geometry;
3. conservative text alignment.

Lower-priority evidence may refine a missing BBox or support confidence but must
not overwrite valid author spans or header roles. Material conflicts preserve
the higher-priority result and emit `table_evidence_conflict`. Overlapping
accepted candidates are resolved deterministically by precedence, confidence,
covered source-node count, area, and stable ID.

## Layout IR contract

Each `LayoutTable` contains:

- stable table ID, LayoutSpace BBox, row and column counts;
- row-major cells, confidence, rule ID, provenance;
- ordered `source_node_ids` linking the table to preserved semantic nodes;
- an evidence kind: tagged, vector lattice, text alignment, or fused.

Each `LayoutTableCell` contains:

- zero-based row and column plus positive row/column spans;
- role: data, row header, column header, or both header;
- text assembled from linked nodes in the selected cell-local reading order;
- optional LayoutSpace BBox and provenance (absent only for a logically empty tagged
  cell with no physical content), confidence, and rule ID;
- ordered `source_node_ids`.

Tables coexist with semantic nodes. Stage 4 does not insert table IDs into the
four node-order arrays and does not delete table-contained nodes from them.
Consumers place a table at the earliest linked source node; the Stage 6 Documa
adapter must implement and test this mapping explicitly.

`capabilities.tables` is true when table reconstruction ran successfully,
including a document with zero accepted tables. An empty table list therefore
means "no supported table detected", not "feature unavailable".

## Bounds and complexity

Add explicit max_path_segments, max_table_candidates, max_tables, and
max_table_cells limits for collected paths, candidates, accepted tables, and cells. Existing structure, content-operation, text-span, page,
object-depth, stream, and document decode limits remain authoritative.

All derived growth uses checked arithmetic. Candidate construction uses sorted
coordinates, interval indexes, and page-local grids; it must not perform an
unbounded all-segment-by-all-segment or all-cell-by-all-node scan. A limit breach
is fatal `limit_exceeded`. Ambiguity is recoverable and preserves source text.

## Stable warnings

- `tagged_table_invalid`: malformed or impossible tagged table topology;
- `table_detection_ambiguous`: insufficient geometric/text evidence;
- `table_evidence_conflict`: tagged, vector, or text evidence materially differs;
- `table_cell_unassigned`: accepted topology has content that cannot be assigned;
- `table_cell_overlap`: a span placement or accepted cell geometry overlaps;
- existing tagged, geometry, text, and limit diagnostics remain unchanged.

Warnings aggregate by page and condition class. Consumers branch on code, never
on the human-readable message.

## Validation metrics

Redistributable fixtures require exact topology and text for:

- tagged TH/TD tables, RoleMap aliases, RowSpan, ColSpan, and empty cells;
- ruled tables with `m/l`, `re`, nested `q/Q/cm`, Form matrices, fragmented and
  dashed rules;
- borderless tables, CJK content, multiline cells, and mixed font sizes;
- rotated-page metadata with unrotated LayoutSpace geometry;
- prose, lists, page columns, forms, charts, and decorative boxes as negatives;
- malformed, cyclic, exact-limit, and one-over-limit inputs.

Supported synthetic fixtures target exact table count, dimensions, cell text,
spans, roles, source-node links, and deterministic JSON. Gold table structure
target is TEDS-S >= 0.90 with no regression. Also report table precision/recall,
cell-text F1, parse+layout+table median, peak memory, serialized size, warnings,
and deterministic hashes.

The private 7-document report contains only corpus IDs, hashes, counts, aggregate
scores, durations, warning-code counts, and resource measurements. It contains
no extracted text, nodes, cells, tags, or table contents.

The completed Stage 4 report covers 7 documents / 1,113 pages at 205.731635
pages/s, 28.309930x the frozen complete Documa adapter. All measured outputs are
deterministic and schema/privacy safe. It records 5 accepted tables / 76 cells,
674,615,296 bytes maximum sampled RSS, and 331,010,437 serialized bytes. Private
TEDS-S remains explicitly null because the frozen corpus has no table gold labels;
synthetic exact fixtures score 1.0. See `tests/fixtures/stage12/stage4-dod.md`.

## Definition of Done

- [x] Coordinate direction, transform order, edge normalization, and ownership
  are specified before implementation.
- [x] Tagged Table/TR/TH/TD hierarchy and span/header attributes are preserved.
- [x] Tagged tables pass exact topology, text, role, provenance, and limit tests.
- [x] Vector paths and Form/CTM geometry are collected into LayoutSpace once.
- [x] Ruled-table reconstruction passes exact positive and negative fixtures.
- [x] Borderless reconstruction is conservative and passes false-positive tests.
- [x] Evidence fusion is deterministic and conflict warnings preserve stronger data.
- [x] Source nodes and all four node orders remain complete and independent.
- [x] Rust, CLI, Python, and WASM expose identical additive table data.
- [x] Frozen-corpus report is deterministic, privacy-safe, and records quality,
  speed, memory, and size.
- [x] Focused tests, formatting, denied-warning Clippy, and workspace tests pass.
- [x] Default-provider cutover remains closed unless every global Go/No-Go gate passes.