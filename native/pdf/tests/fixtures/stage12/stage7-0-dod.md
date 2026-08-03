# Stage 12 Stage 7.0 Definition of Done

Status: Quality contract complete; Stage 7.1 may begin

## Delivered contract

- Technical, nontechnical, and Codex/Claude Code plans freeze Stage 7 text,
  human-order, artifact, table, image, determinism, memory, speed, privacy, and
  rollback gates.
- The plan separates character completeness from human reading order. Content,
  tagged, inferred, and main-flow orders remain explicit and non-aliasing.
- PyMuPDF remains an offline shadow oracle only; it is not runtime truth or human
  gold and is not added to Rust.
- Stage 7.1 must localize page-level differences without persisting text,
  character keys, source paths, URLs, complete counters, or private IR.
- Table/image and final human-order gates require human annotation. Missing gold
  is BLOCKED, never PASS.

## Research evidence

The research gate used four searches and two page reads, then stopped. It reviewed
PDF Association logical-order guidance, official PyMuPDF/pdfminer.six/PDFBox
text-order behavior, and official Docling/Marker/Unstructured repositories. The
spec records direct sources and the 2026-07-29 check date.

## Frozen local evidence

- Stage 6D global character/bigram F1: 0.9608131914/0.9512812709.
- The dominant cases are the 580-page document at 0.9446456204 character F1 and
  AI Index at 0.9643682109. Two other documents already exceed 0.995.
- Stage 6D memory remains PASS at 1.056367x PyMuPDF and Rust remains 5.807007x
  faster at complete-adapter level. Quality work must preserve those gates.

## Validation

- Nontechnical spec plain-language checker passes with zero banned terms.
- Stage 12 contract asserts all three spec files, exact thresholds, Stage 7.1
  privacy boundary, penultimate integration stage, and final rollback stage.
- Formatting, Clippy, and workspace tests are required before Stage 7.1 starts.

## Gate and next stage

Stage 7.0 changes no parser behavior and does not open default cutover. Stage 7.1
implements the privacy-safe page differential profiler and must reproduce Stage
6D aggregate scores before any extraction heuristic changes.
