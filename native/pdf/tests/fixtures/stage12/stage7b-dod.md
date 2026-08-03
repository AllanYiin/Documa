# Stage 12 Stage 7.2 Definition of Done

Status: Raw text completeness gate complete; Stage 7.3 may begin

## Corrected quality boundary

- Stage 7.1 compared complete Documa adapters. The PyMuPDF side had already run
  `find_tables()` and replaced overlapping blocks with reconstructed table text.
- Direct probes on the former worst pages showed Rust glyph, page-root, and
  semantic-node character multisets agree with PyMuPDF raw text blocks. The
  apparent large shortfall was adapter/table rewriting, not parser text loss.
- A dedicated comparator now separates `pymupdf_raw` from
  `rust_layout_source`. Complete counters remain temporary and the final report
  contains no extracted text, character keys, source paths, URLs, or private IR.

## Formal corpus evidence

- Report: `target/stage12-stage7b-parser-text/report.json`
- Report SHA-256:
  `5281d646379a5c38686b93a510c4af84ebe96d9e4419dd338a13f5c547c14f87`
- Corpus: 7 PDFs / 1,113 pages; all page numbers and counts align.
- Aggregate raw non-whitespace character F1 is `0.9989543801655596`
  (precision `0.9989666368200116`, recall `0.998942123811866`).
- Aggregate raw character-bigram F1 is `0.99607461637057`
  (precision `0.9960868377384583`, recall `0.9960623953025756`).
- All seven per-document character F1 values exceed 0.995; the minimum is
  `0.9979043800491556` for AI Index.
- 25 pages are below per-page character F1 0.995; 155 pages are below per-page
  bigram F1 0.99. These are follow-up clusters, not a failed aggregate gate.
- Rust page-root character multisets match source-order semantic nodes on
  1,113/1,113 pages, proving the layout grouping path does not silently drop
  decoded page characters on this corpus.

## Calibration timing

- One process-isolated pass records Rust `113.0875 pages/s` and PyMuPDF raw
  `97.7086 pages/s`, a Rust ratio of about 1.157x.
- This is quality-calibration timing, not the three-run performance release
  benchmark. Stage 6D remains the formal complete-adapter speed/RSS evidence.

## Decision and handoff

- The raw text completeness gates (character F1 at least 0.995 and global
  bigram F1 at least 0.99) pass without a corpus-specific parser heuristic.
- No pdf-core, public schema, Documa adapter, or provider-default behavior is
  changed. Repeating PyMuPDF table text in Rust would be a regression.
- Stage 7.3 may build reviewed human reading-order and artifact gold. Table and
  image/caption gold remain separate Stage 7.5 blockers.
- Default-provider cutover remains forbidden until order and labeled semantic
  gates pass together.

## Validation

- Raw comparator self-test and Python compilation: PASS.
- Formal 7-document raw comparison and privacy audit: PASS.
- Stage 12 contract, formatting, Clippy, workspace tests, and doctests are
  required by the stage gate and recorded in `DEVNOTE.md`.