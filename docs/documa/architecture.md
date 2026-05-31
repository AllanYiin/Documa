# Documa Core Architecture

Stage 0 establishes the package skeleton and stable contracts.

## Layers

1. Parser adapters convert external parser output into Documa IR.
2. Documa IR is the parser-neutral source of truth.
3. Pipeline stages transform IR while preserving evidence.
4. Exporters produce JSON, Markdown, RAG chunks, and assets.
5. Interfaces expose the same behavior through Python API, CLI, MCP, and tool calling.

## Stage 0 Non-goals

- No UI.
- No MCP server implementation.
- No LLM/RLM inference.

## Stage 2 Adapter Baseline

The first concrete parser adapter is `PyMuPDFAdapter`. It extracts text spans,
page metadata, page preview images, PDF links, annotations, and embedded image
assets into parser-neutral Documa IR. PyMuPDF objects must not leave the adapter
boundary.

## Stage 3 Understanding Pipeline Baseline

Stage 3 introduces testable, parser-neutral pipeline stages:

- `ReadingOrderStage`
- `InlineSemanticsStage`
- `LayoutClassificationStage`
- `ParagraphGroupingStage`
- `TableNormalizationStage`
- `ImageNormalizationStage`

These stages are conservative baselines. They attach confidence and metadata,
preserve source references, and avoid pretending that heuristic output is a
fully solved document understanding model.

## Stage 4 Relation Pipeline Baseline

Stage 4 materializes document relations that downstream RAG, RLM, MCP, CLI, and
tool-calling consumers need:

- `FootnoteLinkingStage`
- `TocLinkingStage`
- `CaptionLinkingStage`
- `ProvenanceLinkingStage`

The stages prefer explicit adapter evidence and conservative layout proximity.
When a target cannot be confirmed, they create `UNRESOLVED` relations instead of
silently dropping evidence or inventing links. This keeps the IR useful for
agent workflows that need traceability and repair loops.

## Stage 5 RAG And Tooling Baseline

Stage 5 turns Documa IR into agent-consumable outputs:

- `ChunkingStage` creates RAG/RLM-ready chunks with source block ids, page refs,
  bbox refs, heading paths, asset refs, and metadata.
- `JsonExporter`, `MarkdownExporter`, and `RagJsonExporter` expose IR, readable
  Markdown, and chunk records compatible with common ingestion patterns.
- CLI `export` and `inspect` now return structured JSON. `rag-json` export can
  auto-create chunks when an IR file has not been chunked yet.
- `documa_tool_schemas()` provides JSON Schema descriptors that can be wrapped
  by MCP servers or direct LLM tool-calling integrations.

The baseline intentionally does not implement a full MCP server yet. It keeps
the stable schema layer separate so MCP, CLI, and SDK wrappers can reuse the
same contract.

## Stage 6 Tool Execution Baseline

Stage 6 adds a shared tool execution layer for agents:

- `parse_document_tool`, `export_document_tool`, and `inspect_document_tool`
  are plain Python functions that return structured JSON payloads.
- `call_documa_tool()` wraps those functions in an MCP-compatible result shape
  with `content`, `structuredContent`, and `isError`.
- CLI commands now call the same tool service layer as MCP/tool-calling
  integrations, reducing drift between interfaces.
- `documa tools` prints the current tool schemas.
- `documa-mcp` is an optional MCP server entry point. It requires installing the
  `mcp` extra, keeping the core package free of runtime MCP dependencies.

This stage is still parser-adapter based. It does not introduce a UI, a custom
PDF parser, or LLM inference.

## Stage 7 Benchmark Baseline

Stage 7 adds an executable quality harness for the nine original PDF parsing
risks:

- `run_fixture_benchmark()` validates the fixture manifest and produces
  per-case structured results.
- Missing fixture files are `skipped` by default so the benchmark can run before
  real PDFs are checked in.
- `--require-files` turns missing declarations or files into failed cases for
  release gates.
- `documa benchmark` and `documa_benchmark` expose the same structured result
  through CLI and tool-calling.

This is a readiness and regression harness, not yet a full document-quality
score. As real fixtures are added, capability-specific checks can be attached to
the existing per-case result format.

## Stage 8 Release Readiness Baseline

Stage 8 adds install and release readiness checks:

- `run_doctor()` validates Python version, package importability, optional
  dependency availability, core project files, and fixture benchmark readiness.
- `documa doctor` and `documa_doctor` expose the same structured diagnostics
  through CLI and tool-calling.
- `pyproject.toml` now carries package keywords, classifiers, project URLs, and
  optional extras for PDF and MCP integrations.
- GitHub Actions CI installs the package, runs the unit tests, and executes
  `documa doctor` across Python 3.10, 3.11, and 3.12.

The doctor treats optional integrations such as PyMuPDF and MCP as warnings
when absent. Core package readiness should remain usable without those extras.

## Stage 9 End-To-End Processing Baseline

Stage 9 adds an orchestration layer:

- `run_default_pipeline()` applies the default Documa transformations in order:
  reading order, inline semantics, layout, paragraphs, tables, images,
  relations, chunking, and provenance.
- `documa process` parses a document and runs the full pipeline in one command.
- `documa_process` exposes the same capability for direct tool-calling and MCP
  wrappers.
- When `--out` is provided, processing writes `documa.ir.json` plus a default
  `documa.rag.json` export for retrieval ingestion.

`parse` remains the low-level adapter boundary. `process` is the high-level
agent ingestion entry point.

## Stage 10 Progressive Block Reading Baseline

Stage 10 promotes hierarchical document blocks to the primary agent reading
interface. Rows, lines, spans, and parser-native blocks remain evidence at the
adapter boundary, while `DocumentBlockIR` provides a progressive-disclosure
tree that agents can traverse as document, section, page, paragraph, table,
image, footnote, or metadata blocks.

The default pipeline now builds `document_blocks` after relations and before
optional retrieval chunking:

- `BlockTreeBuildingStage` creates a body tree from headings, page boundaries,
  and page-local blocks. Page headers, footers, and page numbers are preserved
  as furniture metadata instead of being mixed into body text.
- `BlockKeywordExtractionStage` computes keyword metadata bottom-up. Leaf
  blocks collect bounded term statistics; parent blocks aggregate child
  counters and use dynamic support/frequency thresholds rather than rescanning
  raw text at every level.
- `ChunkingStage` is now an optional intra-block retrieval view when
  `document_blocks` exist. Chunks carry `parent_block_id` and must not create
  new cross-block semantic boundaries. Table chunks repeat table context such
  as section path, caption, notes, and column headers when a table must be split.

New progressive-reading tools expose the same behavior through CLI and
tool-calling:

- `documa_list_blocks`
- `documa_inspect_block`
- `documa_read_block`
- `documa_search_blocks`

`rag-json` remains backward compatible and includes additive block metadata.
`block-json` exports the block tree directly for workflows that do not need
retrieval chunks.

## Stage 11 Block Reading Demo

Stage 11 adds a CLI demonstration workflow for the block-based reading model.
`documa block-demo` parses a PDF, runs the understanding pipeline without
retrieval chunking, records the full block list and metadata, ranks blocks from
metadata and previews, reads only selected block bodies, and synthesizes an
extractive answer from loaded evidence.

The trace is written as structured JSON and includes:

- each logical call and returned payload,
- elapsed time for every step,
- token usage for call and returned content,
- selected block ids and scoring details,
- loaded block body excerpts,
- deterministic answer synthesis and evidence.

The demo is intentionally offline and deterministic. It does not require an LLM
provider or network access. Token accounting uses `tiktoken` when available and
falls back to a clearly labeled heuristic otherwise.
