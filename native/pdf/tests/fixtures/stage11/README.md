# Stage 11 generated fixture recipes

## Overview

This directory contains the redistributable Stage 11 fixture contracts and
release evidence. The executable PDF recipes live in
`crates/pdf-core/tests/stage11_contract.rs`; they derive byte offsets in memory
instead of relying on hand-maintained xref values.

## Requirements and prerequisites

- A Rust toolchain compatible with the workspace `rust-toolchain.toml`.
- The repository root as the current working directory.
- No private PDF is required for the generated fixture tests.

## Installation

No additional fixture installation is required. Workspace dependencies are
resolved by Cargo and the generated PDF bytes are created by the tests.

## Quick start

Run the public Stage 11 contract:

```powershell
cargo test -p pdf-core --test stage11_contract -- --nocapture
```

Expected result: all generated cases pass. A private-corpus case may explicitly
report `SKIP` when its environment variables are absent; that is not evidence of
a private-corpus pass.

## Usage

Current generated recipes:

- `latin-positioned-glyphs`: each character in
  `Artificial Intelligence Index Report 2026` is emitted by an independent
  text-show operation with an explicit position.
- `cjk-positioned-glyphs`: each character in `台灣政府動畫宣導影片` is
  emitted independently and mapped through a one-byte ToUnicode CMap.
- High-ratio xref/object-stream and malformed ToUnicode cases remain covered by
  `real_world_regressions.rs` and `stage3.rs`; the contract test asserts that
  those regression tests remain present.

Stage 11 release evidence:

- [`validation-matrix.md`](validation-matrix.md) records the reproducible Stage
  11.6 acceptance, cross-interface, private-corpus, fuzz, benchmark, MSRV, and
  supply-chain evidence.
- [`final-dod.md`](final-dod.md) closes every Stage 11.7 and final DoD item
  against current commands, artifacts, and documented limitations.
- [`benchmark-baseline.toml`](benchmark-baseline.toml) records the fixed 2,000
  glyph release benchmark environment and measurements. Timing is evidence,
  not the sole performance gate.
- [`baseline-golden.toml`](baseline-golden.toml) freezes the redistributable
  Latin and CJK legacy baselines.
- [`../../../crates/pdf-core/tests/stage11_contract.rs`](../../../crates/pdf-core/tests/stage11_contract.rs)
  owns the executable generated-corpus cases, acceptance IDs, and hash checks.
- [`../../real-world/manifest.toml.example`](../../real-world/manifest.toml.example)
  defines the private-corpus schema, hashes, legacy metrics, and text fragments.

All generated contents are project-authored and may be redistributed under the
repository license. The private PDFs are never stored in this directory.