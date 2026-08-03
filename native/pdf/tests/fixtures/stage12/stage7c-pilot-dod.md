# Stage 12 Stage 7.3D Timed Blind Pilot Tooling Definition of Done

Status: Stage 7.3D tooling complete; real two-reviewer pilot BLOCKED; Stage 7.4 forbidden

## Implemented

- `tools/stage12_order_pilot.py` validates a separate schema-v1 operational log
  against the authority-validated schema-v2 human manifest.
- The operational log records active seconds, pages completed, brush transactions,
  correction transactions, undo, export attempts, validation errors, adjudication
  duration, disagreement pages, and coded-reason counts.
- Human truth remains in the manifest. The pilot log cannot contain text, filenames,
  paths, URLs, reviewer names, pointer traces, screenshots, or raster masks.
- The validator requires exactly two distinct reviewer sessions matching reviewer IDs
  on every manifest page and recomputes disagreement pages/reason counts from gold.
- Durations must be finite, positive, and bounded; counters reject booleans, negatives,
  overflow, missing fields, extra fields, identity duplication, and page-count mismatch.
- READY reports expose privacy-safe aggregate time, seconds per page, correction/undo
  rates, exact page agreement, and adjudication seconds per disagreement page.
- No arbitrary speed threshold is invented before the first real pilot.

## Exact evidence

- Public in-memory exact self-test: READY with two distinct reviewers and matching
  adjudication metrics. Negative time, duplicate reviewer, mismatched disagreement
  count, and unsupported private-name field are rejected.
- Public unconfigured pilot template returns BLOCKED.
- Current 28-page private draft plus unconfigured pilot log returns `BLOCKED`, reason
  `human_order_pilot_unconfigured`, and `stage_7_4_gate_review_allowed=false`.
- Blocked aggregate report SHA-256:
  `a5ac0eeb099be5dfd8648a7f7350db50ed9a49f167eb8abbd2318fa644846bc6`.
- Pilot tool SHA-256:
  `d64f69a8d11630e2dee24b26eb883b985563cd9afd7c080350b86dbdb8dfd174`.
- Pilot spec SHA-256:
  `7f40a2df0a9624b41f4f468b76778edd4766822aca8446e257383b0ac3659d56`.
- Unconfigured template SHA-256:
  `26702416870edd0e68e5ec5eb557739f6d3ce0c10e0bf3a4933c8e5c3fcd7a7a`.

## Repository validation

- Packet, block-gold, and timed-pilot Python self-tests: PASS.
- Ruff check and format check for all three Stage 7.3 tools: PASS.
- Stage 12 focused contract: 33/33 PASS.
- `cargo fmt --all --check`: PASS.
- Workspace all-target/all-feature Clippy with `-D warnings`: PASS.
- Full workspace all-feature tests and doctests: PASS.

## Remaining external gate

- Two real people must independently complete all 28 pages and export reviewer-only
  manifests, followed by real adjudication and a completed pilot log.
- The final manifest must validate READY and the pilot report must validate READY.
- Synthetic QA cannot satisfy this gate. Until both real-human artifacts exist,
  Stage 7.4 is forbidden and parser reading-order rules remain unchanged.
