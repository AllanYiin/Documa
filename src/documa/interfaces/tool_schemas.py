"""JSON schemas for tool-calling and MCP wrappers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from documa.interfaces.tool_profiles import allowed_tool_names


def _status_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "document_id": {"type": "string"},
            "output_path": {"type": ["string", "null"]},
        },
        "required": ["status"],
    }


def documa_tool_schemas(profile: str = "admin") -> list[dict[str, Any]]:
    """Return stable tool descriptors that can be wrapped by MCP servers."""

    descriptors = [
        {
            "name": "documa_parse",
            "title": "Parse document into Documa IR",
            "description": "Parse a PDF or document through Documa adapters and return UTF-8 JSON metadata for structured document understanding.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "out": {"type": ["string", "null"]},
                    "lang": {"type": "string", "default": "auto"},
                },
                "required": ["source"],
            },
            "outputSchema": _status_output_schema(),
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_export",
            "title": "Export Documa IR",
            "description": "Export Documa IR as JSON, Markdown, RAG JSON chunks, or block JSON.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "format": {"type": "string", "enum": ["json", "markdown", "rag-json", "block-json"]},
                    "out": {"type": ["string", "null"]},
                    "max_chars": {"type": "integer", "minimum": 1},
                },
                "required": ["ir_path"],
            },
            "outputSchema": _status_output_schema(),
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_process",
            "title": "Parse and process document",
            "description": (
                "Process a large PDF or long document. Pass the required source field (not input_path) "
                "and set out for large files so the full IR is written to disk instead of returned inline. "
                "The response includes a one-line-per-stage pipeline summary; full stage diagnostics are "
                "written to pipeline_report_path when out is set."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "out": {"type": ["string", "null"]},
                    "lang": {"type": "string", "default": "auto"},
                    "max_chars": {"type": "integer", "minimum": 1, "default": 1200},
                    "export_formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["json", "markdown", "rag-json", "block-json"]},
                    },
                    "ocr": {"type": "boolean", "default": False},
                },
                "required": ["source"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_id": {"type": "string"},
                    "output_path": {"type": ["string", "null"]},
                    "export_paths": {"type": "object"},
                    "pipeline": {"type": "object"},
                    "pipeline_report_path": {"type": ["string", "null"]},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_ingest_mailbox",
            "title": "Ingest email mailbox directory",
            "description": (
                "Ingest a directory of .eml/.msg messages as a Documa email collection, "
                "writing one processed document per message plus a mailbox manifest."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "out": {"type": "string"},
                    "lang": {"type": "string", "default": "auto"},
                    "max_chars": {"type": "integer", "minimum": 1, "default": 1200},
                    "export_formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["json", "markdown", "rag-json", "block-json"]},
                    },
                    "recursive": {"type": "boolean", "default": False},
                    "continue_on_error": {"type": "boolean", "default": True},
                    "progress": {"type": "string", "enum": ["text", "jsonl"], "default": "text"},
                },
                "required": ["source", "out"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "collection_type": {"type": "string"},
                    "manifest_path": {"type": "string"},
                    "summary": {"type": "object"},
                    "documents": {"type": "array"},
                    "errors": {"type": "array"},
                },
                "required": ["status", "summary"],
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_inspect",
            "title": "Inspect Documa IR",
            "description": "Return a structured summary of pages, chunks, relations, tables, and images.",
            "inputSchema": {
                "type": "object",
                "properties": {"ir_path": {"type": "string"}},
                "required": ["ir_path"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_id": {"type": "string"},
                    "page_count": {"type": "integer"},
                    "chunk_count": {"type": "integer"},
                    "relation_count": {"type": "integer"},
                },
                "required": ["status", "document_id"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_view",
            "title": "Build Documa universal viewer",
            "description": (
                "Build a hierarchical human-viewer payload for any Documa-supported source or existing IR, "
                "including document metadata, block metadata, optional body text, and query matches."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": ["string", "null"]},
                    "ir_path": {"type": ["string", "null"]},
                    "out": {"type": ["string", "null"]},
                    "format": {"type": "string", "enum": ["json", "markdown", "html"], "default": "json"},
                    "query": {"type": "string", "default": ""},
                    "lang": {"type": "string", "default": "auto"},
                    "max_chars": {"type": "integer", "minimum": 1, "default": 1200},
                    "max_depth": {"type": ["integer", "null"], "minimum": 0},
                    "include_body": {"type": "boolean", "default": False},
                    "body_chars": {"type": "integer", "minimum": 1, "default": 1200},
                    "result_limit": {"type": "integer", "minimum": 0, "default": 10},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_id": {"type": "string"},
                    "format": {"type": "string"},
                    "output_path": {"type": ["string", "null"]},
                    "viewer": {"type": ["object", "null"]},
                    "content": {"type": ["string", "null"]},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_list_blocks",
            "title": "List Documa document blocks",
            "description": "Return a progressive-disclosure block list without full block bodies.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "depth": {"type": ["integer", "null"], "minimum": 0},
                    "parent_id": {"type": ["string", "null"]},
                    "include_metadata_summary": {"type": "boolean", "default": True},
                    "limit": {"type": ["integer", "null"], "minimum": 1, "description": "Return at most this many blocks; pair with offset to page through large documents."},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                },
                "required": ["ir_path"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_inspect_block",
            "title": "Inspect Documa document block metadata",
            "description": "Return metadata for a single document block without expanding all body text.",
            "inputSchema": {
                "type": "object",
                "properties": {"ir_path": {"type": "string"}, "block_id": {"type": "string"}},
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_read_block",
            "title": "Read Documa document block body",
            "description": "Read exact source text for a selected Documa block after evidence search, optionally including descendants.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_id": {"type": "string"},
                    "include_children": {"type": "boolean", "default": False},
                    "max_chars": {"type": ["integer", "null"], "minimum": 1},
                    "max_tokens": {"type": ["integer", "null"], "minimum": 1, "description": "Truncate content to this many tokens, counted by the configured token counter (tiktoken auto-detected, or DOCUMA_TOKEN_COUNTER=anthropic:<model>); errors with TOKEN_COUNTER_UNAVAILABLE when no counter is configured. The tighter of max_chars/max_tokens wins."},
                    "start": {"type": "integer", "minimum": 0, "default": 0, "description": "Continuation character cursor returned by a previous read."},
                },
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_read_blocks",
            "title": "Read several Documa evidence blocks",
            "description": "Batch-read selected blocks under one exact token budget, with optional deterministic context expansion.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "total_max_tokens": {"type": "integer", "minimum": 1, "default": 800},
                    "per_block_max_tokens": {"type": ["integer", "null"], "minimum": 1},
                    "context_mode": {
                        "type": "string",
                        "enum": ["none", "auto", "neighbors", "children"],
                        "default": "none"
                    },
                },
                "required": ["ir_path", "block_ids"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "results": {"type": "array"}, "budget": {"type": "object"}},
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_search_blocks",
            "title": "Search Documa document blocks",
            "description": "Search large PDF or long-document evidence blocks by metadata, keywords, previews, and bounded body snippets before reading full text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "query": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                    "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Skip this many ranked results; use with total_matches for paging."},
                    "any_of": {"type": ["array", "null"], "items": {"type": "string"}},
                    "fields": {"type": ["array", "null"], "items": {"type": "string"}},
                    "snippet_fields": {"type": ["array", "null"], "items": {"type": "string"}},
                    "response_profile": {"type": "string", "enum": ["nav", "evidence", "debug"], "default": "nav", "description": "nav returns only routing fields; evidence adds citation and selection metadata; debug adds diagnostics."},
                    "verbosity": {"type": "string", "enum": ["compact", "standard", "debug"], "description": "Deprecated compatibility alias. Explicit compact/standard requests the legacy evidence shape; debug maps to response_profile=debug."},
                    "include_snippets": {"type": "boolean", "default": True},
                    "max_snippets_per_block": {"type": "integer", "minimum": 0, "default": 2},
                    "search_body": {"type": "boolean", "default": True},
                    "context_chars": {"type": "integer", "minimum": 0, "default": 24},
                    "context_words": {"type": "integer", "minimum": 0, "default": 8},
                    "max_response_tokens": {"type": ["integer", "null"], "minimum": 0, "description": "Hard ceiling on the complete compact-serialized structured response. Default: an automatic 2000-token ceiling when a token counter is configured; pass 0 to disable."},
                    "granularity": {"type": "string", "enum": ["auto", "section", "leaf", "mixed"], "default": "auto"},
                    "scope_block_id": {"type": ["string", "null"], "description": "Restrict retrieval to this block and its descendants."},
                    "max_evidence_tokens": {"type": ["integer", "null"], "minimum": 1, "description": "Exact shared token cap used by adaptive evidence selection."},
                    "adaptive_top_k": {"type": "boolean", "default": True},
                    "max_results_per_branch": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["ir_path", "query"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_block_tree",
            "title": "Return Documa document block tree",
            "description": "Return the block hierarchy (the document outline) without expanding block bodies. Use max_depth=2-3 for a cheap structural overview.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "max_depth": {"type": ["integer", "null"], "minimum": 0, "description": "Collapse subtrees below this depth to children_count."},
                    "max_nodes": {"type": "integer", "minimum": 1, "default": 500},
                    "include_citations": {"type": "boolean", "default": False, "description": "Set true to add per-node page citation labels; the default lean outline omits them."},
                    "include_sketches": {"type": "boolean", "default": False, "description": "Set true to attach a precomputed one-glance summary (sketch) and read_cost_chars per section from the search sidecar; the cheapest document overview."},
                },
                "required": ["ir_path"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_block_xref",
            "title": "Return Documa block references",
            "description": "Return parent, children, source blocks, source chunks, and relations for one block.",
            "inputSchema": {
                "type": "object",
                "properties": {"ir_path": {"type": "string"}, "block_id": {"type": "string"}},
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_benchmark",
            "title": "Run Documa fixture benchmark",
            "description": "Validate the fixture manifest and report benchmark readiness for Documa parsing risks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "manifest_path": {"type": "string", "default": "fixtures/pdf/manifest.json"},
                    "fixtures_dir": {"type": "string", "default": "fixtures/pdf"},
                    "out": {"type": ["string", "null"]},
                    "require_files": {"type": "boolean", "default": False},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "summary": {"type": "object"},
                    "cases": {"type": "array"},
                    "output_path": {"type": ["string", "null"]},
                },
                "required": ["status", "summary", "cases"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_doctor",
            "title": "Run Documa environment diagnostics",
            "description": "Check package, optional dependency, and fixture readiness for Documa.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {"type": "string", "default": "."},
                    "include_benchmark": {"type": "boolean", "default": True},
                    "store_dir": {"type": ["string", "null"], "default": None},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "documa_version": {"type": "string"},
                    "summary": {"type": "object"},
                    "checks": {"type": "array"},
                },
                "required": ["status", "summary", "checks"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_cite_block",
            "title": "Cite a document block",
            "description": (
                "Resolve a block id to its citation: page label, bounding boxes, source excerpt, "
                "and a formatted citation string."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_id": {"type": "string"},
                    "style": {"type": "string", "enum": ["page-bbox", "markdown", "inline"], "default": "page-bbox"},
                },
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "block_id": {"type": "string"},
                    "page_label": {"type": "string"},
                    "grounding": {"type": "string"},
                    "bboxes": {"type": "array"},
                    "excerpt": {"type": "string"},
                    "citation_string": {"type": "string"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_cite_chunk",
            "title": "Cite a RAG chunk",
            "description": (
                "Resolve a chunk id to its citation: page label, bounding boxes, excerpt, heading path, "
                "and the source blocks it was built from."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "style": {"type": "string", "enum": ["page-bbox", "markdown", "inline"], "default": "page-bbox"},
                },
                "required": ["ir_path", "chunk_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "page_label": {"type": "string"},
                    "grounding": {"type": "string"},
                    "bboxes": {"type": "array"},
                    "excerpt": {"type": "string"},
                    "source_blocks": {"type": "array"},
                    "citation_string": {"type": "string"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_render_citation",
            "title": "Render a citation string",
            "description": "Render a formatted citation string for a chunk id or block id in the requested style.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "ref_id": {"type": "string"},
                    "style": {"type": "string", "enum": ["page-bbox", "markdown", "inline"], "default": "page-bbox"},
                },
                "required": ["ir_path", "ref_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "ref_id": {"type": "string"},
                    "style": {"type": "string"},
                    "citation_string": {"type": "string"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_source_window",
            "title": "Read blocks around a block",
            "description": "Return the neighboring blocks (by reading order) around a block id for extra context.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_id": {"type": "string"},
                    "before": {"type": "integer", "minimum": 0, "default": 1},
                    "after": {"type": "integer", "minimum": 0, "default": 1},
                },
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "block_id": {"type": "string"},
                    "window": {"type": "array"},
                    "warnings": {"type": "array"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_verify_citations",
            "title": "Verify cited block ids",
            "description": (
                "Check that every cited block id exists in the document and carries a page reference. "
                "This is an id-existence check, not semantic answer verification."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ir_path", "block_ids"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "items": {"type": "array"},
                    "overall_valid": {"type": "boolean"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_ingest",
            "title": "Ingest document into the local store",
            "description": (
                "Parse + process a document into the local document store and return a stable, "
                "content-addressed document_id. Re-ingesting identical content deduplicates."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "store_dir": {"type": "string", "default": ".documa"},
                    "lang": {"type": "string", "default": "auto"},
                    "max_chars": {"type": "integer", "minimum": 1, "default": 1200},
                    "ocr": {"type": "boolean", "default": False},
                },
                "required": ["source"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_id": {"type": "string"},
                    "deduplicated": {"type": "boolean"},
                    "ir_path": {"type": "string"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_list_documents",
            "title": "List ingested documents",
            "description": "List all documents registered in the local document store.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "store_dir": {"type": "string", "default": ".documa"},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_count": {"type": "integer"},
                    "documents": {"type": "array"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_inspect_store",
            "title": "Inspect local document store health",
            "description": (
                "Report store health: index integrity, entries with missing IR files, orphan "
                "document directories, and registry lock state."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "store_dir": {"type": "string", "default": ".documa"},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "document_count": {"type": "integer"},
                    "missing_ir": {"type": "array"},
                    "orphan_dirs": {"type": "array"},
                    "lock": {"type": "object"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_index_collection",
            "title": "Build local collection index",
            "description": "Build or rebuild the local SQLite FTS collection index from active registry documents.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "store_dir": {"type": "string", "default": ".documa"},
                    "collection_id": {"type": "string", "default": "default"},
                },
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "collection_id": {"type": "string"},
                    "index_path": {"type": "string"},
                    "document_count": {"type": "integer"},
                    "block_count": {"type": "integer"},
                    "index_version": {"type": "string"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": False},
        },
        {
            "name": "documa_search_collection",
            "title": "Search local document collection",
            "description": "Search active large-document registry entries and return citation-ready Documa block results across the local collection index.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Terms are AND-ed; wrap adjacent words in double quotes for phrase matching. Falls back to any-term matching (match_mode=any_term) when nothing matches all terms."},
                    "store_dir": {"type": "string", "default": ".documa"},
                    "collection_id": {"type": "string", "default": "default"},
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "per_document_limit": {"type": ["integer", "null"], "default": None},
                    "document_ids": {"type": ["array", "null"], "items": {"type": "string"}, "maxItems": 100, "description": "Restrict the search to these doc- registry ids (scoped follow-up queries)."},
                    "group_by_document": {"type": "boolean", "default": False, "description": "Page over document-level rollups (exact hit_count, best snippet, up to 3 read-ready top_blocks) — the right shape for 'which documents mention X'."},
                    "response_profile": {"type": "string", "enum": ["nav", "evidence"], "default": "nav", "description": "nav emits compact hits/rollups for routing; evidence preserves citation-ready rows."},
                    "max_response_tokens": {"type": ["integer", "null"], "minimum": 0, "description": "Hard ceiling on the complete compact-serialized structured response. Default: an automatic 2000-token ceiling when a token counter is configured; pass 0 to disable."},
                },
                "required": ["query"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "collection_id": {"type": "string"},
                    "query": {"type": "string"},
                    "match_mode": {"type": ["string", "null"]},
                    "group_by_document": {"type": "boolean"},
                    "result_count": {"type": "integer"},
                    "document_count": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "has_more": {"type": "boolean"},
                    "results": {"type": "array"},
                    "hints": {"type": "array", "items": {"type": "string"}},
                    "recommended_next": {"type": "object"},
                    "budget": {"type": "object"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_validate_ir",
            "title": "Validate IR against the Documa schema",
            "description": "Validate an IR JSON file against the published documa.schema.json contract.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                },
                "required": ["ir_path"],
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "valid": {"type": "boolean"},
                    "violations": {"type": "array"},
                },
                "required": ["status"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]
    allowed = allowed_tool_names(profile)
    return [descriptor for descriptor in descriptors if descriptor["name"] in allowed]



def _with_no_extra_properties(schema: dict[str, Any], *, require_all: bool) -> dict[str, Any]:
    schema = deepcopy(schema)
    if schema.get("type") == "object":
        schema.setdefault("additionalProperties", False)
        properties = schema.get("properties", {})
        if require_all:
            schema["required"] = list(properties)
        for value in properties.values():
            if isinstance(value, dict):
                _apply_no_extra_properties(value, require_all=require_all)
    return schema


def _apply_no_extra_properties(schema: dict[str, Any], *, require_all: bool) -> None:
    if schema.get("type") == "object":
        schema.setdefault("additionalProperties", False)
        properties = schema.get("properties", {})
        if require_all:
            schema["required"] = list(properties)
        for value in properties.values():
            if isinstance(value, dict):
                _apply_no_extra_properties(value, require_all=require_all)
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        _apply_no_extra_properties(schema["items"], require_all=require_all)


def openai_tool_schemas(*, strict: bool = False, profile: str = "admin") -> list[dict[str, Any]]:
    """Return OpenAI function-tool descriptors derived from Documa schemas."""

    tools = []
    for descriptor in documa_tool_schemas(profile=profile):
        parameters = _with_no_extra_properties(descriptor["inputSchema"], require_all=strict)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": descriptor["name"],
                    "description": descriptor["description"],
                    "parameters": parameters,
                    "strict": strict,
                },
            }
        )
    return tools
