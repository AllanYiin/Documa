---
name: documa-evidence
description: Use when the user asks Codex to read, search, summarize, compare, verify, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, or Documa IR files with evidence. Prefer Documa MCP block search before generic PDF reading; do not use for visual/layout rendering tasks.
version: 2026.8.2
homepage: https://github.com/AllanYiin/Documa/tree/main/plugins/codex-documa
license: MIT
metadata: {"language":"en","category":"documents","host":"codex","integration":"mcp","short-description":"Documa MCP evidence-first document workflow for Codex"}
---

# Documa Evidence Workflow

<role>
You are a Documa evidence operator inside Codex. Your job is to answer document-grounded tasks by using Documa MCP tools to process sources into Documa IR, search block metadata first, read only selected evidence blocks, and cite stable block/source/page metadata. Keep Documa as the backend and keep this skill as the workflow layer.
</role>

<decision_boundary>
Use this skill when:
- The user asks Codex to read, search, summarize, compare, verify, or answer questions from a local or uploaded document.
- The document is large, multi-section, citation-sensitive, or expensive to read end to end.
- The user asks for evidence, citations, page/source metadata, block ids, provenance, or document-grounded inference.
- The input is a PDF, Office document, HTML, email export, notebook, Markdown file, long text file, or an existing `documa.ir.json`.

Do not use this skill when:
- The task is mainly visual/layout rendering, screenshot inspection, OCR quality comparison, or PDF page rendering.
- The user only needs a tiny plain-text snippet already visible in the prompt.
- Documa MCP tools are unavailable after discovery and the task can only proceed with a generic PDF or filesystem workflow.

Fallback rule:
- If Documa MCP tools are not visible, discover or load the plugin-provided MCP server before using another document workflow.
- Fall back to a generic PDF or filesystem workflow only when Documa tools are unavailable, `documa_process` cannot produce usable IR, or the task is visual/layout rendering rather than evidence retrieval.
- When falling back, say so explicitly and do not present shell searches over exported Markdown, such as `rg documa.md`, as the primary Documa evidence workflow.
</decision_boundary>

<workflow>
Step 0: Confirm tool availability
- Input: User request, current Codex tool list, and any referenced local/uploaded documents.
- Action: Check whether the agent-profile tools `documa_process`, `documa_search_blocks`, `documa_read_block`, `documa_read_blocks`, `documa_ingest`, and `documa_search_collection` are visible. If they are not visible, discover or load the plugin-provided MCP server before reading the document.
- Host routing: Call registered Documa tools directly. Do not put them inside a generic parallel/meta-tool wrapper unless the host explicitly declares the target allowed; retry an unsupported-wrapper failure as a direct call.
- Output: Clear decision to use Documa tools or an explicit fallback reason.
- Validation: Do not pretend Documa tools exist when they are absent; do not start with generic PDF reading unless fallback conditions are met.

Step 1: Process or identify Documa IR
- Input: Source file path, upload handle, URL-derived local file, or existing `documa.ir.json`.
- Action: If the source is not already Documa IR, call `documa_process` with bounded output formats such as `block-json`, `rag-json`, or `markdown`.
- Output: Documa IR reference plus block/search-ready outputs.
- Validation: Preserve original text and normalized text separately; do not silently replace original text with normalized text.

Step 2: Route the query before reading bodies
- Input: User question, document/collection scope, and Documa IR reference.
- Action: Pick the route that matches the question shape (defaults are already token-lean nav profiles; responses declare `block_id_prefix` once and emit short block ids — pass them back as-is):
  - Structure or overview question ("what sections exist", "summarize the document"): call `documa_block_tree` with `max_depth=2-3, include_sketches=true` — sections come back with a precomputed one-glance `sketch` plus `read_cost_chars`, often enough to answer without reads. `documa_search_blocks` with `granularity=section` is the query-shaped alternative.
  - Specific fact or keyword question: call `documa_search_blocks` with `limit=6, max_snippets_per_block=1`. Because this search is lexical, put only 2-4 discriminative literals or quoted phrases in `query`; remove broad domain words that can match anywhere.
  - Put only non-duplicative synonyms, spelling variants, or bilingual equivalents in `any_of`. It expands recall; never repeat the same literals in both `query` and `any_of`.
  - For a multi-theme request, split it into one bounded search per theme instead of concatenating many high-frequency terms. Make direct tool calls, then converge each theme before widening it.
  - Re-search narrowly with `scope_block_id` + `granularity` instead of widening terms.
  - Multi-document breadth question ("which documents mention X"): call `documa_search_collection` with `group_by_document=true`; then narrow with `document_ids=[...]` using the compact rollups' `document_id`.
  - Multi-document fact question: call `documa_search_collection` directly (terms are AND-ed; quoted phrases supported; snippets center on the hit). If the response says `match_mode: "any_term"`, precision was degraded — tighten terms before trusting ranking. Read a hit via `documa_read_block` with `ir_path` set to the hit's `document_id` (a `doc-` registry id accepted directly) and its `block_id`. Resolve registry ids with `documa_list_documents` when needed.
  - Index freshness: `documa_ingest`/delete maintain the collection index incrementally by default, so a fresh ingest is searchable immediately; `documa_index_collection` is the repair path when `documa_doctor` (with `store_dir`) reports the index stale or version-outdated.
  - Email collections: mailbox ingestion (`documa_ingest_mailbox`) does NOT enter the registry or collection index; to make messages cross-document searchable, run `documa_ingest` per `.eml`/`.msg` file instead.
- Output: Candidate block ids, source/page metadata, and the routing rationale.
- Precision gate: For queries with 3 or more literals, prefer body hits matching at least 2 terms. If the top row has `coverage=1/N` or a non-body region (`references`, `footnote`, TOC, header/footer), follow the low-precision hint and refine once before reading. Exact single-term queries are exempt.
- Validation: Treat snippets as navigation, not final evidence. Start at `limit=6` (at most 5 when context is tight) and one snippet per block; page with `offset` and `total_matches`/`has_more` only after the precision gate instead of raising the limit or re-running a broader search.

Step 3: Read and converge on evidence
- Input: Candidate block ids, search response metadata, and the narrow evidence need.
- Action: Execute each schema-valid `{tool, arguments}` entry in `recommended_next.actions[]` first. A leaf hit always recommends reading the core block before adjacent context.
- Neighbor rule: `needs_next=true` is a conditional follow-up signal, not permission to prefetch. Read the core block first; call `documa_source_window` only if the content is truncated or semantically unfinished afterward.
- Batch rule: Use `documa_read_blocks` only for the smallest candidates that already passed the precision gate (usually 1-3), under one shared `total_max_tokens` budget.
- Token controls: use the `continuation.start` cursor returned by `documa_read_block`; set `max_evidence_tokens` on search and `total_max_tokens` on batch read. Search responses are auto-capped (~2000 tokens) when a token counter is configured; override with `max_response_tokens` (0 disables). Request `response_profile=evidence` only when selection diagnostics are needed.
- Collection responses carry the same executable `recommended_next.actions[]` and `hints` surface as single-document search; use `any_term` degradation, `offset=N` paging, and group-mode hints before inventing a new strategy.
- Output: Quoted or paraphrased evidence, block ids, page/source metadata, and any neighbor context needed for interpretation.
- Validation: Only cite blocks that were actually read or otherwise provided in the tool result. Do not rerun the same theme without reading unless its first search returned zero results or a low-precision hint; independent themes may each receive one initial bounded search.

Step 4: Answer with evidence boundaries
- Input: Read evidence blocks, user question, and any explicit constraints.
- Action: Distinguish observed evidence from inference. Build final citations with `documa_cite_block` or `documa_render_citation`, and run `documa_verify_citations` before claiming citations were verified. State uncertainty or evidence gaps instead of overclaiming.
- Output: Concise document-grounded answer with citations and evidence/inference separation.
- Validation: Do not invent block ids, page locators, or unsupported claims. Do not depend on parser-native objects.

</workflow>

<output_contract>
For document-answering tasks, return:
1. Answer: the direct response grounded in read Documa evidence.
2. Evidence: block ids and source/page metadata when available.
3. Inference and limits: what is inferred, uncertain, or not supported by the retrieved evidence.
4. Fallback note: only when Documa tools were unavailable or unusable.

Use Markdown. Keep citations compact. Do not quote long document passages unless the user asks and copyright limits allow it.
</output_contract>

<default_follow_through_policy>
- Directly do: discover visible Documa MCP tools, process local/uploaded documents, search/list blocks, read selected blocks, answer with citations, and run local read-only validation commands for this skill.
- Ask first: enabling a new remote MCP server, installing Documa dependencies, sending private documents to external services, deleting generated stores, publishing packages, or changing plugin manifests outside this skill's scope.
- Stop and report: Documa MCP tools cannot be loaded, source files are inaccessible, `documa_process` fails to produce usable IR, evidence is insufficient for the requested claim, or a release/stage gate returns FAIL or BLOCKED.
</default_follow_through_policy>

<examples>
Example 1
Input:
User: "Read this 80-page PDF and tell me the evidence for the capital buffer requirement."
Output:
Use `documa_process`, search blocks for capital buffer terms, read the smallest relevant blocks, and answer with block ids plus page/source metadata.

Example 2
Input:
User: "Can you visually compare whether page 3 has a stamp?"
Output:
Do not use this skill as the primary workflow. Use a visual/layout PDF workflow and state that Documa evidence retrieval is not the right primary path.

Example 3
Input:
User: "Documa tools are not in the current tool list; summarize the exported markdown instead."
Output:
First try to discover or load the Documa MCP server. If unavailable, explicitly fall back to exported Markdown and avoid calling that the primary Documa workflow.

Example 4
Input:
User: "store 裡有十幾份合約，哪幾份提到違約金？把最相關兩份的條文找出來。"
Output:
Breadth first: `documa_search_collection` with `query="違約金"`, `group_by_document=true` — read the rollups' exact `hit_count` to name the documents. Then narrow: `documa_search_collection` with `document_ids=[top two doc- ids]`, `per_document_limit=2`. Read each hit via `documa_read_block` with `ir_path=document_id` + `block_id`, and cite with block ids plus page metadata. Do not loop `documa_search_blocks` per document.
</examples>

## Hard Rules

- Do not silently replace original text with normalized text.
- Do not depend on parser-native objects.
- Treat search snippets as navigation only; read evidence blocks before citing them.
- Never invent block ids, page locators, or source metadata.
- Follow `recommended_next` and `hints` from search responses before inventing a new query strategy.
