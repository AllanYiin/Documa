"""JSON schemas for tool-calling and MCP wrappers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


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


def documa_tool_schemas() -> list[dict[str, Any]]:
    """Return stable tool descriptors that can be wrapped by MCP servers."""

    return [
        {
            "name": "documa_parse",
            "title": "Parse document into Documa IR",
            "description": "Parse a document through Documa adapters and return UTF-8 JSON metadata.",
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
            "description": "Parse a document, run the default Documa understanding pipeline, and optionally export agent-ready outputs.",
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
                },
                "required": ["status"],
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
            "description": "Return body text for a selected document block, optionally including descendants.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "block_id": {"type": "string"},
                    "include_children": {"type": "boolean", "default": False},
                    "max_chars": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["ir_path", "block_id"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_search_blocks",
            "title": "Search Documa document blocks",
            "description": "Search block metadata, keywords, previews, and body snippets using deterministic lexical matching.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "query": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "default": 10},
                    "any_of": {"type": ["array", "null"], "items": {"type": "string"}},
                    "fields": {"type": ["array", "null"], "items": {"type": "string"}},
                    "snippet_fields": {"type": ["array", "null"], "items": {"type": "string"}},
                    "verbosity": {"type": "string", "enum": ["compact", "standard", "debug"], "default": "compact"},
                    "include_snippets": {"type": "boolean", "default": True},
                    "max_snippets_per_block": {"type": "integer", "minimum": 0, "default": 5},
                    "search_body": {"type": "boolean", "default": True},
                    "context_chars": {"type": "integer", "minimum": 0, "default": 24},
                    "context_words": {"type": "integer", "minimum": 0, "default": 8},
                },
                "required": ["ir_path", "query"],
            },
            "outputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "documa_block_tree",
            "title": "Return Documa document block tree",
            "description": "Return the full block hierarchy without expanding block bodies.",
            "inputSchema": {
                "type": "object",
                "properties": {"ir_path": {"type": "string"}},
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
    ]


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


def openai_tool_schemas(*, strict: bool = False) -> list[dict[str, Any]]:
    """Return OpenAI function-tool descriptors derived from Documa schemas."""

    tools = []
    for descriptor in documa_tool_schemas():
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
