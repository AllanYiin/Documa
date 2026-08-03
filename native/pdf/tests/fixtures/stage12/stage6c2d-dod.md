# Stage 12 Stage 6C2-D Definition of Done

Status: Complete; Stage 6C2-E may begin

## Scope completed

- `FurnitureCollector` retains only page/node IDs, normalized fingerprints,
  top/bottom bands, original/final role metadata, inferred-order IDs, artifact
  state, confidence, and page-number evidence. It copies no span text or geometry
  arrays.
- Repeated header/footer and page-number decisions are finalized after the final
  page into stable-ID `LayoutNodeFinalization` updates and per-page `main_flow`.
- The native event API now emits provisional furniture roles/main-flow and a
  nonempty `page_finalizations` payload. The validating collector restores the
  exact complete `DocumentLayout`.
- Text-table and figure/caption rules run page-locally before furniture finalize;
  only node role/confidence/rule and `main_flow` are delayed.

## Exactness, limits, and private evidence

- Event tests pass 6/6, including a direct three-page repeated-furniture case
  whose page events are provisional and whose final patches restore exact output.
- Existing reading-order tests pass 13/13, including repeated furniture, unique
  margins, author-role precedence, Arabic/Roman page numbers, and exact limits.
- Frozen 7-document/1,113-page Layout IR remains byte-identical to Stage 6C2-B.
  The final combined report and SHA-256 are recorded in `stage6c2c-dod.md`.
- Final exact CPython 3.10 wheel: 1,108,719 bytes, SHA-256
  `8bfde5151edae46e828aaa27d125073b1e4d94915241da5b7fc874586a6036e1`;
  wheel tests pass 11/11.
- Node WASM tests pass 8/8 plus 2/2 web tests.
- Documa doctor passes 8/8 with 18/18 fixture readiness, Rust adapter/reading-
  order focused tests pass 17/17, the full suite passes 353/353, and full Ruff
  passes. PyMuPDF remains the default provider and renderer.
- Formatting, native workspace Clippy, wasm32 Clippy, full workspace tests with
  both configured private contracts, and doctests pass with warnings denied.

## Gate decision

Stage 6C2-D is complete and Stage 6C2-E may begin. The event finalization protocol
is real, but `extract_layout_events()` still builds the complete native document
before yielding its first event. Stage 6C2-E must replace that compatibility
producer and the Python draining queue with a genuinely lazy native producer.
Default-provider cutover remains forbidden; F1, complete-adapter RSS, and private
table/image gold gates are unchanged.
