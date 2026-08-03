# Stage 12 Stage 1B Definition of Done

Audit date: 2026-07-29 (Asia/Taipei)
Status: PASS

## Delivered contract

- `crates/pdf-core/src/layout_ir.rs` owns schema version 1 and every PDF-aware
  projection rule. The normal result is block/span level; debug glyphs and
  timings are explicit opt-ins.
- All public page, node, span, and debug-glyph geometry is expressed in the
  exact `layout_unrotated_top_left` LayoutSpace established by Stage 1A.
- `source_order`, `tagged_order`, `inferred_order`, and `main_flow` are separate
  arrays. Stage 1B populates only `source_order`; unavailable capabilities are
  false and their arrays remain empty.
- Nodes and spans have deterministic IDs, normalized BBoxes, Quads, confidence,
  stable rule IDs, and provenance containing the page object, source ordinals,
  MCIDs, and text origins.
- `layout_text_bbox_estimated` documents the Stage 1B vertical-extent estimate.
  Existing text extraction JSON and native `PositionedGlyph` PDF coordinates
  remain unchanged.
- Rust, CLI, Python, and browser WASM expose the same core-owned schema through
  `PdfDocument::extract_layout`, `rust-pdf layout`, `rust_pdf.extract_layout`,
  and `extractLayout`.

## Complexity and determinism corrections

Before the formal run, glyphs were bucketed by page once. This removed a
page-count multiplied by document-glyph-count scan from Layout IR construction.
Span gap thresholds now use LayoutSpace BBox height, so non-default `/UserUnit`
does not mix native font units with normalized page geometry. MCID matching no
longer allocates a temporary vector for every candidate merge.

Default results omit non-deterministic timing values. WASM rejects requested
layout timings with the stable `unsupported_feature` code instead of touching an
unsupported clock or panicking.

## Formal private-corpus schema, privacy, and performance audit

The release CLI processed the frozen seven-document corpus with one warm-up and
three measured runs per document. Timing includes process startup, parse, layout,
JSON serialization, and stdout capture. JSON decoding, schema audit, hashing,
and report writing are excluded.

| Measure | Result |
|---|---:|
| documents | 7 |
| pages | 1,113 |
| sum of document medians | 4.056804 s |
| Layout IR throughput | 274.353900 pages/s |
| frozen Stage 0 Documa throughput | 7.267 pages/s |
| speedup vs frozen Stage 0 Documa | 37.752772x |
| deterministic document groups | 7 / 7 |
| schema audits | 7 / 7 |
| privacy audits | 7 / 7 |
| semantic nodes | 1,113 |
| text spans | 133,503 |
| serialized bytes, one run per document | 271,882,351 |

The speedup is a non-simultaneous comparison against the frozen Stage 0 report,
not a same-run microbenchmark. Stage 0 Rust used `extract --mode auto`, so its
50.805 pages/s result is not directly comparable to source-order Layout IR.

The 271.9 MB result size is an integration cost even though throughput is high.
A page-level or streaming transfer API should be considered before Documa
cutover; this does not change the Stage 1B schema contract.

Formal report:

```text
target/stage12-layout-benchmark/report.json
SHA-256 00f244b330419058e13dd019a6e9c88aeebfab58bd084bc5f7c75b5e29a49345
```

The report is 10,884 bytes and records only corpus identifiers, hashes, counts,
timings, output sizes, and audit booleans. `contains_extracted_content` and
`private_ir_written` are both false. No private Layout IR was persisted.

## Front-end artifacts

| Artifact | Result |
|---|---|
| Python wheel | 771,432 bytes; SHA-256 `d3539523dac5354021f37c76c06b99271785248f9da9fc7d685edc2f44c43d8d` |
| Python isolated tests | 6 passed |
| WASM web package | `extractLayout` present in generated declarations |
| WASM binary | 1,029,347 bytes; SHA-256 `fe8cc4184a69872df9d2047f702d7c78a872db84474b9b5b6c514242bc864a2c` |
| Node wasm-bindgen tests | 6 passed |

## Executed gate

```text
python -m py_compile tools\stage12_layout_benchmark.py                  PASS
python tools\stage12_layout_benchmark.py --self-test                   PASS
cargo test -p pdf-core --test stage12_layout_ir                         3 passed
cargo test -p pdf-core --test stage12_contract                          6 passed
cargo test -p pdf-cli --test layout                                     1 passed
cargo fmt --all --check                                                  PASS
cargo clippy --workspace --all-targets -- -D warnings                    PASS
cargo test --workspace                                                   PASS
python -m pytest bindings\python\tests -q                              6 passed
wasm-pack test --node bindings\wasm                                    6 passed
```

The workspace run contains one pre-existing intentionally ignored manual Stage
11 benchmark. All executed tests passed.

## Completion audit

- [x] Layout IR schema names, version, digest, and serialization are stable.
- [x] Every public Layout IR geometry value uses the Stage 1A coordinate contract.
- [x] Four order arrays and capability states are explicit and non-aliasing.
- [x] IDs, bounds, provenance, confidence, rule IDs, warnings, and opt-ins are tested.
- [x] Rust, CLI, Python, and WASM expose the shared core schema.
- [x] Legacy extraction shapes and native positioned-glyph coordinates are preserved.
- [x] The full private corpus is deterministic and passes schema/privacy audits.
- [x] Focused, formatting, Clippy, front-end, and workspace gates pass.

Stage 1B is complete. Stage 2 may begin. Default-provider cutover remains
forbidden until the later reading-order, table, integration, resource, and
quality gates pass.