# Stage 12 Stage 6A/6B Definition of Done Evidence

Status: PASS for 6A and 6B (2026-07-29); Stage 6C core streaming remains active

## Delivered integration contract

- Documa keeps `pymupdf` as the default PDF provider and selects `rust` only
  through the explicit registry option.
- `RustPdfAdapter` lazily imports the optional exact Rust wheel and maps schema
  version 1 `layout_unrotated_top_left` pages into Documa IR.
- Text roles, spans, all four orders, tables/cells/spans, image placements,
  captions, Alt/Artifact evidence, links, destinations, outlines, warnings,
  capabilities, and parser provenance remain traceable.
- Rust inferred order is locked at the Documa reading-order stage; Documa does
  not silently recompute geometric order for Rust pages.
- Stable recoverable errors cover missing bindings, parse failures, incompatible
  schemas/coordinate spaces, invalid provider names, and truncated page streams.
- The replacement boundary is parser extraction only. PyMuPDF page rasterization
  remains required for OCR and previews because Rust has no renderer.

## Formal private-corpus shadow benchmark

`tools/stage12_documa_shadow.py` ran each complete Documa adapter in an independent
process with one warm-up and three measured runs over 7 PDFs / 1,113 pages. The
report contains no extracted text, URLs, images, comparison counters, or private
IR. Both providers were deterministic for all documents.

- Rust Documa adapter: 55.385195 sum-of-medians seconds, 20.095623 pages/s;
- PyMuPDF Documa adapter: 168.429137 seconds, 6.608120 pages/s;
- Rust speedup versus complete PyMuPDF Documa: 3.041050x;
- normalized non-whitespace character F1: 0.960813 (cutover target 0.995);
- character-bigram reading-order proxy: 0.951281;
- Rust / PyMuPDF maximum sampled RSS: 1,711,919,104 / 610,291,712 bytes
  (2.805083x);
- Rust / PyMuPDF canonical Documa IR bytes: 242,352,585 / 69,306,577
  (3.496819x);
- invalid Rust bboxes: 0; page counts: exact 1,113 / 1,113;
- report SHA-256:
  `21142e2c2f6db995336c8ba8f38f996565dff5bb5fce2d8343092edbe658432d`.

Table and image counts are diagnostic, not accuracy scores: Rust reports
conservative reconstructed tables and painted image occurrences, while the
PyMuPDF adapter reports finder candidates and image blocks. Private gold labels
are still required before a table/image cutover decision.

## Stage 6C transfer-layer evidence

The backward-compatible Python `extract_layout_stream()` API serializes metadata
once and drains one page JSON object at a time. Existing `extract_layout()` is
unchanged. Documa prefers `draining_json_v1` and automatically falls back to the
old whole-document API for an older wheel.

- exact wheel Python tests: 11/11 passed;
- Documa stream/adapter/reading-order focused tests: 17/17 passed;
- public two-column integration: 2 pages / 9 blocks, `draining_json_v1`;
- draining transfer alone reduced the two stress cases by 18.3381% and 17.0152%;
- default decorative aggregation then reduced the 580-page case to 658,415,616
  bytes (-61.5393%) and the 423-page case to 884,400,128 bytes (-18.4775%);
- the 580-page Documa IR materializes 315 content images and aggregates 75,249
  decorative occurrences; explicit `rust_pdf_include_decorative_images=True` restores all;
- both stress cases preserved the Stage 6B text SHA, block count, and span count;
- streaming wheel: 1,058,770 bytes, SHA-256
  `b5058b6d243ba4bef955fce3978972ab6240bf7bb752858a86785cfa90c91de7`.

This removes the simultaneous whole-document JSON string and decoded Python
Layout dictionary. It does not yet make the Rust core produce and release pages
incrementally: native `DocumentLayout` is still built for the whole document.
The streaming-plus-aggregation maximum remains 1.449143x the formal PyMuPDF maximum, so the memory
cutover gate remains open.

## Validation

```text
Documa focused pytest
17 passed

Documa full pytest with explicit snapshot plugins
353 passed

Ruff on all changed Documa files
All checks passed

cargo fmt --all --check
PASS

cargo clippy --workspace --all-targets --all-features -- -D warnings
PASS

cargo clippy -p pdf-core -p pdf-wasm --target wasm32-unknown-unknown \
  --all-targets --all-features -- -D warnings
PASS

cargo test --workspace --all-features
PASS, including doctests
```

## Gate decision

Stage 6A and Stage 6B are complete. The Stage 6C transfer layer is complete, but
native page-production and the overall memory gate are not. Stage 6D default
provider cutover is forbidden because character F1 is below 0.995, the earlier
tagged-order proxy remains below 0.95, private table/image gold is absent, and
streaming RSS remains above the allowed production bound.