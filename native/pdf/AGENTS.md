# Project Agent Rules

## Scope

- Implement a read-only PDF parser in Rust without depending on any existing
  PDF-aware parser.
- The primary product goal is reliable text extraction.
- The deliverables are a Rust library, CLI, Python package, and browser WASM
  package backed by one shared parser core.

## Architecture invariants

- `crates/pdf-core` owns every PDF-aware rule.
- Bindings convert types and errors only; they must not parse PDF syntax.
- `pdf-core` must keep `#![forbid(unsafe_code)]`.
- Do not add `lopdf`, `pdf-rs`, PDFium, MuPDF, Poppler, or wrappers around them.
- Generic compression, Unicode, font, image, serialization, and binding
  libraries are allowed when they contain no PDF object/xref/page logic.
- Public errors must carry a stable machine-readable code.
- All input-derived lengths, offsets, recursion, object counts, and decoded
  output must be bounded.

## Stage gate

Before moving to the next stage:

1. Run the stage-specific tests.
2. Run `cargo fmt --check`.
3. Run Clippy with warnings denied.
4. Confirm the stage Definition of Done with current command output.
5. Fix incomplete work before proceeding.
