# Stage 12 Technical Specification: Documa PDF Parser Replacement

Specification version: 1
Status: Stage 0 through Stage 6D complete; default cutover NO-GO on quality and labeled-gold gates

## Scope and ownership

- `crates/pdf-core` owns every PDF-aware rule: extraction, tagged structure,
  geometry, reading order, page furniture, tables, image placements, and navigation.
- Python, WASM, and CLI bindings convert values, options, and stable errors only.
- The Documa adapter maps Rust Layout IR into Documa IR. Documa continues to own
  sections, chunking, search, citation rendering, domain policy, and LLM logic.
- This stage does not add a third-party PDF parser or renderer and does not add
  OCR, decryption, repair, or complete raster rendering.

## Stage 0 baseline

Measure all three paths against the same files, machine, options, and run policy:

1. PyMuPDF raw `Page.get_text("dict")`.
2. Current Documa `PyMuPDFAdapter.parse`, without preview rendering.
3. Rust release CLI `extract --json --mode auto`.
4. Stage 6A/6B add the opt-in Documa Rust provider and formal shadow diff;
   Stage 6C adds native lazy transfer and Stage 6D compacts mapped Documa metadata.

Each corpus case verifies SHA-256 before measuring and records byte length, page
count, structural counts, canonical result hashes, warnings, elapsed time,
quality proxies, and coordinate anomaly counts.

Timing is diagnostic evidence, not a gate by itself. Full private IR is written
only when `--write-private-ir` explicitly targets a secured local output.

## Go/No-Go

| Measure | Required result before default-provider cutover |
|---|---|
| corpus parse success | 100% of the frozen corpus |
| page count | exact baseline parity |
| normalized character F1 | at least 0.995 |
| reading-order score | no regression; gold target at least 0.95 |
| table TEDS-S | no regression; gold target at least 0.90 |
| silent data loss | zero |
| deterministic hash | 100% for identical input/version/options |
| parse+layout+table median | no regression; stretch goal 30% faster |
| peak memory | no more than 1.2 times baseline |

Current Stage 6D shadow evidence is still a cutover NO-GO on quality, not memory:
normalized character F1 is 0.960813 and the earlier tagged-order proxy is 0.940546.
The final one-warm-up / three-run complete adapter benchmark measures Rust at
34.704637 pages/s, 5.807007x PyMuPDF, with a passing 1.056367x peak-RSS ratio.
Private labeled table/image gold remains absent. Timing and memory success do not
override failed or unevaluated correctness gates.
Failure of a correctness, data-loss, or quality gate forbids changing Documa's
default provider to Rust.

## Layout IR minimum contract

`DocumentLayout` carries schema/parser versions, options digest, warnings,
quality, timings, page boxes/rotation/transforms, semantic nodes, tables, image
placements, and four explicit orders. Every decision has confidence, provenance,
object id, MCID where applicable, and rule id.

Normal binding output is block/span/table level. Glyph output is debug-only.

The four orders are `source_order`, `tagged_order`, `inferred_order`, and
`main_flow`. Headers, footers, and page numbers remain preserved and classified;
they are not silently deleted.

## Stage gate

Before completing any substage, run focused tests, `cargo fmt --all --check`,
workspace all-target Clippy with `-D warnings`, and workspace tests. Record the
current output and check every Definition of Done item.
