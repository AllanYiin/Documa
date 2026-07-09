# answer_verification

Answer-support verification examples built on the
`documa.interfaces.answer_support` contract (added in R-Stage 6).

Layering:

1. `documa_verify_citations` (core, deterministic) — cited block ids exist and
   carry page references.
2. `build_evidence_bundle()` (core, deterministic) — assemble excerpts +
   citations for the cited blocks.
3. `AnswerSupportChecker` implementations (this directory, optional) — split
   the answer into claims and verify each against the evidence, e.g. with an
   LLM. Streaming output; never part of core or CI.
