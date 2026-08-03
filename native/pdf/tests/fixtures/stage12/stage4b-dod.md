# Stage 12 Stage 4B Definition of Done Evidence

Status: PASS (2026-07-29)

## Delivered contract

- `graphics.rs` is the shared finite affine-matrix and resource-dictionary layer
  used by text and vector traversal. Text/Form coordinate behavior did not fork.
- `vector_paths.rs` collects only stroke-painted straight segments from `m`, `l`,
  `re`, and `h`, with bounded `q/Q/cm`, nested Form Matrix/resource traversal,
  cycle checks, operation budgets, and document path-segment limits.
- Every path point applies active CTM/Form Matrix in PdfUserSpace, then exactly one
  `PageGeometry.pdf_to_layout` projection. Public grid/cell geometry is normalized
  top-left LayoutSpace with x right and y down.
- Nearly collinear fragments join within fixed tolerances. Fill-only paths,
  isolated grids without distributed text, curves, skewed rules, and malformed
  optional paths do not silently become tables.
- Closed cells are found in a bounded coordinate grid, grouped by connected
  components, required to form a full rectangle of at least 2x2 cells, and
  assigned text by one page-node pass. Tagged table nodes have precedence and are
  excluded from vector candidates.
- Vector cells keep physical grid BBoxes even when empty; text, provenance, and
  source-node links remain honest. Source semantic nodes and all order arrays are
  preserved.

## Focused validation

```text
cargo test -p pdf-core --test stage12_table_reconstruction --all-features
9 passed; 0 failed
```

Coverage includes tagged Stage 4A regressions plus ruled `re/m/l` topology,
fragment joins, fill-only negatives, direct `q/Q/cm`, Form Matrix, exact
LayoutSpace direction, malformed optional path recovery, and exact/one-short
segment/candidate limits.

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

## Gate decision

Stage 4B is complete. Stage 4C conservative borderless-text reconstruction may
begin. Stage 4 remains incomplete and default-provider cutover remains forbidden.
The current Layout path performs a second bounded content/Form decode for vector
collection; Stage 4D must benchmark this cost and combine traversal if required
by the speed or document decode-budget gates.