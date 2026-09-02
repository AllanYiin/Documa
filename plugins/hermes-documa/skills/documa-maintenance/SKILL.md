---
name: documa-maintenance
description: Maintain and validate Documa with its admin MCP tools.
version: 2026.7.23
license: MIT
---

# Documa Maintenance Workflow

Use the admin MCP profile for repository and store maintenance. The plugin's MCP server defaults to the lean agent profile (set `DOCUMA_MCP_PROFILE=admin` in the server env and restart it, or run the equivalent `documa` CLI commands directly) before expecting admin tools to be visible. Start with `documa_doctor`; use `documa_inspect_store` for registry/index state, `documa_index_collection` only as an explicit repair path, `documa_validate_ir` for schema checks, and `documa_benchmark` for release evidence.

Keep the retrieval sidecar disposable. Rebuild it whenever source digest, schema version, normalizer version, tokenizer version, or feature version differs. Never treat sidecar metadata as citation truth or silently copy normalized text over original text.

Return the conclusion, findings ordered by severity, exact validation commands/results, and remaining external risks.
