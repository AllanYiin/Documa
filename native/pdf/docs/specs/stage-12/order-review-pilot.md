# Stage 12 Stage 7.3D Timed Blind Pilot

Status: implementation contract; real two-reviewer pilot pending

## Objective

Prove that two independent humans can complete the schema-v2 blind brush packet,
measure labor and correction burden, adjudicate disagreements, and produce a manifest
accepted by the Python authority. This stage measures the review process; it does not
modify Rust reading order or treat synthetic QA as human evidence.

## Inputs and ownership

- `manifest`: the final schema-v2 adjudicated manifest. It owns human block truth.
- `pilot_log`: a separate schema-v1 operational log. It owns durations and action counts.
- Reviewer A and Reviewer B work from independent packet copies and do not compare
  labels before both reviewer-only exports exist.
- The adjudicator starts only after both reviewer exports pass schema and identity checks.

The pilot log must never contain document text, PDF filenames, absolute paths, URLs,
email addresses, reviewer names, screen recordings, pointer traces, or raster masks.
Reviewer identities are limited to the manifest IDs (`reviewer-a`, `reviewer-b`).

## Pilot log schema

```json
{
  "schema_version": 1,
  "status": "complete",
  "packet_index_sha256": "<64 lowercase hex>",
  "sessions": [
    {
      "reviewer_id": "reviewer-a",
      "active_seconds": 0.0,
      "pages_completed": 28,
      "brush_transactions": 0,
      "correction_transactions": 0,
      "undo_transactions": 0,
      "export_attempts": 1,
      "validation_errors": 0
    }
  ],
  "adjudication": {
    "active_seconds": 0.0,
    "pages_reviewed": 28,
    "disagreement_pages": 0,
    "reason_counts": {},
    "validation_errors": 0
  }
}
```

A distributable template uses `status=unconfigured`, zero sessions, and an empty
adjudication object. It must validate as `BLOCKED`, never READY.

## Measurement procedure

1. Start a monotonic stopwatch when the reviewer begins page work. Pause for interruptions.
2. Record active time only; do not derive duration from wall-clock timestamps.
3. Count each committed brush block as one brush transaction. Count erase, split, merge,
   reorder, role correction, and unassign as correction transactions. Record undo separately.
4. Stop after reviewer-only export succeeds. Record export attempts and authority validation errors.
5. Repeat independently for Reviewer B.
6. Adjudication time begins after both imports pass and ends after final export validates.
7. Copy only aggregate counters into the pilot log; keep private manifests under `target/`.

## Validator and report

`tools/stage12_order_pilot.py` must:

- call the schema-v2 manifest authority and require READY for a completed pilot;
- require exactly two distinct sessions matching reviewer IDs present on every page;
- require every session and adjudication to cover the manifest page count;
- recompute disagreement pages and adjudication reason counts from the manifest;
- reject negative, non-finite, boolean-as-integer, excessive, or inconsistent values;
- emit only aggregate time, pages, transaction rates, agreement counts, reason counts,
  stable status/reason, and input SHA-256 hashes;
- return `BLOCKED` for an unconfigured log or incomplete human manifest.

## Acceptance and stage gate

Engineering acceptance: public exact self-test, malformed log tests, privacy audit, Ruff,
Stage 12 contract, formatting, Clippy, workspace tests, and doctests pass.

Human pilot acceptance requires all of the following:

- two real, independent reviewer sessions complete every selected page;
- final manifest authority status is READY;
- measured active time and transaction counts exist for both reviewers and adjudication;
- computed disagreement pages and reason counts exactly match the manifest;
- export/validation errors are recorded honestly; no data loss or unrecoverable UI failure occurred.

Seconds per page, correction transactions per page, undo transactions per page, exact
page agreement, and adjudication seconds per disagreement page are reported as diagnostic
baselines. No arbitrary speed threshold is invented before the first real pilot. Stage 7.4
remains forbidden until the real pilot report is READY.
