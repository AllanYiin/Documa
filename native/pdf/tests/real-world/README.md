# Private real-world PDF corpus

## Goal

Run the Stage 11 text-extraction contract against the two supplied private PDFs
without copying either document into the repository. The core runner verifies
the corpus contract; the CLI runner verifies inspect, validate, and all three
extraction modes.

## Prerequisites

- Both private PDF files are available locally.
- Run commands from the repository root.
- The workspace Rust toolchain is installed.
- Use either the shared corpus directory variable or both per-document
overrides. Configuring only one override is an error, not a skip.

## Procedure

### Step 1: configure the files

Use one private directory containing both files:

```powershell
$env:RUST_PDF_REAL_CORPUS_DIR = '<path-to-private-corpus>'
```

For files stored in different directories, use both per-document overrides:

```powershell
$env:RUST_PDF_REAL_AI_INDEX = '<path-to-ai-index.pdf>'
$env:RUST_PDF_REAL_TAIWAN = '<path-to-taiwan-pdf>'
```

The runners calculate SHA-256 before parsing. A configured file with the wrong
digest fails. When no corpus variables are configured, a runner emits an
explicit `SKIP` message and succeeds without claiming a private-corpus pass.

### Step 2: run the core contract

```powershell
cargo test -p pdf-core --test stage11_contract private_real_world_contract_if_configured -- --exact --nocapture
```

### Step 3: run the CLI release matrix

This runner checks SHA-256, `inspect`, `validate --diagnostics`, and all three
extraction modes:

```powershell
cargo test -p pdf-cli --test stage11_private -- --nocapture
```

## Verify

A valid configured run reports one passing test from each command. It also
confirms both expected document hashes before parsing. The reviewed Stage 11.6
metrics are recorded in
[`../fixtures/stage11/validation-matrix.md`](../fixtures/stage11/validation-matrix.md).

## Troubleshooting

- `SKIP` means no private corpus was configured; it does not prove the private
  acceptance criteria.
- An incomplete-configuration error means only one per-document override was
  set. Configure both paths or clear both variables.
- A digest mismatch means the file is not the reviewed corpus version. Do not
  edit the expected hash until provenance has been verified.
- A required/forbidden fragment failure indicates a text-fidelity regression;
  reproduce it with a minimal generated fixture before changing a baseline.

## Policy

- Never copy the private PDFs into this repository.
- Never replace a changed digest without verifying the document provenance.
- Keep current behavior under `baseline_*`; Stage 11 target behavior belongs
  under `target_*` and `forbidden_*`.
- Required fragments collapse layout whitespace before matching; forbidden
  fragments match the raw Auto text so unrelated cross-line boundaries do not
  become false positives.
- Every real-world bug must also receive a minimal generated regression fixture
  that is safe to redistribute.
- `manifest.local.toml` and `.private/` are ignored for local experiments.
- Aggregated warning codes distinguish `unicode_mapping_invalid` from
  `unicode_mapping_missing`; do not update warning baselines without a minimal
  synthetic reproduction and review.