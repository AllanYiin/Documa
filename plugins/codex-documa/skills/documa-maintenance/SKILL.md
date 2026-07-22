---
name: documa-maintenance
description: Use when maintaining, diagnosing, benchmarking, migrating, validating, or releasing Documa itself. Do not use for ordinary document evidence questions.
version: 2026.7.22
license: MIT
metadata: {"language":"en","category":"developer-tools","host":"codex","integration":"mcp"}
---

# Documa Maintenance Workflow

Use the admin MCP profile for repository and store maintenance. Start with `documa_doctor`; use `documa_inspect_store` for registry/index state, `documa_index_collection` only as an explicit repair path, `documa_validate_ir` for schema checks, and `documa_benchmark` for release evidence.

For a release or skill review, inspect the sibling evidence skill's `references/readiness_report.md`, `references/migration-governance.md`, `assets/evals/evals.json`, and `assets/evals/regression_gates.json`. Report PASS, FAIL, or BLOCKED from the final gate: a failed stage or policy gate cannot be overridden by local passes.

Keep the retrieval sidecar disposable. Rebuild it whenever source digest, schema version, normalizer version, tokenizer version, or feature version differs. Never treat sidecar metadata as citation truth or silently copy normalized text over original text.

Return the conclusion, findings ordered by severity, exact validation commands/results, and remaining external risks.
