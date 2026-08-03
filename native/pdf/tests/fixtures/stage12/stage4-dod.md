# Stage 12 Stage 4 Definition of Done Evidence

Status: PASS (2026-07-29)

## Delivered contract

- `pdf-core` owns tagged, vector-lattice, borderless-text, and fused table
  reconstruction. Bindings only serialize the shared Layout IR.
- Valid author-tagged row/column topology, `RowSpan`/`ColSpan`, header roles,
  structure objects, provenance, and source-node links have highest precedence.
- A compatible vector lattice refines the table and cell LayoutSpace BBoxes and
  produces `evidence = fused`; it never overwrites author spans or header roles.
  A topology mismatch preserves the tagged table and emits one aggregated
  `table_evidence_conflict` warning.
- Borderless detection remains conservative. Rejected candidates remain ordinary
  semantic nodes; table acceptance never removes nodes or entries from source,
  tagged, inferred, or main-flow orders.
- Text and vector extraction reuse the same parsed top-level content operations
  per page. Vector collection completes before those operations are released, so
  Stage 4 avoids a second page decode without retaining a document-wide operation
  graph. Form recursion, path segments, candidates, tables, cells, and all
  existing parser resources remain bounded.
- Public table and cell geometry is `layout_unrotated_top_left`: normalized
  CropBox top-left, x right, y down, points after UserUnit, with page Rotate not
  applied. Active CTM and Form Matrix are applied before exactly one PDF-to-layout
  projection.

## Synthetic and interface validation

```text
cargo test -p pdf-core --test stage12_table_reconstruction
16 passed; 0 failed

cargo test -p pdf-core --test stage12_contract
14 passed; 0 failed

cargo test -p pdf-cli --test layout
3 passed; 0 failed

exact built Python wheel + PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
8 passed; 0 failed

wasm-pack test --node bindings/wasm
8 passed; 0 failed
```

Coverage includes tagged spans and header roles, RoleMap aliases, empty cells,
ruled and fragmented grids, direct and Form CTMs, fill-only/decorative negatives,
borderless numeric tables, prose/key-value/misalignment negatives, exact and
one-short limits, fusion and conflict precedence, CJK, multiline cells, mixed font
sizes, and `/Rotate 90` metadata with unrotated LayoutSpace geometry.

The supported synthetic fixtures have exact table count, dimensions, cell text,
spans, roles, BBoxes, and source-node links; exact equality implies TEDS-S 1.0 for
those fixtures.

## Formal private-corpus benchmark

`tools/stage12_table_benchmark.py` ran one warm-up and three measured release-CLI
runs over 7 PDFs / 1,113 pages. The report is private-content safe and stores no
extracted text, node arrays, cell contents, or private IR.

- throughput: 205.731635 pages/s;
- speedup versus frozen complete Documa adapter: 28.309930x;
- throughput ratio versus Stage 3: 0.988694 (1.1306% feature cost);
- sum of document median durations: 5.409960 seconds;
- maximum sampled process RSS: 674,615,296 bytes;
- serialized bytes for one run of every document: 331,010,437;
- accepted tables/cells: 5 / 76;
- evidence: fused 1, vector lattice 2, text alignment 2;
- all 7 groups byte-deterministic across measured runs;
- all schema and privacy audits passed;
- report SHA-256:
  `2a49875c7da1bf81145e27cad299f4b24a9c97ab4ef46e2193efe55d12e73de8`.

The private corpus has no table ground truth, so private table precision, recall,
cell-text F1, and TEDS-S are not reported; `private_teds_s` is explicitly null.
The absolute RSS is recorded, but no comparable Stage 3 RSS baseline existed, so
the 1.2x memory ratio gate is not claimed.

## Release-candidate artifacts

- Python CPython 3.10 wheel: 971,837 bytes, SHA-256
  `4133713b7696b1e97891cdff44d2096b56046f356f23459461e2364d6e87b088`;
- browser WASM: 1,327,069 bytes, SHA-256
  `50ec697c7897905e439957f41f0824b4e1fabe30aee34323fbf823c2d9252931`.

## Stage gate

```text
cargo fmt --all --check
PASS

cargo clippy --workspace --all-targets --all-features -- -D warnings
PASS

cargo clippy -p pdf-core -p pdf-wasm --target wasm32-unknown-unknown \
  --all-features -- -D warnings
PASS

cargo test --workspace --all-features
PASS
```

## Gate decision

Stage 4 is complete and Stage 5 image-placement and caption-flow work may begin.
Default-provider cutover remains forbidden: normalized character F1 has not met
0.995, the tagged reading-order proxy remains 0.940546 below 0.95, and private
table ground truth is still absent. Public version metadata intentionally remains
`0.2.0` / `stage-11` until the later release gate.