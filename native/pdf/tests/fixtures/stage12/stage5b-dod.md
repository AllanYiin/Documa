# Stage 12 Stage 5B Definition of Done

Date: 2026-07-29
Status: Complete; Stage 5C may begin

## Delivered contract

- Image occurrences retain current marked-content tag, MCID, author Alt, and
  Artifact state through page and nested Form traversal.
- Validated StructTree Figure associations provide authoritative structure object,
  tag, Alt text, confidence, and stable source-node relationships.
- Author Caption roles and conservative Figure/Fig./CJK caption prefixes may link
  by bounded LayoutSpace gap and horizontal compatibility.
- Table-owned, Header/Footer/PageNumber, and Artifact nodes cannot become captions.
- Equal candidates remain unlinked with `figure_caption_ambiguous`; author figures
  without an anchor emit aggregated `image_placement_unassigned`.
- Semantic nodes and source/tagged/inferred/main-flow arrays remain unchanged.

## Validation evidence

- `cargo test -p pdf-core --test stage12_figure_flow`: 5/5 passed.
- `cargo test -p pdf-core --test stage12_image_placements`: 5/5 passed.
- `cargo test -p pdf-core --test stage12_contract`: 16/16 passed.
- `cargo test -p pdf-cli --test layout`: 4/4 passed.
- Exact CPython 3.10 wheel tests: 6/6 passed.
- `wasm-pack test --node bindings/wasm`: Stage 11/12 suite 7/7 and web suite 2/2 passed.
- `cargo fmt --all --check`: passed.
- Native workspace all-target/all-feature Clippy with `-D warnings`: passed.
- wasm32 core/WASM all-target/all-feature Clippy with `-D warnings`: passed.
- `cargo test --workspace`: passed, including doctests.

## Artifact

- CPython 3.10 wheel: 988,466 bytes; SHA-256
  `951a625e54b36ccaefc0796543d747d4488b9a6dafb396653e2bcb6c262e537f`.

Default-provider cutover remains forbidden until all global Go/No-Go gates pass.