# Stage 12 Stage 6 Documa Shadow Adapter Contract

Specification version: 1
Status: Stage 6A through Stage 6D complete; default cutover remains NO-GO on quality gates

## Goal

Integrate the shared Rust Layout IR into Documa without changing the default PDF
provider until all correctness gates pass. Compare the complete current Documa
PyMuPDF path and the Rust path on identical inputs while keeping rollback trivial.

## Correct replacement boundary

Documa 0.6.1 does not use pdfplumber or pdfminer. Its default PDF parser is
`PyMuPDFAdapter`; PyMuPDF is also separately used to rasterize pages for OCR.
Stage 6 replaces the parser adapter path only. Page preview and OCR rasterization
remain on the existing renderer until Rust has an independently gated renderer.

## Substages

### Stage 6A: opt-in adapter and deterministic mapping

Add a lazy-loaded `RustPdfAdapter` and explicit `pdf_provider="rust"` registry
selection. The default remains `pymupdf`. Map schema-version 1
`layout_unrotated_top_left` data into parser-neutral Documa IR:

- every source node remains traceable through stable source refs and metadata;
- complete inferred order provides page block order, while all four Rust orders
  remain preserved as metadata;
- explicit Header/Footer/PageNumber/Table/Figure/Caption/Artifact roles are
  preserved instead of being silently flattened;
- Rust table topology becomes one Documa table candidate with cell spans/roles
  retained in metadata and covered cells represented honestly;
- content and author Figure placements become `ImageIR` occurrences with source-node
  and caption/Alt evidence; heuristic/Artifact decorative occurrences remain in Rust
  Layout IR but are aggregate-only in Documa by default, with an explicit reversible
  opt-in; encoded bytes are optional assets, never assumed to be rendered pixels;
- Link, destination, outline, warning, capability, and parser provenance metadata
  remain available without executing actions.

Reject unknown schema versions or coordinate spaces with a stable Documa error.
Missing optional Rust bindings must not break default PyMuPDF parsing.

### Stage 6B: shadow parity and performance

Run PyMuPDF Documa and Rust Documa adapters independently over the frozen 7 PDFs.
Record parse success, page counts, normalized character F1, reading-order proxy,
table/image/navigation counts, silent-loss diagnostics, deterministic hashes,
durations, peak RSS, and serialized size. Reports must not store private text,
URLs, image bytes, or full IR.

### Stage 6C: page-level or streaming transfer

The Stage 5 full Layout IR is 1.290143x the Stage 4 serialized size. Replace the
whole-document JSON round trip with a page-level or streaming binding before
cutover, or prove an equivalent memory bound. Do not retain full JSON bytes and
the decoded Python object graph simultaneously for a large document.

### Stage 6D: compact mapping, gate, and rollback

Default Documa mapping uses `compact_trace_v1`: one document-level schema defines
source ordinal bounds, MCIDs, text origins, and rule ID while page object and
coordinate space are inherited from page/document metadata. Stable source refs,
semantic roles, tagged evidence, table/image/navigation evidence, and all four
orders remain available. `rust_pdf_include_verbose_metadata=True` restores the
legacy verbose metadata shape.

Keep provider selection explicit and reversible. A failed character, reading
order, table, silent-loss, determinism, or memory gate forbids default cutover.
Timing alone cannot override correctness. Preserve the PyMuPDF adapter and its
existing tests until Stage 8 removes rollback after real production evidence.

## Current evidence and decision

Stage 6C2-E completed genuine native page production but the first complete
Documa shadow still measured 946,515,968-byte Rust RSS, or 1.553468x PyMuPDF.
Stage 6D then removed mapped-IR amplification without changing Rust parser output:

- compact metadata reduces the 423-page AI Index canonical IR from 87,313,096 to
  54,548,123 bytes and encoded metadata from 47,522,349 to 15,501,820 bytes;
- the privacy-safe lifecycle profile separates a 356,667,392-byte parse peak from
  a 564,396,032-byte canonical-serialization peak;
- the final 7-document / 1,113-page, one-warm-up / three-run shadow measures Rust
  at 34.704637 pages/s versus PyMuPDF at 5.976338 pages/s (5.807007x);
- maximum RSS is 646,643,712 bytes for Rust and 612,139,008 for PyMuPDF, or
  1.056367x. Stage 6D memory gate is complete because this is below 1.2x;
- every provider/document group is byte-deterministic, and Rust text SHA plus
  block/span/semantic counts remain exact against Stage 6C2-E.

Character F1 remains 0.960813 and bigram F1 0.951281. The earlier tagged-order
proxy remains 0.940546, and private labeled table/image gold is absent. Therefore
default-provider cutover remains forbidden even though timing, determinism, and
memory now pass.

Table and image provider counts are not interchangeable accuracy metrics. Rust
counts conservative reconstructed tables and paint occurrences; PyMuPDF counts
finder candidates and image blocks. Private labeled gold remains mandatory.

## Definition of Done

- [x] Opt-in Rust adapter maps text, roles, tables, images, navigation, warnings,
  provenance, coordinates, and all four orders into Documa IR.
- [x] Default registry behavior remains byte-compatible and uses PyMuPDF.
- [x] Missing Rust bindings and malformed/unsupported Rust Layout IR have stable
  recoverable Documa errors.
- [x] Adapter unit/integration tests and existing Documa tests pass.
- [x] Frozen-corpus shadow report is deterministic, privacy-safe, and records all
  Go/No-Go metrics.
- [x] Whole-document duplicate-memory risk is removed or bounded before cutover.
- [x] Default-provider cutover remains forbidden unless every global gate passes.