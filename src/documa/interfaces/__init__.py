"""Interfaces for agent and tool integrations."""

from documa.interfaces.tool_schemas import documa_tool_schemas, openai_tool_schemas
from documa.interfaces.tools import (
    benchmark_tool,
    block_tree_tool,
    block_xref_tool,
    call_documa_tool,
    doctor_tool,
    export_document_tool,
    inspect_block_tool,
    inspect_document_tool,
    list_blocks_tool,
    list_documa_tools,
    parse_document_tool,
    process_document_tool,
    read_block_tool,
    search_blocks_tool,
    view_document_tool,
)

__all__ = [
    "benchmark_tool",
    "block_tree_tool",
    "block_xref_tool",
    "call_documa_tool",
    "doctor_tool",
    "documa_tool_schemas",
    "openai_tool_schemas",
    "export_document_tool",
    "inspect_block_tool",
    "inspect_document_tool",
    "list_blocks_tool",
    "list_documa_tools",
    "parse_document_tool",
    "process_document_tool",
    "read_block_tool",
    "search_blocks_tool",
    "view_document_tool",
]
