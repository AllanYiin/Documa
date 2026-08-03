# Stage 12 Stage 4A Definition of Done Evidence

Status: PASS (2026-07-29)

## Delivered contract

- `pdf-core` preserves bounded `Table -> TR -> TH | TD` hierarchy after RoleMap
  resolution instead of retaining only flattened MCID associations.
- Valid Table attributes preserve positive RowSpan/ColSpan and Row/Column/Both
  header Scope. Invalid topology preserves all visible source text and emits the
  stable `tagged_table_invalid` warning.
- Tagged tables use deterministic rectangular span placement. Mixed-page,
  overlapping, impossible, zero, or over-limit topology is never silently emitted.
- Every accepted table and non-empty cell carries optional structure object,
  LayoutSpace geometry, exact source-node links, confidence, provenance, and a
  stable rule ID. A logically empty tagged cell has `bbox = None` and
  `provenance = None` instead of fabricated geometry, and emits
  `table_cell_unassigned`.
- Table-contained semantic nodes remain present in source, tagged, inferred, and
  main-flow orders. Stage 4A adds table objects without replacing node IDs.
- `capabilities.tables = true` means reconstruction is available; an empty table
  array means no supported table was accepted.
- `ParseLimits` now includes explicit bounds for path segments, table candidates,
  accepted tables, and logical/physical table cells. Stage 4A directly enforces
  `max_tables` and `max_table_cells`; later substages own the remaining limits.

## Focused validation

```text
cargo test -p pdf-core --test stage12_table_reconstruction --all-features
4 passed; 0 failed
```

The focused fixtures cover exact tagged topology, TH scopes, RowSpan, ColSpan,
RoleMap aliases, honest empty-cell geometry, invalid-span recovery, source-node
order preservation, and exact/one-short table limits.

```text
cargo test -p pdf-core --test stage12_contract --all-features
14 passed; 0 failed
```

## Stage gate

```text
cargo fmt --all --check
PASS

cargo clippy --workspace --all-targets --all-features -- -D warnings
PASS

cargo clippy -p pdf-core -p pdf-wasm --all-targets --all-features \
  --target wasm32-unknown-unknown -- -D warnings
PASS

cargo test --workspace --all-features
PASS
```

The full workspace run includes Rust core, CLI, Python binding, WASM binding,
integration tests, and doctests. No third-party PDF parser, native engine, unsafe
core code, OCR, renderer, LLM, or embedding model was added.

## Gate decision

Stage 4A is complete. Stage 4B vector-path collection and ruled-table topology may
begin. Stage 4 as a whole remains incomplete; no private-corpus TEDS-S, table
precision/recall, performance, memory, or four-artifact release audit has run yet.
Default-provider cutover remains forbidden.