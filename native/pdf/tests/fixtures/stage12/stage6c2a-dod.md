# Stage 12 Stage 6C2-A Definition of Done

Status: Complete; Stage 6C2-B may begin

## Scope completed

- Added bounded `DocumentStart`, `Page`, and `DocumentFinalize` DTOs plus an
  owned `LayoutEventStream`.
- Added `collect_layout_events` with stable machine-readable failures for
  malformed order/count/coordinate/patch input and exact event/update limits.
- Routed the complete `PdfDocument::extract_layout()` API through the collector.
- Preserved the frozen `layout_unrotated_top_left` coordinate contract.
- Kept the current producer explicitly classified as complete-document
  compatibility production, not native page streaming.

## Exactness and performance evidence

- Focused event/collector tests: 5/5 passed.
- Frozen private corpus: 7/7 PDFs and 1,113/1,113 pages parsed.
- Canonical Layout IR SHA-256 and all three serialized byte counts match the
  pre-refactor Stage 5 outputs for every document.
- Privacy-safe parity report:
  `target/stage12-stage6c2a-parity/report.json`
- Report SHA-256:
  `e39eb613c419aa671cfc5f0c61f7b0d6f842131ff566f4da97ecb4b918398dd5`
- Refactored collector throughput: 201.748765 pages/s.
- Maximum sampled core RSS: 674,770,944 bytes.
- Stage 5 comparison: 195.707651 pages/s and 675,053,568 bytes; no
  compatibility-seam throughput or memory regression was observed.

The report contains only timings, hashes, counts, byte sizes, RSS, and audit
booleans. It contains no extracted text, URLs, image bytes, semantic arrays, or
private Layout IR.

## Cross-interface and integration gates

- Exact CPython 3.10 wheel: 1,072,142 bytes, SHA-256
  `561d797fa9d4e2d93c137d6913d3d4e4527a27bc29df8d0a573441d49fca73db`.
- Exact wheel binding tests: 11/11 passed.
- Node WASM: 8 stage tests plus 2 web tests passed.
- Documa Rust adapter/reading-order focused tests: 17/17 passed.
- Documa full suite with explicit snapshot plugins: 353/353 passed.
- Native and wasm32 Clippy with warnings denied, formatting, and full workspace
  tests/doctests passed.

## Gate decision

Stage 6C2-A is complete and Stage 6C2-B may begin. This decision does not close
Stage 6C2: the producer still constructs the complete `DocumentLayout` before
events are drained. The complete-adapter RSS gate, quality gates, private
table/image gold gates, and native Python release semantics are not satisfied.
Default-provider cutover remains forbidden.
