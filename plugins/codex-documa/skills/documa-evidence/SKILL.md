---
name: documa-evidence
description: Use Documa MCP tools for evidence-first document understanding. Use when a task asks Codex to read, search, summarize, compare, or answer from PDF, Office, HTML, email, notebook, Markdown, or Documa IR files.
---

# Documa Evidence Workflow

Use Documa when the user asks for answers grounded in uploaded or local documents.

Preferred sequence:

1. If the source is not already a `documa.ir.json`, call `documa_process` with bounded output formats such as `block-json`, `rag-json`, or `markdown`.
2. Start with `documa_search_blocks` or `documa_list_blocks`. Treat search snippets as navigation only.
3. Call `documa_read_block` for the smallest set of block ids that can support the answer.
4. If evidence is incomplete, refine the search query or read direct parent/child blocks before expanding to the whole document.
5. In the final answer, distinguish observed evidence from inference and cite block ids or source/page metadata when available.

Do not silently replace original text with normalized text. Do not depend on parser-native objects.

