# Stage 12 Stage 5C Definition of Done

Date: 2026-07-29
Status: Complete; Stage 5D may begin

## Delivered contract

- Page Link annotations preserve LayoutSpace rectangles and optional QuadPoints.
- URI and GoTo actions, old-style destinations, destination name trees, and
  outline preorder are represented by shared Rust DTOs with stable targets.
- JavaScript, Launch, embedded-file execution, and unknown actions are never
  executed; they remain warned unsupported metadata.
- Malformed optional navigation preserves visible text and later valid entries.
- `max_annotations`, `max_named_destinations`, `max_outline_items`, and existing
  object-depth limits bound all document-wide navigation traversal.
- CLI, Python, and WASM serialize the same `pdf-core` navigation result.

## Validation evidence

- `cargo test -p pdf-core --test stage12_navigation`: 3/3 passed.
- `cargo test -p pdf-core --test stage12_contract`: 17/17 passed.
- `cargo test -p pdf-cli --test layout`: 5/5 passed.
- Exact CPython 3.10 wheel tests: 7/7 passed.
- `wasm-pack test --node bindings/wasm`: Stage 11/12 suite 8/8 and web suite 2/2 passed.
- `cargo fmt --all --check`: passed.
- Native workspace and wasm32 Clippy with `-D warnings`: passed.
- `cargo test --workspace --all-features`: passed, including doctests.

## Artifact

- CPython 3.10 wheel: 1,026,878 bytes; SHA-256
  `290a07aab92b1cdcadbf7693b87d8b2490338840e607546d72e712cc8af55cca`.

Default-provider cutover remains forbidden until all global Go/No-Go gates pass.