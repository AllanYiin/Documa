# Stage 12 Stage 2 Definition of Done

Status: PASS
Date: 2026-07-29

## Scope completed

- Marked-content tags, non-negative MCIDs, Alt, ActualText metadata, and nested
  Artifact state are collected internally without changing legacy text DTOs.
- `StructTreeRoot`, `StructElem` K/Pg, MCR dictionaries, RoleMap chains, and
  ParentTree Nums/Kids are traversed in declared order within explicit limits.
- Layout IR schema remains version 1. Additive node/span/glyph metadata is omitted
  when empty, so untagged Stage 1B serialization remains byte-compatible.
- Tagged order is structure-derived and independent from complete source order.
  Alt remains metadata, structure ActualText does not duplicate visible text,
  and Artifact content remains present and classified.
- Malformed optional structure preserves source text and emits aggregated stable
  warnings. Limit breaches remain fatal `limit_exceeded` errors.
- CLI, Python, and browser WASM expose the same core-owned schema; bindings do not
  parse PDF syntax.

## Complexity and resource audit

Structure recursion and ParentTree number-tree recursion both check
`max_object_depth`. Elements, K work, ParentTree entries, RoleMap entries, and
associations are bounded. Tagged joins use a per-page MCID-to-node index, and
ParentTree arrays resolve once per page. This removes the two quadratic paths
found during the first performance audit. `pdf-core` still forbids unsafe code
and adds no PDF-aware dependency.

## Frozen private-corpus result

The same SHA-verified 7 documents / 1,113 pages from Stage 0 were run with one
warm-up and three measured release-CLI runs per document. Timing includes process
startup, parse, Layout IR construction, serialization, and stdout capture; JSON
decode, audit, hashing, and report write are excluded.

| Metric | Result |
|---|---:|
| sum of document medians | 5.093775 s |
| Stage 2 throughput | 218.501999 pages/s |
| speedup vs frozen Stage 0 Documa | 30.067210x |
| Stage 1B throughput retained | 79.6424% |
| Stage 2 overhead vs Stage 1B | 20.3576% |
| serialized bytes, one run/document | 289,055,422 |
| increase vs Stage 1B | 17,173,071 bytes (6.3164%) |
| tagged pages | 423 |
| tagged nodes | 15,360 |
| associated MCIDs | 15,146 |
| marked nodes | 16,424 |
| artifacts | 423 |
| deterministic groups | 7/7 |
| schema audits | 7/7 |
| privacy audits | 7/7 |

All six untagged documents have exactly the same serialized byte length and
SHA-256 as the frozen Stage 1B report. The entire 17,173,071-byte increase is in
the tagged AI Index document, where Stage 2 metadata is actually present.

Aggregate roles are 423 artifact, 346 figure, 1,832 paragraph, and 14,513
unclassified nodes. Stable tagged diagnostics include 207 ambiguous MCID, 164
missing MCID, and 62 deferred object-reference warnings. These defects preserve
visible source text and are exposed for later Stage 3/5 handling rather than
silently inventing order.

Formal report:

```text
target/stage12-tagged-benchmark/report.json
bytes: 15,014
sha256: 919b5be5995433aa9b3e970303255ebec9fdacad1278e459c8e3718070f41ff9
contains_extracted_content: false
private_ir_written: false
```

The report contains only contract metadata, counts, hashes, timings, warning
codes, and booleans. It stores no visible text, Alt, ActualText, private tag name,
or private Layout IR.

## Front-end and package evidence

| Deliverable | Evidence |
|---|---|
| Rust core | 13 focused tagged-structure tests pass |
| CLI | 2 Layout IR tests pass, including tagged schema |
| Python wheel | 830,450 bytes; SHA-256 `c0018ba845cbb46eaf01a98d7cdff51f30535cd2f351af2c81f012230ae35fd3` |
| Python isolated wheel tests | 7 passed in 0.01 s from unpacked final wheel |
| WASM web package | `extractLayout` present and generated module initializes in Node |
| WASM binary | 1,110,988 bytes; SHA-256 `20f30b9ece7f7dc28886c949743d85feabad7d5d5dc53634dbe433b897f8d1c3` |
| Node wasm-bindgen tests | 7 passed, including tagged schema |

## Executed gate

```text
python -m py_compile tools\stage12_tagged_benchmark.py                 PASS
python tools\stage12_tagged_benchmark.py --self-test                  PASS
cargo test -p pdf-core --test stage12_tagged_structure --all-features 13 passed
cargo test -p pdf-core --test stage12_contract                         PASS
cargo test -p pdf-cli --test layout                                    2 passed
cargo fmt --all --check                                                 PASS
cargo clippy --workspace --all-targets -- -D warnings                   PASS
cargo test --workspace                                                  PASS
final unpacked Python wheel tests                                       7 passed
wasm-pack test --node bindings\wasm                                    7 passed
web-target generated package init                                      PASS
```

## Completion audit

- [x] Marked metadata and legacy DTO separation are implemented and tested.
- [x] Structure tree, RoleMap, ParentTree, MCR, cycles, and exact limits are bounded.
- [x] Source, tagged, inferred, and main-flow orders remain explicit and non-aliasing.
- [x] Tagged roles, provenance, accessible metadata, and artifacts are deterministic.
- [x] Missing, ambiguous, cyclic, malformed, and deferred paths preserve source text.
- [x] Rust, CLI, Python, and WASM expose the additive schema consistently.
- [x] The frozen corpus is deterministic and passes schema/privacy audits.
- [x] Focused, formatting, denied-warning Clippy, workspace, and package gates pass.

Stage 2 is complete. Stage 3 may begin human reading-order inference, paragraph
grouping, and repeated page-furniture classification. Default-provider cutover
remains forbidden until the later reading-order, table, Documa integration,
quality, rollout, and rollback gates pass.