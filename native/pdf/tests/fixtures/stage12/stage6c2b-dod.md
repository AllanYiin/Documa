# Stage 12 Stage 6C2-B Definition of Done

Status: Complete; Stage 6C2-C may begin

## Scope completed

- Added an internal `TextPageProducer` that decodes one page at a time while
  sharing the document resolver, monotonic DecodeBudget, bounded object-stream
  cache, source ordinal, limits, and vector collection state.
- `build_layout_document` now converts each page directly into `PageLayout` and
  drops that page's positioned glyphs, marked-content metadata, separators, and
  parsed content operations before extracting the next page.
- Removed the former complete `ExtractedTextV2Details`, `glyphs_by_page`, and
  document-wide Layout positioned-glyph redistribution path.
- Preserved the public complete text APIs, Layout IR schema, stable IDs, warnings,
  quality counters, vector fallback behavior, and coordinate contract.

## Exactness, limits, and private evidence

- Direct producer tests pass 2/2: two-page delivery keeps global source ordinals,
  and the document glyph limit accepts the exact first-page boundary then rejects
  the second glyph with stable `limit_exceeded` behavior.
- All pdf-core tests and doctests pass, including the existing DecodeBudget,
  cache, content-operation, positioned-glyph, legacy Layout, and Auto boundaries.
- Configured 423-page AI Index and 15-page Taiwan private tests pass all
  ContentOrder, Layout, and Auto contracts.
- Frozen 7-document/1,113-page Layout IR parity matches every Stage 6C2-A
  canonical SHA-256 and serialized byte count.
- Privacy-safe core report:
  `target/stage12-stage6c2b-page-text/report.json`
- Report SHA-256:
  `8da9a29de5cea7fa07997133076537f4669ae157ef3ef76881904ff0e9a7d4f2`
- Core throughput: 212.467582 pages/s, 1.053130x Stage 6C2-A.
- Core peak RSS: 444,100,608 bytes, 0.658150x Stage 6C2-A (-34.1850%).

## Artifact and Documa regression

- Exact CPython 3.10 wheel: 1,071,719 bytes, SHA-256
  `358e116ae8ede8c7d2dba8e79400adce374842c2daa0bcb5319fbdf647e7e74c`.
- Exact wheel binding tests pass 11/11.
- Documa Rust adapter/reading-order focused tests pass 17/17.
- Documa full suite with explicit snapshot plugins passes 353/353.

An interim one-warm-up/one-measured-run complete Documa shadow is recorded at
`target/stage12-stage6c2b-documa-shadow-r1/report.json`, SHA-256
`7f1d17be13124b0e111330c123388b248ab0359c573c18308394f0db80084e26`.
It measured Rust 27.818060 pages/s versus PyMuPDF Documa 7.461973 pages/s
(3.727976x), with unchanged character/bigram F1 of 0.960813/0.951281. This is
interim directional evidence, not a replacement for the formal three-run report.

## Gate decision

Stage 6C2-B is complete and Stage 6C2-C may begin. The complete Documa Rust peak
RSS remains 884,908,032 bytes, 1.454378x the measured PyMuPDF maximum of
608,444,416 bytes, so the 1.2x gate still fails. The compatibility event producer
still owns the complete `DocumentLayout`; page-indexed tagged/navigation inputs,
delayed furniture patches, and native Python event consumption remain required.
Default-provider cutover remains forbidden.
