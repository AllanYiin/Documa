# Documa — project rules for Claude Code

## Long-term invariants

- **Quality metrics stay decoupled from the pipeline**: modules under
  `src/documa/quality/` must not import pipeline internals — they consume IR
  data (JSON / dataclasses) only. Evaluation code and pipeline code evolve
  independently.
- **OCR text never impersonates native text**: every OCR-derived block or
  image text must carry `metadata["origin"] = "ocr"` plus `ocr_engine` and
  `ocr_confidence`. Separation is for auditability; exporters/search/chunking
  must not special-case it into lower priority.
- **IR compatibility contract**: `ir_version` follows semver semantics. Minor
  bumps are strictly additive (old consumers ignore unknown fields); removing
  or retyping fields requires a major bump. `schema/documa.schema.json` is
  generated from `src/documa/core/ir.py` by `scripts/generate_schema.py` —
  never edit the schema file by hand.

## Dependencies

- Core runtime dependencies are exceptional: each one must be justified in a
  comment next to its entry in `pyproject.toml` (current sole entry: filelock,
  guarding registry concurrency). ML/LLM inference never enters core or CI —
  pluggable interfaces live in core, implementations live in `examples/` or
  optional extras.

## Testing

- Snapshot regression tests (`tests/test_ir_snapshot_regression.py`) guard
  pipeline output. Regenerate golden files (`pytest --force-regen`) only for
  intended output changes, and say so in the commit message.
- Run the full `pytest` suite before declaring any stage complete.
