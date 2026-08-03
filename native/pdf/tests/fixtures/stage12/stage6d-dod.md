# Stage 12 Stage 6D Definition of Done

Status: Memory and determinism gates complete; default-provider cutover remains NO-GO

## Delivered contract

- Documa maps Rust node/span provenance to `compact_trace_v1` by default. One
  document-level schema defines `[source_ordinal_start, source_ordinal_end, mcids,
  text_origins, rule_id]`; page object and coordinate space are inherited from
  page/document metadata instead of repeated for every block and span.
- Stable `BlockIR.source_refs`, block/span IDs, BBoxes, page numbers, semantic
  roles, tagged evidence, table topology, image evidence, navigation, warnings,
  and all four Rust orders remain available. Citation tools continue to resolve
  real blocks and visual BBoxes without consulting verbose Rust metadata.
- Repeated per-block reading-order dictionaries, duplicate page main-flow lists,
  child coordinate-space strings, empty optional fields, and redundant table
  source text are omitted in the compact profile.
- `ParseOptions.metadata["rust_pdf_include_verbose_metadata"] = True` restores
  the previous verbose evidence shape. PyMuPDF remains the default provider and
  remains the renderer for OCR/page previews.

## Privacy-safe lifecycle profile

The isolated 423-page AI Index profile separates object-lifetime peaks from
canonical serialization and never writes source text, paths, URLs, or IR:

- parse peak RSS: 356,667,392 bytes;
- metadata-scan peak RSS: 228,425,728 bytes;
- canonical-serialization peak RSS: 564,396,032 bytes;
- canonical Documa IR: 54,548,123 bytes;
- aggregate encoded metadata estimate: 15,501,820 bytes across 285,391 fields;
- report: `target/stage12-stage6d-metadata-profile/compact-final.json`, SHA-256
  `14f4b276fca4583e3784850c43cc6889513d7fc34fb07ffb67f06a22182c054b`.

Against the pre-compaction profile, canonical IR falls from 87,313,096 bytes to
54,548,123 (-37.53%), encoded metadata from 47,522,349 to 15,501,820 (-67.38%),
and the isolated canonical-serialization peak from 942,632,960 to 564,396,032
(-40.13%).

## Formal shadow evidence

The final benchmark used the frozen 7 PDFs / 1,113 pages, one warm-up, and three
measured runs per provider/document. Both providers are deterministic for every
case; the report is privacy-safe and writes no private IR.

- Rust Documa: 34.704637 pages/s, 646,643,712-byte maximum RSS, and
  103,285,955 total serialized bytes.
- PyMuPDF Documa: 5.976338 pages/s, 612,139,008-byte maximum RSS, and
  69,306,577 total serialized bytes.
- Rust speedup: 5.807007x. Rust RSS ratio: 1.056367x, below the 1.2 gate.
- All seven Rust text SHA-256 values and block/span/semantic counts remain exact
  against Stage 6C2-E. Invalid BBox count remains zero.
- Character/bigram F1 remains 0.9608131914/0.9512812709.
- Report: `target/stage12-stage6d-documa-shadow-final/report.json`, SHA-256
  `245966517805ae6d4689355307c7bd12e1f8675b41b87476bc600759a10ac44d`.

## Validation

- Documa Rust adapter/reading-order focused tests pass 18/18.
- Full Documa suite passes 354/354 with explicit snapshot plugins; full Ruff passes.
- `documa doctor --project-root .` passes 8/8 and fixture readiness passes 18/18.
- The exact Stage 6C2-E wheel remains the native input: 1,100,990 bytes, SHA-256
  `5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61`.

## Gate and next stage

Stage 6D memory, deterministic-output, citation-trace, and rollback gates are
complete. Default-provider cutover remains forbidden because character F1 is
below 0.995, the tagged-order proxy remains below 0.95, and private table/image
gold labels are absent. The next stage must improve text and human reading order,
then establish labeled table/image accuracy; timing cannot waive those gates.
