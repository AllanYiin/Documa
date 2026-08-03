# Stage 12 Stage 6C2 Native Page-Production Contract

Specification version: 1
Status: Stage 6C2-E and Stage 6D memory work complete; default cutover NO-GO on quality gates

## Problem

The Python draining transfer removes the whole-document JSON string and decoded
Python Layout dictionary, and aggregate-only decorative mapping removes semantic
IR amplification. At contract freeze, peak RSS still reached 884,400,128 bytes, 1.449143x the formal
PyMuPDF Documa maximum. `PdfDocument::extract_layout` still constructed all page
text details, glyphs, semantic nodes, tables, image placements, and orders before
the first page can drain.

A superficial iterator around `DocumentLayout.pages` is not native streaming.
Re-parsing every page in a second pass is also unacceptable: it can charge the
monotonic document DecodeBudget twice, repeat expensive work, and turn a valid
bounded document into a limit failure.

## Goals

1. Produce and release full page payloads incrementally from `pdf-core`.
2. Preserve byte-equivalent final Layout IR for identical version/options/input,
   excluding explicitly opted-in timings.
3. Keep every PDF-aware rule in `pdf-core`; bindings only transfer events/errors.
4. Preserve stable node/source IDs so final document patches are deterministic.
5. Reduce complete Rust-to-Documa peak RSS to at most 1.2x the frozen PyMuPDF
   complete-adapter maximum (at most 732,350,054 bytes on the frozen machine).
6. Keep parse success, page count, text SHA, roles, orders, tables, images,
   navigation, warnings, and stable errors equivalent to the collected API.

## Non-goals

- Do not move domain semantics, cross-page table merge, sectioning, chunking,
  retrieval, or LLM reasoning into Rust.
- Do not add rendering or remove PyMuPDF from OCR/page-preview rasterization.
- Do not change the coordinate convention, parser limits, public error codes, or
  existing `extract_layout()` result shape.
- Do not use filesystem spooling, hidden worker threads, unsafe code, or another
  PDF-aware dependency.

## Frozen coordinate contract

Every public page/span/node/table/image/link geometry remains
`layout_unrotated_top_left`:

- origin: normalized CropBox top-left;
- x axis: increases right;
- y axis: increases down;
- units: points after applying UserUnit;
- page Rotate: not applied to coordinates and retained only as page metadata;
- PDF CTM and Form Matrix: applied before exactly one PdfUserSpace-to-LayoutSpace
  projection;
- DisplaySpace is presentation-only and must never be mixed into Layout IR.

No event or patch may contain an untagged coordinate tuple. Finalization patches
identify stable IDs and semantic/order changes; they do not introduce a second
coordinate space.

## Semantic ownership

Rust owns deterministic PDF-derived semantics: font/Unicode decoding, marked and
tagged structure, local reading order, repeated furniture evidence, tables,
Figure/Caption/Artifact evidence, image paint occurrences, and navigation.
Documa owns domain/cross-page semantics and LLM reasoning. Decorative occurrences
remain complete in Rust Layout IR; Documa may retain aggregate counts by default
and materialize all occurrences only through explicit opt-in.

## Event model

The new internal source of truth is a bounded `LayoutEvent` producer:

1. `DocumentStart`
   - schema/parser/options/coordinate space/page count;
   - capabilities and global navigation indexes available before page delivery;
   - no full document text and no page array.
2. `Page`
   - one page geometry, text, semantic nodes, tables, image placements, links,
     source/tagged/local-inferred orders, and provisional main-flow state;
   - the producer relinquishes the full page payload after the consumer accepts it.
3. `DocumentFinalize`
   - compact stable-ID patches for repeated Header/Footer/PageNumber/Artifact roles
     and final main-flow membership;
   - document warnings, quality/timing summaries, outlines/destinations if not
     available at start, and completion counts/digests.

`extract_layout()` must collect these same events and apply finalization patches to
produce the existing `DocumentLayout`. Python `extract_layout_stream()` consumes
the events directly; Documa builds blocks per page and applies terminal patches by
Rust source ID before downstream pipeline stages run.

## Required internal refactors

### 6C2-A: event protocol and compatibility collector

- Add bounded `DocumentStart`, `Page`, and `DocumentFinalize` event DTOs.
- Add a compatibility collector that validates ordering, coordinate-space
  consistency, counts, stable-ID patches, and exact event/patch limits.
- Reimplement complete `extract_layout()` through that collector without changing
  serialized output. This substage deliberately still wraps a complete
  `DocumentLayout`; it is an API/refactor seam, not native page production.

### 6C2-B: page-scoped extraction primitives

- Split text/content traversal into a page-scoped producer with shared document
  resolver, fonts, limits, monotonic DecodeBudget, and bounded object-stream cache.
- Do not retain document-wide positioned glyph or decoded operation vectors.
- Aggregate document quality counters and text digest incrementally.

### 6C2-C: global PDF structure indexes

- Traverse StructTreeRoot/RoleMap/ParentTree and navigation once into bounded,
  page-indexed compact associations before or during page production.
- Apply tagged roles, MCIDs, table topology, Figure/Alt/Artifact evidence, links,
  and caption rules as each page is materialized.
- Preserve all existing depth/count/decoded-byte limits and recovery warnings.

### 6C2-D: delayed repeated-furniture finalization

- During each page, retain only normalized fingerprints, candidate bands, page
  number, node ID, and required confidence/provenance fields.
- After the final page, deterministically emit role/main-flow patches.
- Patch storage is bounded by existing semantic-node limits and contains no copied
  span text or geometry arrays.

### 6C2-E: native bindings and complete front-end collectors

- Expose a Python native event/page iterator whose accepted page is immediately
  dropped; no `VecDeque<PageLayout>` prebuilt from a complete document.
- Preserve the public page iterator while changing its strategy to `native_events_v2`;
  drain terminal stable-ID patches individually after page production.
- CLI/WASM output remains unchanged by collecting the event stream.

## Work order and stage gates

1. Add event DTOs and a synthetic collector-parity harness; no behavior change.
2. Page-scope text/glyph/content extraction and prove exact legacy/layout/auto
   parity plus unchanged DecodeBudget boundaries.
3. Add page-indexed tagged/navigation inputs and page-local tables/images/figures.
4. Add furniture fingerprints/finalization patches and four-order exact parity.
5. Switch Python stream, then CLI/WASM complete collectors, to the event producer.
6. Run focused, native/wasm Clippy, full workspace, exact wheel, Documa full suite,
   fuzz/limit, and private shadow gates before marking 6C2 complete.

## Acceptance criteria

- [x] Complete collector JSON is byte-identical to the pre-refactor result for all
  synthetic fixtures and the frozen corpus, except explicitly enabled timings.
- [x] Python native stream never owns a complete `Vec<PageLayout>` or full Layout
  JSON string; accepted pages are released before producing the next page.
- [x] No decoded page-operation or positioned-glyph vector survives page delivery.
- [x] Document DecodeBudget, object cache, recursion, page/node/span/table/image,
  navigation, and event/patch counts have exact-boundary and one-over tests.
- [x] Truncated stream, consumer exception/cancellation, and malformed optional
  structure recover with stable errors and no leaked worker/process/file state.
- [x] 7/7 private documents parse; 1,113/1,113 page counts and Rust text SHA match.
- [x] Character/bigram scores do not regress from 0.960813/0.951281 while the
  separate quality program works toward the 0.995 character gate.
- [ ] Peak complete-adapter RSS is at most 732,350,054 bytes (1.2x frozen PyMuPDF).
- [ ] Median complete-adapter throughput does not regress from 20.095623 pages/s.
- [x] `cargo fmt --all --check`, native and wasm Clippy `-D warnings`, full workspace
  tests/doctests, exact wheel tests, Documa focused/full tests, and Ruff all pass.
- [x] Default provider remains PyMuPDF and rollback remains one option change.

## Risks and explicit decisions

- Repeated furniture is a finalize patch, not a reason to retain every page.
- Tagged structure is pre-indexed compactly; semantic inference is not duplicated
  in Python.
- A two-pass design may scan compact metadata twice only if decoded bytes and work
  are charged exactly once and output parity/limits are proved. Re-decoding page
  content under a reset budget is forbidden.
- Timing cannot waive F1, gold-label, silent-loss, determinism, or memory failures.
## Stage 6C2-A completion evidence

- `LayoutEventStream` moves owned pages without cloning page payloads, while
  `collect_layout_events` enforces event order, page identity, coordinate-space,
  patch identity, finite confidence, count, and limit invariants.
- Focused event tests pass 5/5, including exact-boundary/one-short limits,
  malformed streams, coordinate mixing, and delayed stable-ID patches.
- The 7-document/1,113-page frozen corpus is byte-identical to the Stage 5
  canonical Layout IR: all seven canonical SHA-256 values and all three
  serialized byte counts per document match. The privacy-safe report SHA-256 is
  `e39eb613c419aa671cfc5f0c61f7b0d6f842131ff566f4da97ecb4b918398dd5`.
- The refactored complete collector measured 201.748765 pages/s and
  674,770,944-byte peak RSS, so this compatibility seam introduced no regression
  against the Stage 5 core baseline. It does not satisfy the complete-adapter
  1.2x RSS gate.
- Exact CPython wheel tests pass 11/11, Node WASM tests pass 8+2, and Documa
  focused/full tests pass 17/17 and 353/353.
- Stage 6C2-A is complete. Stage 6C2-B must replace the current complete-document
  producer; Stage 6C2 overall and default-provider cutover remain incomplete.
## Stage 6C2-B completion evidence

- `TextPageProducer` shares one document runtime and delivers page-local text,
  glyphs, marked-content metadata, warnings, quality, and vector results. Exact
  document glyph limits and global source ordinals are enforced incrementally.
- `build_layout_document` immediately builds each `PageLayout`; parsed operations
  and positioned glyph vectors no longer survive that page's conversion. Public
  complete text APIs continue collecting their required result shape.
- Two direct producer tests, the complete pdf-core suite, configured private
  ContentOrder/Layout/Auto contracts, and 7-document/1,113-page canonical Layout
  parity pass. The privacy-safe core report SHA-256 is
  `8da9a29de5cea7fa07997133076537f4669ae157ef3ef76881904ff0e9a7d4f2`.
- Core peak RSS fell from 674,770,944 to 444,100,608 bytes (-34.1850%) while
  throughput rose from 201.748765 to 212.467582 pages/s.
- Exact wheel tests pass 11/11 and Documa focused/full tests pass 17/17 and
  353/353. The interim complete-adapter run reached 27.818060 pages/s but still
  used 884,908,032 bytes (1.454378x PyMuPDF), so the overall memory gate is open.
- Stage 6C2-B is complete. Stage 6C2-C must page-index tagged/navigation inputs
  and apply page-local tables/images/figures without retaining the complete
  document before native event delivery.
## Stage 6C2-C completion evidence

- Tagged structure and navigation are pre-indexed once into bounded per-page
  buckets. Reading order, tagged associations/tables, vector/text tables, images,
  figures/captions, and links now consume one materialized page at a time.
- Stage-specific warning payload order and de-duplication remain exact through
  compact key states. The legacy document-wide vector recovery path has a bounded
  rollback so optional malformed content cannot silently change stable output.
- The final 7-document/1,113-page combined C/D run is byte-identical to Stage
  6C2-B, deterministic, schema-safe, and privacy-safe. It measured 182.426213
  pages/s and 434,147,328-byte core RSS; report SHA-256 is
  `149f92aaf43a4806a36b76e373fc6dcb9070c08c1f43f289195bcdcbc9f9bcfb`.
- Stage 6C2-C is complete. Page-local semantic rules are no longer a reason to
  retain complete glyph/content vectors; lazy event delivery remains Stage 6C2-E.

## Stage 6C2-D completion evidence

- `FurnitureCollector` keeps compact page/node IDs, normalized fingerprints,
  bands, order IDs, and required original/final classification fields only.
- `extract_layout_events()` now exposes provisional page furniture and emits real
  stable-ID node/main-flow finalizations. A direct three-page test and the frozen
  corpus prove that `collect_layout_events()` restores the exact complete result.
- Event tests pass 6/6; reading-order tests pass 13/13. The exact final wheel is
  1,108,719 bytes with SHA-256
  `8bfde5151edae46e828aaa27d125073b1e4d94915241da5b7fc874586a6036e1`;
  wheel tests pass 11/11 and Node WASM passes 8+2.
- Documa doctor passes 8/8 with 18/18 fixture readiness, focused tests pass 17/17,
  full tests pass 353/353, and Ruff passes. PyMuPDF remains default and retains
  OCR/page-preview rendering.
- Stage 6C2-D is complete. Stage 6C2-E must replace the complete-document event
  source with a genuinely lazy producer and make Python consume it without a
  prebuilt `VecDeque<PageLayout>`.

## Stage 6C2-E completion evidence

- `LayoutEventProducer` is the fallible native source. Compact document indexes are
  prepared once; page text/content/semantics are produced only when `next()` asks
  for that page. A later-page malformed-content test proves first-page delivery is
  not a compatibility illusion, and cancellation is an ownership drop with no
  external state.
- Python `native_events_v2` owns this producer directly. Document metadata updates
  in place at exhaustion, and `draining_stable_id_patches_v1` releases one terminal
  page finalization at a time. Documa applies patches before returning `DocumentIR`.
- The frozen 7-document/1,113-page core output is exact to Stage 6C2-C/D. The
  privacy-safe report SHA-256 is
  `9c3d666e4561e2dc7bf8793b6ffed21a06663a7c9c53663215372adcbb16f776`;
  it measured 161.881041 pages/s and 441,270,272-byte core RSS in one run.
- Exact wheel tests pass 11/11; final wheel SHA-256 is
  `5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61`.
  Documa focused/full tests pass 17/17 and 353/353, and Ruff passes.
- The first full shadow remained a cutover NO-GO: Rust was 3.682255x faster but
  used 946,515,968 bytes, 1.553468x PyMuPDF. Per-page finalization draining lowered
  the isolated AI Index probe to 900,263,936 bytes, still above 1.2x and handing
  mapped metadata/provenance compaction to Stage 6D.
- Lazy vector recovery is fail-forward because an accepted page cannot be
  retracted. Limit errors terminate; non-limit later vector failures preserve prior
  accepted pages, disable later optional vectors, and finalize a stable warning.
## Stage 6D completion evidence

- Documa `compact_trace_v1` retains source ordinals, MCIDs, text origins, and rule
  IDs under one shared schema; page object and coordinate space are inherited.
  Stable refs, citation BBoxes, roles, tables, images, navigation, warnings, and
  four orders remain available. Verbose legacy metadata is an explicit opt-in.
- The final one-warm-up / three-run shadow is deterministic for both providers on
  all 7 documents / 1,113 pages. Rust reaches 34.704637 pages/s versus PyMuPDF
  5.976338 pages/s, a 5.807007x speedup.
- Maximum complete-adapter RSS is 646,643,712 bytes for Rust versus 612,139,008
  for PyMuPDF, or 1.056367x. The 1.2x memory criterion is now complete.
- Character F1 remains 0.960813, tagged-order proxy remains below target, and
  private table/image labels remain absent. Default-provider cutover is still
  forbidden; memory success does not waive correctness gates.
