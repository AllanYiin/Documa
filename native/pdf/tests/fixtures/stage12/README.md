# Stage 12 fixture policy

`baseline-contract.json` describes a private local corpus. The PDFs are not
redistributable and must never be copied into this repository or build artifacts.

The contract stores only file names, byte lengths, SHA-256 values, and page
counts. It allows reproducibility without exposing document text.

Default baseline output may contain only timings, counts, hashes, warnings,
quality proxies, and coordinate anomaly counts. It must not contain extracted
text, raw blocks, images, or binary assets.

Full IR output requires the explicit `--write-private-ir` option and remains
local under `target/stage12-baseline/private-ir`.

Synthetic and freely redistributable regressions belong in their owning stage
suite. Stage 1A uses generated PDFs in `stage12_geometry.rs` and `geometry.rs`,
so no private or binary fixture is required. Completion evidence is recorded in
`stage0-dod.md`, `stage1a-dod.md`, `stage1b-dod.md`, `stage2-dod.md`, and `stage3-dod.md`. Stage 1B uses the
same frozen corpus through `tools/stage12_layout_benchmark.py`; its report stores
only hashes, counts, timings, sizes, and audit booleans. Stage 2 reuses the same
contract through `tools/stage12_tagged_benchmark.py`; it additionally records
only aggregate tagged-page, MCID, artifact, role, and warning-code counts. It
never writes visible text, Alt, ActualText, tags, or private Layout IR. Stage 3
uses `tools/stage12_reading_order_benchmark.py` and additionally stores only
order-pair totals, aggregate scores, main-flow coverage, role counts, and a
multi-column proxy count. Stage 4 uses `tools/stage12_table_benchmark.py` and
stores aggregate table topology, timing, memory, size, and warning counts only.
Stage 5 uses `tools/stage12_image_navigation_benchmark.py` and adds only aggregate
image occurrence/object, Figure/Caption, Link/destination/outline target-kind,
timing, memory, size, warning, and hash data. It never stores text, Alt text, URLs,
image bytes, semantic node arrays, or private Layout IR. Completion evidence is in
`stage4-dod.md`, `stage5a-dod.md`, `stage5b-dod.md`, `stage5c-dod.md`, and
`stage5-dod.md`.

Stage 6 uses `tools/stage12_documa_shadow.py` to compare complete Documa adapter
processes. Character and bigram counters exist only in automatically removed
worker temporary directories; the report retains F1 values, hashes, counts,
timings, RSS, sizes, determinism, and anomaly totals. It never stores extracted
text, URLs, assets, full IR, or comparison counters. Stage 6A/6B evidence and the
partial Stage 6C memory result are recorded in `stage6ab-dod.md`.
Stage 6C2-A reuses the privacy-safe Stage 5 image/navigation benchmark to prove
collector parity. Its report stores only hashes, counts, timings, serialized byte
sizes, RSS, and audit booleans; it does not retain private Layout IR or extracted
content. Completion evidence is recorded in `stage6c2a-dod.md`.
Stage 6C2-B uses the same privacy-safe benchmark to prove page-scoped text/glyph
parity and memory change, plus the Documa shadow runner for an explicitly labeled
one-run interim integration measurement. Both reports retain only hashes, counts,
quality scores, timings, sizes, RSS, and audit booleans. Completion evidence is
recorded in `stage6c2b-dod.md`.
Stage 6C2-C/D use the same private-safe benchmark to prove page-indexed local
semantics and stable-ID delayed furniture patches. Completion evidence is in
`stage6c2c-dod.md` and `stage6c2d-dod.md`; neither file contains extracted text.

Stage 6C2-E evidence is recorded in `stage6c2e-dod.md`: genuine native page production, exact private parity, Python/Documa patch draining, and the then-failing complete-adapter RSS gate.

Stage 6D evidence is recorded in `stage6d-dod.md`: default compact Documa trace metadata, reversible verbose evidence, citation parity, lifecycle memory separation, and a passing three-run complete-adapter RSS gate. Default cutover remains closed on quality and labeled-gold gates.
