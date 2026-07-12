---
name: documa-evidence
description: Use when the user asks Codex to read, search, summarize, compare, verify, or answer from large PDFs, long documents, Office, HTML, email, notebook, Markdown, or Documa IR files with evidence. Prefer Documa MCP block search before generic PDF reading; do not use for visual/layout rendering tasks.
version: 2026.7.13
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
- Action: Check whether plugin-provided Documa MCP tools such as `documa_process`, `documa_search_blocks`, `documa_list_blocks`, `documa_read_block`, and `documa_doctor` are visible. If they are not visible, discover or load the plugin-provided MCP server before reading the document.
- Output: Clear decision to use Documa tools or an explicit fallback reason.
- Validation: Do not pretend Documa tools exist when they are absent; do not start with generic PDF reading unless fallback conditions are met.

Step 1: Process or identify Documa IR
- Input: Source file path, upload handle, URL-derived local file, or existing `documa.ir.json`.
- Action: If the source is not already Documa IR, call `documa_process` with bounded output formats such as `block-json`, `rag-json`, or `markdown`.
- Output: Documa IR reference plus block/search-ready outputs.
- Validation: Preserve original text and normalized text separately; do not silently replace original text with normalized text.

Step 2: Route the query before reading bodies
- Input: User question, document/collection scope, and Documa IR reference.
- Action: Pick the route that matches the question shape:
  - Structure or overview question ("what sections exist", "summarize the document"): call `documa_block_tree` with `max_depth=2-3` and `include_citations=false` for a cheap outline, or `documa_list_blocks` with `depth=1`; descend later with `parent_id` plus `limit`/`offset`.
  - Specific fact or keyword question: call `documa_search_blocks` with 2-4 precise terms, `verbosity=compact`, and `limit<=5`. Put bilingual synonyms in `any_of` when the question language may differ from the document language. Wrap adjacent words in double quotes for phrase intent.
  - Multi-document question: check index freshness via `documa_doctor` with `store_dir`, rebuild once with `documa_index_collection` if stale, then call `documa_search_collection` (terms are AND-ed; quoted phrases supported). If the response says `match_mode: "any_term"`, precision was degraded — tighten terms before trusting ranking. Chain each hit's `read_ref` into `documa_read_block`: `read_ref.ir_path` is a `doc-` registry id that `documa_read_block` accepts directly. Resolve registry ids with `documa_list_documents` when needed.
- Output: Candidate block ids, source/page metadata, and the routing rationale.
- Validation: Treat snippets as navigation, not final evidence. Do not raise `limit` past 10 before narrowing `fields` or refining terms; page with `offset` and `total_matches`/`has_more` instead of re-running broader searches.

Step 3: Read and converge on evidence
- Input: Candidate block ids, search response metadata, and the narrow evidence need.
- Action: Follow the search response's `recommended_next` first — it names the block ids to read and a `max_chars` budget derived from `recommended_read_chars`. When evidence is incomplete, escalate in order: `documa_source_window` for neighbor context around a hit; `documa_block_xref` for parent/children/relations; refine the query once, guided by the response `hints`; browse `documa_list_blocks` under the nearest section; whole-section reads (`include_children=true`) are the last resort, justified only when `neighbors.needs_next` is true or the hit is a section heading.
- Token controls: set `max_chars` or `max_tokens` on `documa_read_block` (the tighter wins); set `max_response_tokens` on `documa_search_blocks` as a hard response ceiling; set `include_snippets=false` when only block ids are needed. Token budgets need a configured counter (tiktoken auto-detected, or `DOCUMA_TOKEN_COUNTER=anthropic:<model>` for Claude counting); on `TOKEN_COUNTER_UNAVAILABLE`, fall back to `max_chars`.
- Output: Quoted or paraphrased evidence, block ids, page/source metadata, and any neighbor context needed for interpretation.
- Validation: Only cite blocks that were actually read or otherwise provided in the tool result. Never run two consecutive searches without reading in between unless the first search returned zero results.

Step 4: Answer with evidence boundaries
- Input: Read evidence blocks, user question, and any explicit constraints.
- Action: Distinguish observed evidence from inference. Build final citations with `documa_cite_block` or `documa_render_citation`, and run `documa_verify_citations` before claiming citations were verified. State uncertainty or evidence gaps instead of overclaiming.
- Output: Concise document-grounded answer with citations and evidence/inference separation.
- Validation: Do not invent block ids, page locators, or unsupported claims. Do not depend on parser-native objects.

Step 5: Report gates when reviewing this skill
- Input: Local skill folder and `skill-creator-advanced` validation scripts.
- Action: Run the relevant release or stage gate before claiming readiness. Use `references/readiness_report.md`, `references/migration-governance.md`, `assets/evals/evals.json`, and `assets/evals/regression_gates.json` as the local release evidence.
- Output: PASS / FAIL / BLOCKED gate result and remaining limitations.
- Validation: 任一 final gate、stage gate 或 policy gate 為 FAIL / BLOCKED 時，結論只能是 FAIL 或 BLOCKED。局部 PASS 只可列在定位資訊，且必須明確標註不具放行效力。
</workflow>

<output_contract>
For document-answering tasks, return:
1. Answer: the direct response grounded in read Documa evidence.
2. Evidence: block ids and source/page metadata when available.
3. Inference and limits: what is inferred, uncertain, or not supported by the retrieved evidence.
4. Fallback note: only when Documa tools were unavailable or unusable.

For skill review or release-readiness tasks, return:
1. Conclusion: PASS, FAIL, BLOCKED, or completed modification.
2. Findings: ordered by severity with file paths and gate evidence.
3. Validation: exact commands run and results.
4. Remaining risk: only items that need external environment, live MCP availability, or human review.

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
</examples>

## Hard Rules

- Do not silently replace original text with normalized text.
- Do not depend on parser-native objects.
- Treat search snippets as navigation only; read evidence blocks before citing them.
- Never invent block ids, page locators, or source metadata.
- Follow `recommended_next` and `hints` from search responses before inventing a new query strategy.
