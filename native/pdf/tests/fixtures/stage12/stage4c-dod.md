# Stage 12 Stage 4C Definition of Done Evidence

Status: PASS (2026-07-29)

## Delivered contract

- Borderless candidates are rebuilt from Stage 3 semantic-node geometry, not
  PDF-native coordinates or source array order. Nodes are re-clustered into
  LayoutSpace rows even when the inferred node vector is column-major.
- Acceptance requires at least three rows, consistent column counts, stable left
  or right column alignment, and explicit intra-row gaps. Two-column candidates
  additionally require four rows and a 75% numeric column below the first row.
- Colon-dominant key-value layouts, parallel prose, lists/non-horizontal content,
  furniture, Artifact, misaligned columns, and already claimed table nodes are
  excluded. Ambiguous candidates remain ordinary text and emit one aggregated
  `table_detection_ambiguous` warning per page.
- Accepted text cells preserve node BBoxes, text, provenance, source-node IDs,
  main-flow membership, confidence, and stable rule IDs. No source node or order
  entry is removed.
- Text candidate/table/cell growth is bounded by `max_table_candidates`,
  `max_tables`, and `max_table_cells`, including exact and one-short tests.

## Validation

```text
cargo test -p pdf-core --test stage12_table_reconstruction --all-features
13 passed; 0 failed

cargo test -p pdf-core --test stage12_contract --all-features
14 passed; 0 failed

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

Positive fixtures cover three-column and conservative two-column numeric tables.
Negative fixtures cover key-value pairs, parallel prose, and misalignment.

## Gate decision

Stage 4C is complete. Stage 4D fusion, private-corpus table/performance audit,
cross-front-end artifacts, and the final Stage 4 gate may begin. Default-provider
cutover remains forbidden.