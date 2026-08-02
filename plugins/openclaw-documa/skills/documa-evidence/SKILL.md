---
name: documa-evidence
description: Use Documa tools for evidence-first document understanding. Use before generic PDF reading when a task asks OpenClaw to read, search, summarize, compare, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, or Documa IR files.
metadata: {"openclaw":{"requires":{"bins":["documa"]}}}
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents. For large PDFs or long files, prefer this workflow over generic PDF skills because Documa can search block metadata first and read only selected evidence.

Trigger and fallback rules:

- Use Documa first for large PDFs, long attachments, multi-section reports, contracts, papers, manuals, or any document task where reading the whole file would be wasteful.
- Use Documa first when the user asks for evidence, citations, page/source metadata, comparison, summarization, or question answering over a document.
- If Documa tools are not visible, inspect or enable the Documa plugin before using another PDF workflow.
- Call registered Documa tools directly. Do not route them through a generic parallel/meta-tool wrapper unless the host explicitly declares the target allowed; retry unsupported-wrapper failures as direct calls.
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

Preferred sequence (single document):

1. If the source is not already a `documa.ir.json`, call `documa_process` with bounded export formats such as `block-json`, `rag-json`, or `markdown`.
2. For an overview, call `documa_block_tree` with `max_depth=2-3, include_sketches=true`. For facts, start `documa_search_blocks` with `limit=6, max_snippets_per_block=1`; use 2-4 discriminative lexical literals or quoted phrases in `query`, and only non-duplicative synonyms/spelling variants in `any_of`. Split multi-theme questions into one bounded search per theme instead of concatenating high-frequency terms.
3. Before reading a query with 3+ literals, prefer body hits matching at least 2 terms. Refine a top `coverage=1/N` or non-body hit once. Exact single-term queries are exempt.
4. Execute `recommended_next.actions[]` exactly as returned. Read the core hit before neighbors even when `needs_next=true`; fetch adjacent context only if the core is truncated or semantically unfinished. Batch only the smallest precision-qualified set (usually 1-3) under a shared budget.
5. If evidence is incomplete, re-search narrowly with `scope_block_id` + `granularity` or inspect parent/child relations before expanding scope. Do not rerun the same theme without reading unless it returned zero or low precision; independent themes may each receive one initial bounded search.
6. In the final answer, distinguish observed evidence from inference and cite block ids or source/page metadata when available.

Preferred sequence (multiple documents):

1. `documa_ingest` each file into the store — it returns a stable `doc-` id and keeps the collection index fresh incrementally, so ingested files are searchable immediately. `documa_index_collection` is only the repair path when the index is missing or version-outdated.
2. Breadth question ("which documents mention X"): `documa_search_collection` with `group_by_document=true` — each rollup carries an exact `hit_count`, the best snippet, and read-ready `top_refs`. Fact question: `documa_search_collection` flat.
3. Narrow follow-ups with `document_ids=[...]` (the `doc-` ids from rollups or `documa_list_documents`) plus `per_document_limit`; page with `offset`/`has_more` instead of widening `limit`.
4. Read a hit via `documa_read_block` with `ir_path` set to the hit's `document_id` (a `doc-` registry id the tool accepts directly) and its `block_id`.
5. Interpret response signals: `match_mode: "any_term"` means precision degraded (tighten terms or quote phrases); follow `recommended_next` and `hints` before inventing a new strategy. `max_response_tokens` bounds the response when context is tight.

Do not silently replace original text with normalized text. Do not depend on parser-native objects.

