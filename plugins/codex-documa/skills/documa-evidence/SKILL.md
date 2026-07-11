---
name: documa-evidence
description: Use Documa MCP tools for evidence-first document understanding. Use before generic PDF reading when a task asks Codex to read, search, summarize, compare, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, or Documa IR files.
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents. For large PDFs or long files, prefer this workflow over generic PDF skills because Documa can search block metadata first and read only selected evidence.

Trigger and fallback rules:

- Use Documa first for large PDFs, long attachments, multi-section reports, contracts, papers, manuals, or any document task where reading the whole file would be wasteful.
- Use Documa first when the user asks for evidence, citations, page/source metadata, comparison, summarization, or question answering over a document.
- If Documa MCP tools are not visible, discover or load the plugin-provided MCP tools before using another PDF workflow.
- Fall back to a generic PDF skill only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval. Say that fallback explicitly.

Preferred sequence:

1. If the source is not already a `documa.ir.json`, call `documa_process` with bounded output formats such as `block-json`, `rag-json`, or `markdown`.
2. Start with `documa_search_blocks` or `documa_list_blocks`. Treat search snippets as navigation only.
3. Call `documa_read_block` for the smallest set of block ids that can support the answer.
4. If evidence is incomplete, refine the search query or read direct parent/child blocks before expanding to the whole document.
5. In the final answer, distinguish observed evidence from inference and cite block ids or source/page metadata when available.

If Documa tools are not visible in the current Codex tool list, discover/load the Documa MCP tools before reading the document. Do not treat shell searches over exported Markdown, such as `rg documa.md`, as the primary Documa evidence workflow; use them only as a fallback when MCP tools are unavailable and say so explicitly.

Do not silently replace original text with normalized text. Do not depend on parser-native objects.
