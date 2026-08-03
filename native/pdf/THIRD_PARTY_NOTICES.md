# Third-party notices

This project contains no third-party PDF parser code. All PDF object, xref, page, font, content,
text, and image-XObject rules are implemented in `crates/pdf-core`.

Direct runtime dependencies are generic libraries:

- `flate2` / `miniz_oxide`: DEFLATE implementation
- `encoding_rs`: legacy character encodings
- `unicode-normalization`: Unicode normalization
- `image` with JPEG-only features: JPEG header/codec validation
- `serde`, `serde_json`, `thiserror`: data and error plumbing
- `clap`: CLI argument parsing
- `pyo3`: Python binding
- `wasm-bindgen`, `serde-wasm-bindgen`: browser WASM binding

The resolved dependency set and exact versions are recorded in `Cargo.lock`. The release license
gate is:

```text
cargo deny check licenses sources bans advisories
```

At the Stage 10 audit this gate passed. Allowed licenses are defined in `deny.toml`; unmatched
allowances and duplicate transitive versions are warnings to review, not permission to bypass a
denied license.
