---
name: documa-evidence
description: Use Documa MCP tools for evidence-first document understanding. Use before generic PDF reading when a task asks Claude Code to read, search, summarize, compare, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, Documa IR files, or a multi-document store.
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents. For large PDFs, long files, or multi-document stores, prefer this workflow over generic PDF skills because Documa searches block metadata first and reads only selected evidence.

Trigger and fallback rules:

- Use Documa first for large PDFs, long attachments, multi-section reports, contracts, papers, manuals, document sets, or any task where reading whole files would be wasteful.
- Use Documa first when the user asks for evidence, citations, page/source metadata, comparison, summarization, or question answering over one or many documents.
- If Documa MCP tools are not visible, discover or enable the plugin-provided MCP server before using another PDF workflow.
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

## Route by question shape

| Question shape | Route |
| --- | --- |
| Structure / overview of one document ("what sections exist", "summarize") | `documa_block_tree` with `max_depth=2-3, include_citations=false`, or `documa_list_blocks depth=1`; descend with `parent_id` + `limit`/`offset` |
| Specific fact in one document | `documa_search_blocks` with 2-4 precise terms, `response_profile=nav`, `limit<=5`; bilingual synonyms in `any_of`; quote adjacent words for phrases |
| Breadth across documents ("which documents mention X") | `documa_search_collection` with `group_by_document=true` — each rollup has an exact `hit_count`, best snippet, and up to 3 read-ready `top_blocks` |
| Specific fact across documents | `documa_search_collection` flat; narrow follow-ups with `document_ids=[...]` + `per_document_limit` |

Single documents enter via `documa_process` (produces IR + blocks). Document sets enter via `documa_ingest` per file — it returns a stable `doc-` registry id and maintains the collection index incrementally, so ingested files are searchable immediately. `documa_index_collection` is the repair path when `documa_doctor` (with `store_dir`) reports the index stale or version-outdated. Mailbox ingestion does not enter the collection; `documa_ingest` each `.eml`/`.msg` to make email searchable cross-document.

## Converge on evidence

1. Execute `recommended_next.actions[]` first — each item is a schema-valid `{tool, arguments}` call and already carries the required `ir_path`/`block_id` or browse arguments. Collection hits chain their `read_ref` into the same executable action contract.
2. If evidence is incomplete, escalate in order: `documa_source_window` for neighbor context; `documa_block_xref` for parent/children/relations; refine the query once, guided by the response `hints`; browse `documa_list_blocks` under the nearest section. Whole-section reads (`include_children=true`) are the last resort, justified only when `neighbors.needs_next` is true or the hit is a section heading.
3. Interpret response signals instead of guessing: `match_mode: "any_term"` means precision degraded — tighten terms or quote phrases; `total_matches`/`has_more` mean page with `offset`, never widen `limit` blindly; a hint suggesting `group_by_document` means hits span many documents and the flat list is the wrong shape.

## Token budget knobs

- `documa_read_block`: `max_chars` or `max_tokens` (the tighter wins); start from the hit's `recommended_read_chars`.
- `documa_search_blocks` / `documa_search_collection`: `max_response_tokens` is a hard ceiling on the complete compact-serialized structured response; dropped rows are reported in `budget.dropped_results`.
- `documa_search_blocks`: `include_snippets=false` when only block ids are needed.
- Token budgets need a configured counter (tiktoken auto-detected, or `DOCUMA_TOKEN_COUNTER=anthropic:<model>` for Claude counting). On `TOKEN_COUNTER_UNAVAILABLE`, fall back to `max_chars`.

## Anti-patterns

- Two consecutive searches without reading in between (unless the first returned zero results).
- Raising `limit` past 10 before narrowing `fields`, `document_ids`, or refining terms.
- Looping `documa_search_blocks` per document when `documa_search_collection` answers the breadth question in one call.
- Treating search snippets as final evidence — they are navigation only.

## Close out with evidence boundaries

Distinguish observed evidence from inference. Build citations with `documa_cite_block`/`documa_render_citation`, and run `documa_verify_citations` before claiming citations were verified. Never invent block ids, page locators, or source metadata. Do not silently replace original text with normalized text. Do not depend on parser-native objects.
