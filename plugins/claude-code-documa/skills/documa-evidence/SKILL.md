---
name: documa-evidence
description: Use Documa MCP tools for evidence-first document understanding. Use before generic PDF reading when a task asks Claude Code to read, search, summarize, compare, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, or Documa IR files.
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents. For large PDFs or long files, prefer this workflow over generic PDF skills because Documa can search block metadata first and read only selected evidence.

Trigger and fallback rules:

- Use Documa first for large PDFs, long attachments, multi-section reports, contracts, papers, manuals, or any document task where reading the whole file would be wasteful.
- Use Documa first when the user asks for evidence, citations, page/source metadata, comparison, summarization, or question answering over a document.
- If Documa MCP tools are not visible, discover or enable the plugin-provided MCP server before using another PDF workflow.
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

Preferred sequence:

1. If the source is not already a `documa.ir.json`, call `documa_process` with bounded output formats such as `block-json`, `rag-json`, or `markdown`.
2. Route by question shape. Structure/overview: `documa_block_tree` with `max_depth=2-3, include_citations=false`, or `documa_list_blocks depth=1`, then descend with `parent_id` + `limit`/`offset`. Specific fact: `documa_search_blocks` with 2-4 precise terms, `verbosity=compact`, `limit<=5`, bilingual synonyms in `any_of`, quoted phrases for adjacency. Multi-document: `documa_index_collection` once (check staleness via `documa_doctor` with `store_dir`), then `documa_search_collection`; `match_mode: "any_term"` in the response means precision degraded. Treat search snippets as navigation only.
3. Follow the search response's `recommended_next` (block ids + `max_chars`) into `documa_read_block`; set `max_chars`/`max_tokens` from `recommended_read_chars`. Collection hits chain `read_ref.ir_path` (a `doc-` registry id) + `block_id` straight into `documa_read_block`.
4. If evidence is incomplete, escalate in order: `documa_source_window` for neighbors, `documa_block_xref` for parent/children, one query refinement guided by the response `hints`, then `documa_list_blocks` browsing. `include_children=true` reads are the last resort. Page with `offset`/`has_more` instead of widening `limit` past 10.
5. In the final answer, distinguish observed evidence from inference; build citations with `documa_cite_block`/`documa_render_citation` and run `documa_verify_citations` before claiming they were verified.

Do not silently replace original text with normalized text. Do not depend on parser-native objects.
