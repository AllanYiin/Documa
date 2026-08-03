# Stage 12 Stage 5A Definition of Done

Date: 2026-07-29
Status: Complete; Stage 5B may begin

## Delivered contract

- Every painted Image XObject `Do` occurrence is collected in the shared bounded
  page/Form graphics traversal without decoding pixels.
- Public geometry is `layout_unrotated_top_left`: CropBox-relative, UserUnit-scaled,
  x right, y down, and page Rotate unapplied.
- The image unit-square corners `(0,1)`, `(1,1)`, `(1,0)`, `(0,0)` retain identity
  through q/Q/cm, nested Form matrices, and exactly one `pdf_to_layout` projection.
- Repeated, direct, indirect, mirrored, rotated, sheared, and Form-nested placements
  retain stable page paint ordinals, resource paths, object IDs, Quads, and BBoxes.
- Invalid optional image geometry preserves text, vector/table evidence, and later
  image occurrences with `image_placement_invalid`; occurrence limits remain fatal.
- Image provenance ordinals belong to the page-local image-paint domain and are not
  numerically comparable to text-glyph source ordinals.

## Validation evidence

- `cargo test -p pdf-core --test stage12_image_placements`: 5/5 passed.
- `cargo test -p pdf-core --test stage12_contract`: 15/15 passed.
- `cargo test -p pdf-core --test stage12_table_reconstruction`: 16/16 passed.
- `cargo test -p pdf-cli --test layout`: 4/4 passed.
- Exact CPython 3.10 wheel tests: 6/6 passed.
- `wasm-pack test --node bindings/wasm`: Stage 11/12 suite 7/7 and web suite 2/2 passed.
- `cargo fmt --all --check`: passed.
- Native workspace all-target/all-feature Clippy with `-D warnings`: passed.
- wasm32 core/WASM all-target/all-feature Clippy with `-D warnings`: passed.
- `cargo test --workspace`: passed, including doctests.

## Artifact

- CPython 3.10 wheel: 976,616 bytes; SHA-256
  `9ef3503fdd3b261cd4cd88d5d60f4f9d6067b98365cedc4b6c363a30a2a08d6b`.

Default-provider cutover remains forbidden until all global Go/No-Go gates pass.