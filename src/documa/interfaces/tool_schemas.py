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
