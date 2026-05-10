"""JSON schemas for tool-calling and MCP wrappers."""

from __future__ import annotations

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
            "description": "Export Documa IR as JSON, Markdown, or RAG JSON chunks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ir_path": {"type": "string"},
                    "format": {"type": "string", "enum": ["json", "markdown", "rag-json"]},
                    "out": {"type": ["string", "null"]},
                    "max_chars": {"type": "integer", "minimum": 1},
                },
                "required": ["ir_path"],
            },
            "outputSchema": _status_output_schema(),
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
    ]
