---
name: documa-evidence
description: Use Documa MCP tools for evidence-first document understanding. Use before generic PDF reading when a task asks Claude Code to read, search, summarize, compare, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, Documa IR files, or a multi-document store.
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents. For large PDFs, long files, or multi-document stores, prefer this workflow over generic PDF skills: Documa searches block metadata first and reads only selected evidence. Defaults are already token-lean (nav profiles, lean outlines, an automatic response-token ceiling); you only tune parameters when escalating.

Trigger and fallback rules:

- Use Documa first for large PDFs, long attachments, multi-section reports, contracts, papers, manuals, document sets, or any task where reading whole files would be wasteful — and whenever the user asks for evidence, citations, page/source metadata, comparison, summarization, or QA over documents.
- If Documa MCP tools are not visible, discover or enable the plugin-provided MCP server before using another PDF workflow.
- Call registered Documa tools directly. Do not route them through a generic parallel/meta-tool wrapper unless the host explicitly declares the target allowed; retry unsupported-wrapper failures as direct calls.
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

## Reading responses

- Responses declare `block_id_prefix` once and emit short block ids (e.g. `p12_para3`). Pass short ids back to any tool as-is; never reconstruct long ids by hand.
- `page` is the citation label (e.g. `PDF p.59 (printed p.54)`); `page_refs` are physical 1-based page numbers (`page_ref_kind` in the envelope). Empty/null fields are omitted entirely — absent means empty.
- Search responses are auto-capped (~2000 tokens) when a token counter is configured; `budget.dropped_results` appears only when the cap bit. Override with `max_response_tokens` (0 disables).

## Route by question shape

| Question shape | Route |
| --- | --- |
| Structure / outline for one document | `documa_block_tree` with `max_depth=2-3, include_sketches=true` — sections come back with a precomputed one-glance `sketch` and `read_cost_chars`. Descend with `documa_list_blocks` `parent_id` + `limit`/`offset`. |
| Source-preserving summary for one document or subtree | `documa_summarize` — local Rust LingXi selects exact source clauses and returns block/page refs without invoking an LLM. Treat `top_k` as a soft limit; on a provider error, fall back explicitly to tree/search/read. |
| Specific fact in one document | Start `documa_search_blocks` with `limit=6, max_snippets_per_block=1`. Use only 2-4 discriminative lexical literals or quoted phrases in `query`; put non-duplicative synonyms/spelling variants in `any_of`. For 3+ literals, refine a top `coverage=1/N` or non-body hit before reading. Re-search under `scope_block_id` instead of widening terms. |
| Breadth across documents ("which documents mention X") | `documa_search_collection` with `group_by_document=true` — each rollup has an exact `hit_count`, best snippet, and read-ready `top_refs`. |
| Specific fact across documents | `documa_search_collection` flat — rows carry `(document_id, block_id)`; read via `documa_read_block` with `ir_path=document_id`. Narrow follow-ups with `document_ids=[...]` + `per_document_limit`. |

Single documents enter via `documa_process` (produces IR + blocks). Document sets enter via `documa_ingest` per file — it returns a stable `doc-` registry id and maintains the collection index incrementally. `documa_index_collection` is the repair path when `documa_doctor` (with `store_dir`) reports the index stale or version-outdated. Mailbox ingestion does not enter the collection; `documa_ingest` each `.eml`/`.msg` to make email searchable cross-document.

## Converge on evidence

1. Execute `recommended_next.actions[]` first — each item is a schema-valid `{tool, arguments}` call with the required ids already filled in.
2. Read the core hit before any neighbor even when `needs_next=true`; that flag is a conditional follow-up signal. Only if the core content is truncated or semantically unfinished, use `documa_source_window`, then `documa_block_xref` or section browsing.
3. Batch-read only the smallest candidates that passed the precision gate (usually 1-3) under one shared budget. For independent themes, allow one bounded initial search each; do not rerun the same theme without a read unless it returned zero or low precision.
4. Interpret response signals instead of guessing: `match_mode: "any_term"` means precision degraded — tighten terms or quote phrases; `total_matches`/`has_more` mean page with `offset`, never widen `limit` blindly; a hint suggesting `group_by_document` means the flat list is the wrong shape.

## Token budget knobs (escalation only — defaults need no tuning)

- `documa_read_block`: `max_chars` or `max_tokens` (the tighter wins); start from the hit's `read_chars`.
- `documa_read_blocks`: batch candidates under one shared `total_max_tokens`; continue truncated reads via the `continuation.start` cursor.
- `documa_search_blocks`: `max_evidence_tokens` caps the selected evidence set; `include_snippets=false` when only block ids are needed; `response_profile=evidence` only when selection diagnostics are needed.
- Token budgets need a configured counter (tiktoken auto-detected, or `DOCUMA_TOKEN_COUNTER=anthropic:<model>`). On `TOKEN_COUNTER_UNAVAILABLE`, fall back to `max_chars`.

## Anti-patterns

- Re-running the same theme without reading in between (unless the first result was zero or low precision); independent themes may each receive one bounded initial search.
- Looping `documa_search_blocks` per document when `documa_search_collection` answers the breadth question in one call.
- Treating search snippets or section sketches as final evidence — they are navigation only.

## Close out with evidence boundaries

Distinguish observed evidence from inference. Build citations with `documa_cite_block`/`documa_render_citation`, and run `documa_verify_citations` before claiming citations were verified. Never invent block ids, page locators, or source metadata. Do not silently replace original text with normalized text. Do not depend on parser-native objects.
