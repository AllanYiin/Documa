---
name: documa-codegraph
description: Trace repository structure and impact with Documa MCP.
license: MIT
---

# Documa Code Graph Workflow

Use Documa's repository graph as navigation and the source file as evidence.

## Workflow

1. Confirm that `documa_code_context` is visible and that the repository has a synchronized workspace id. If the graph is missing, synchronize the explicit repository root through the admin tool or `documa code-graph-sync <root>`; do not scan unrelated directories.
2. Choose one intent: `lookup`, `dependencies`, `callers`, `callees`, `trace`, `impact`, `cycles`, `overview`, or `diff`.
3. Prefer an exact node id or qualified symbol in `symbols`. Use `targets` for trace endpoints. Keep `max_hops=2` unless the requested chain genuinely requires more.
4. Call `documa_code_context`. It returns a bounded proof path and reads up to three source-hash verified evidence blocks in one call.
5. Treat `EXACT` and `RESOLVED` edges as hard navigation. Leave `include_possible=false` by default; when enabled, label `POSSIBLE` paths as candidates rather than facts.
6. Report the direct answer, the evidence locators/spans and hashes actually read, then the uncertainty receipt. Dynamic dispatch, reflection, star imports, monkey patches, and unresolved calls are explicit limits, not proof of no dependency.

## Hard Rules

- Graph paths are not final evidence. Base claims on returned evidence blocks.
- Never hide stale generation or source hash failures by reading an older graph.
- Do not claim Python runtime completeness; state whether a path is exact, resolved, possible, or unresolved.
- Model-generated summaries, when present, are derived metadata and cannot create authoritative graph edges.
- Repository indexing is local derived state; it does not authorize publishing, deployment, or external source upload.
