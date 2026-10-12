# P1/P2 implementation tracker

User direction: complete the non-human-gold items from `repository-review-2026-10-12.md` first.
Human reading-order/table/image acceptance stays last; Stage 7.4 and provider cutover remain closed.

Status: non-human-gold engineering items completed locally. Final evidence and artifacts are listed below.

## TODO

- [x] Bounded ASCIIHex, ASCII85, RunLength and LZW filter support and chain/limit tests.
- [x] Vertical CID W2/DW2 advances/origins and cross-interface regressions.
- [x] Conservative OCR graphics-effect/format eligibility and lazy page candidates.
- [x] Lazy OCR output, document budgets and multi-page memory evidence.
- [x] Additive page selection, configurable limits, CLI NDJSON and WASM event streams.
- [x] Reusable Python document handle and GIL release, with runtime tests.
- [x] Default/all-feature/MSRV/WASM CI and bounded Layout/image/OCR fuzz targets.
- [x] Pinned external ONNX model/runtime integration evidence and fixtures.
- [x] Clarify development capabilities, remove placeholder metadata, rebuild deliverables and update docs.
- [x] Final format, Clippy, Rust/Python/WASM and appropriate fuzz/integration checks.

## Constraints

- Preserve existing signatures and output defaults; new APIs are additive.
- PDF rules remain in `pdf-core`, which forbids unsafe code.
- No PDF-aware parser dependency, renderer, editing or default provider cutover.
- Preserve current upstream sync changes; baseline copies are under `target/priority-20261012-before`.
- The original implementation excluded external mutation; the later user request explicitly authorizes topic commits and submission to Documa main. Registry publication remains outside scope.

## Validation

- Filters: 7 new tests, Stage 2 4 tests and decode-budget 11 tests PASS; core all-target/all-feature Clippy PASS.
- Vertical metrics: W2/DW2/TJ/CTM tests 4/4, Stage 11 geometry 3/3, Stage 12 geometry 7/7 PASS; core Clippy PASS. Cross-interface checks pending.
- OCR: core candidate tests 5/5, native decoder 4/4 and lazy/cancel/budget tests 2/2 PASS; core/OCR Clippy PASS. RSS and real ONNX checks pending.
- Selection: new core tests 4/4, existing Layout events 8/8, Stage 12 contracts 33/33 PASS. Frontends pending.
- Format source: [Adobe ISO 32000-1:2008](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf), sections 7.4.2–7.4.5 and 9.7.4.3. The PDF was downloaded only for local specification reading, not added as a parser dependency.
- Python exact source-built ONNX-enabled CPython 3.10 wheel: initial 17/17 tests PASS, including real GIL release, concurrent calls and stream lifetime. Final wheel/ONNX stream rerun pending.
- Node WASM: 14/14 tests PASS, including selection, cancellation, late failure, vertical metrics and OCR candidate streams.
- CLI selected streams: 4/4 PASS; existing CLI all-feature tests PASS.
- Real ONNX: 7/7 CLI integration cases PASS with pinned Runtime 1.27.1, PP-OCRv4 weights, explicit height=48 preparation and dictionary space token. Public scan text exact, confidence 0.9757749. This is not human gold.
- MSRV: default workspace/all-target build PASS on 1.88.0; all-feature workspace/all-target build PASS on 1.89.0. Existing ONNX transitive nalgebra/wide/safe_arch dependencies reject 1.88, so docs/CI explicitly separate feature MSRVs.
- Synthetic 48-page CLI RSS (3 measured runs after warm-up): collected pretty JSON peak 273,731,584 bytes; streamed compact NDJSON peak 6,488,064 bytes; ratio 0.023702. Includes serializer differences; not a parser-only or private-corpus benchmark.

## Final validation

| Check | Current result |
|---|---|
| Workspace all-feature Rust tests | 264 passed including doctest; 1 existing manual benchmark ignored |
| Native all-target/all-feature Clippy | PASS with `-D warnings` |
| WASM all-target Clippy | PASS with `-D warnings` |
| Workspace and fuzz formatting | PASS |
| Default MSRV | Rust 1.88.0 workspace/all-target check PASS |
| ONNX MSRV | Rust 1.89.0 workspace/all-target/all-feature check PASS |
| Exact ONNX-enabled CPython 3.10 wheel | 19 tests PASS, including real ONNX collection/owned-stream equivalence and budget errors |
| Node WASM tests | 14 passed |
| Packaged web-target WASM | JS constructor, selected events, handle lifetime, limits, filters, vertical metrics and OCR candidate smoke PASS |
| Packaged Windows CLI | Text/build-info smoke and 7 pinned ONNX integration cases PASS |
| Rust core crate | `cargo package -p pdf-core --allow-dirty --locked` verification PASS; 70 packaged files |
| Fuzz: Layout/events/tables/navigation | 50,101 runs, 21 seconds, no crash; coverage 5,160 |
| Fuzz: images/OCR candidates | 69,184 runs, 21 seconds, no crash; coverage 3,503 |
| Fuzz: added filters | 81,280 runs, 21 seconds, no crash; coverage 368 |
| Python lint and documentation structure | Ruff PASS; README and OCR reference structure audits 100/100 |

Fuzzing used `nightly-2026-07-29` with the default AddressSanitizer on Windows MSVC.
The earlier unpinned/no-sanitizer build failed to link coverage symbols; the tested combination
is now explicit in CI. No synthetic result is counted as human gold.

Private corpus environment variables were not configured. Tests that internally skip an unconfigured
private corpus do not establish new private-document quality or throughput results. GitHub Actions
jobs were updated but not remotely dispatched; local platform evidence is Windows x64 plus Node WASM.

## Artifacts

All artifacts are local engineering builds; no registry publication or installed-runtime replacement occurred.

| Deliverable | Location |
|---|---|
| Rust core | [pdf-core-0.2.0.crate](../target/priority-artifacts/pdf-core-0.2.0.crate) |
| CLI, Windows x64 with optional native ONNX | [CLI archive](../target/priority-artifacts/rust-pdf-0.2.0-priority-20261012-windows-x86_64.zip) |
| Python, CPython 3.10 Windows x64 with optional native ONNX | [wheel](../target/priority-artifacts/wheels/rust_pdf_parser-0.2.0-cp310-cp310-win_amd64.whl) |
| Browser WASM | [WASM archive](../target/priority-artifacts/pdf-wasm-0.2.0-priority-20261012.zip) |
| Sizes and SHA-256 hashes | [artifact manifest](../target/priority-artifacts/manifest.json) |

Full copies of the already-declared MIT/Apache-2.0 licenses now replace the license-text placeholder
and are included in the deliverables. The unknown repository URL was omitted instead of inventing a
remote. External model/runtime binaries are not included in the archives or wheel.

## Remaining last-stage work

- Human reading-order double review/adjudication/timed pilot and private table/image gold remain deferred.
- Freeze new parser output before preparing the eventual human-review packet. Artifact SHA/build-info should
  identify development builds when invalidating caches; the legacy version tuple remains unchanged.
- Vertical metrics support W2/DW2 and known writing modes, not a complete predefined CJK CMap collection or glyph outlines.
- Direct OCR still conservatively rejects effects needing rendering. Native runtime/model allocations are separate from
  parser/image/output budgets. The real ONNX fixture verifies engineering integration, not broad OCR accuracy.

Reproducible logs and JSON evidence are under `target/priority-evidence/`; public fixture and model hashes are under
`tests/fixtures/priority/` and `tests/fixtures/ocr/`. Earlier mutable handoff claims should be checked against this report
and the current code. Source backups from the beginning of this work are under `target/priority-20261012-before/`.

## Documa source integration

The public engineering receipts are archived in [`evidence/priority-2026-10-12`](evidence/priority-2026-10-12).
The artifact manifest describes the earlier standalone local builds; these binaries and external models are not checked in.
The `target/` paths above belong to the standalone working copy, not this Git checkout.
Root `.github/workflows/rust-pdf.yml` runs the imported checks from `native/pdf`.
The installable `src/rust_pdf` API matches the native Python wrapper. Existing Documa provider defaults
and renderer/OCR behavior remain unchanged; human quality acceptance is still deferred.
