# Stage 12 Stage 0 Definition of Done

Audit date: 2026-07-29 (Asia/Taipei)
Status: PASS

## Frozen inputs and privacy

- The contract contains exactly 7 private local PDFs and 1,113 pages.
- Every run verifies file byte length and SHA-256 before opening a parser.
- The private PDFs are not copied into this repository.
- Default output records timings, scalar counts, hashes, coordinate anomaly
  counts, and text-quality proxies only.
- `private_ir_written` is `false`; the private output directory does not exist.
- Full IR remains opt-in through `--write-private-ir`.

## Measurement contract

- Machine: the same local Windows host for all parser paths.
- Warm-up runs: 1 per document and parser path.
- Measured runs: 3 per document and parser path.
- Paths: PyMuPDF raw dict, current Documa PyMuPDFAdapter, and Rust release CLI.
- Timing excludes canonical-result hashing.
- All 21 document/parser timing groups were deterministic across all 3 measured
  runs (21/21 groups, 63/63 hashes matched within their group).

Formal report:

```text
target/stage12-baseline/report.json
SHA-256 1d6435fe105a527f3889073bf8c7fc4dd152c92772ea68f5db38ae0912daf00c
```

## Formal performance result

| Path | Sum of document medians | Throughput |
|---|---:|---:|
| PyMuPDF raw `get_text("dict")` | 10.104 s | 110.149 pages/s |
| Rust release CLI | 21.907 s | 50.805 pages/s |
| Documa PyMuPDFAdapter | 153.156 s | 7.267 pages/s |

Rust is 6.991 times faster than the current complete Documa adapter on this
contract. Rust is currently slower than raw PyMuPDF because the workloads and
implementations differ; the raw parser comparison must remain separately named.

## Current quality baseline

The non-whitespace character F1 values versus current Documa are:

```text
0.944646, 0.981260, 0.984125, 0.998092, 0.999736, 0.982284, 0.964385
```

Only 2 of 7 documents currently meet the future cutover target of 0.995. This is
not a Stage 0 failure: Stage 0 freezes and exposes the gap. It is a hard blocker
for default-provider cutover until later implementation stages close it.

The current Documa coordinate audit found zero reversed and zero out-of-bounds
bboxes at a 0.5 pt tolerance. This does not prove coordinate-space consistency;
Stage 1A owns explicit transform and parity tests.

## Executed gate

```text
python tools\stage12_baseline.py --self-test                         PASS
cargo test -p pdf-core --test stage12_contract                      3 passed
cargo fmt --all --check                                             PASS
cargo clippy --workspace --all-targets -- -D warnings               PASS
cargo test --workspace                                              PASS
```

Workspace tests included the Stage 12 contract tests; no existing tests failed.

## Completion audit

- [x] Corpus identity and privacy policy are executable.
- [x] Speed, quality proxy, page count, and coordinate anomaly baseline exists.
- [x] Repeatability is proven across three measured runs.
- [x] Architecture ownership and cutover Go/No-Go metrics are frozen.
- [x] Coordinate-system contract is frozen before layout work.
- [x] Focused and workspace gates pass with warnings denied.

Stage 0 is complete. Stage 1A may begin; no Documa provider cutover is allowed.
