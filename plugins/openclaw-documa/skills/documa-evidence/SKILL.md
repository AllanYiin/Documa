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
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

Preferred sequence (single document):

1. If the source is not already a `documa.ir.json`, call `documa_process` with bounded export formats such as `block-json`, `rag-json`, or `markdown`.
2. Start with `documa_search_blocks` using 2-4 precise terms, `response_profile=nav`, and `limit<=5`. Treat search snippets as navigation only. Set `max_response_tokens` as a hard ceiling on the complete structured response when context is tight; page further matches with `offset` (the response reports `total_matches`).
3. Execute the search response's `recommended_next.actions[]` calls exactly as returned; every item contains a schema-valid `tool` and `arguments` object. Otherwise read the smallest set of block ids that can support the answer, bounding reads with `max_chars` or `max_tokens`.
4. If evidence is incomplete, refine the query once guided by the response `hints`, or read nearby parent/child blocks (`include_children=true` only when `neighbors.needs_next` is true), before expanding scope.
5. In the final answer, distinguish observed evidence from inference and cite block ids or source/page metadata when available.

Preferred sequence (multiple documents):

1. `documa_ingest` each file into the store — it returns a stable `doc-` id and keeps the collection index fresh incrementally, so ingested files are searchable immediately. `documa_index_collection` is only the repair path when the index is missing or version-outdated.
2. Breadth question ("which documents mention X"): `documa_search_collection` with `group_by_document=true` — each rollup carries an exact `hit_count`, the best snippet, and up to three read-ready `top_blocks`. Fact question: `documa_search_collection` flat.
3. Narrow follow-ups with `document_ids=[...]` (the `doc-` ids from rollups or `documa_list_documents`) plus `per_document_limit`; page with `offset`/`has_more` instead of widening `limit`.
4. Chain each hit's `read_ref` into `documa_read_block`: `read_ref.ir_path` is a `doc-` registry id the tool accepts directly.
5. Interpret response signals: `match_mode: "any_term"` means precision degraded (tighten terms or quote phrases); follow `recommended_next` and `hints` before inventing a new strategy. `max_response_tokens` bounds the response when context is tight.

Do not silently replace original text with normalized text. Do not depend on parser-native objects.

