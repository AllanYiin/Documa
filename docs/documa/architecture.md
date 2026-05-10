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
