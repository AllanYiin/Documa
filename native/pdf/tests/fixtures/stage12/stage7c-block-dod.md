# Stage 12 Stage 7.3B Block Gold Definition of Done

Status: Stage 7.3B complete; Stage 7.3C may begin; Stage 7.4 remains forbidden

## Delivered contract

- Gold and candidate fixtures use `schema_version=2`.
- Completed gold assigns every page node to exactly one non-empty block.
- Block roles are `main_flow`, `artifact`, `page_header`, `page_footer`,
  or `page_number`; `internal_order` is explicitly `unspecified`.
- Block precedence is main-flow-only, unique, acyclic, and complete over pages
  containing more than one main-flow block.
- Reviewer agreement is block-ID-independent and compares canonical partition,
  precedence, and artifact roles.
- Schema v1 is rejected as superseded. The old click-per-node packet remains
  historical evidence and cannot be used for real human review.

## Scoring evidence

The public gold contains 3 pages, 17 nodes, 8 main-flow block precedence pairs,
and multi-node blocks whose internal candidate order is intentionally reversed.

| Candidate | Block-pair macro | Cross-node diagnostic | Main-flow F1 | Artifact role | Status |
|---|---:|---:|---:|---:|---|
| perfect | 1.0 | 15/15 = 1.0 | 1.0 | 1.0 | PASS |
| inverted | 0.25 | 4/15 = 0.266667 | 0.888889 | 0.0 | FAIL |

This proves internal node order does not affect a correct block result and that
large blocks do not receive extra macro weight.

- Perfect report SHA-256:
  `918006db44dc4b4149182ba732edab7fb5e63b8a0373e93c7ada2ebe50da22b0`
- Inverted report SHA-256:
  `b942589c9381f2ac1d203f7bb2a78a2146a76ff8d98745fc28f24c893cb5756e`
- Unconfigured private template reports
  `BLOCKED: human_order_gold_unconfigured`; report SHA-256:
  `38cab2bb8e85fe26ae87dec6e004de8944378c4da4cca2bff2dcd37595ef3216`

## Invalid and privacy coverage

Self-test rejects duplicate page node IDs, duplicate membership, empty blocks,
missing coverage, artifact precedence, cycles, reviewer disagreement without a
reason code, privacy-forbidden content, and schema v1. Candidate order must cover
exactly the gold page nodes and candidate page coverage must match gold.

Reports contain no extracted content or source path and keep
`default_provider_cutover_allowed=false`. Private manifests, page images,
workspaces, and labels remain outside the repository.

## Gate decision

Stage 7.3B is accepted after self-test, exact public reports, Ruff check/format,
focused Stage 12 contract, formatting, workspace all-target/all-feature Clippy
with warnings denied, full workspace all-feature tests, and doctests pass.
Stage 7.3C may implement the blind brush workbench against this frozen schema.
Stage 7.4 remains forbidden until two real independent reviews and adjudication
produce a complete validated private manifest.
