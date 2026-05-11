"""Optional MCP server wrapper for Documa tools."""

from __future__ import annotations

from typing import Any

from documa.interfaces.tools import export_document_tool, inspect_document_tool, parse_document_tool, process_document_tool


def create_mcp_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("Install Documa with the 'mcp' extra to run the MCP server.") from exc

    mcp = FastMCP(
        "Documa",
        instructions="Parse documents into Documa IR and export RAG-ready structured outputs.",
    )

    @mcp.tool()
    def documa_parse(source: str, out: str | None = None, lang: str = "auto") -> dict[str, Any]:
        """Parse a document into Documa IR."""

        return parse_document_tool(source=source, out=out, lang=lang)

    @mcp.tool()
    def documa_process(
        source: str,
        out: str | None = None,
        lang: str = "auto",
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        """Parse a document and run the default Documa processing pipeline."""

        return process_document_tool(source=source, out=out, lang=lang, max_chars=max_chars)

    @mcp.tool()
    def documa_export(
        ir_path: str,
        format: str = "json",
        out: str | None = None,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        """Export Documa IR as JSON, Markdown, or RAG JSON."""

        return export_document_tool(ir_path=ir_path, format=format, out=out, max_chars=max_chars)

    @mcp.tool()
    def documa_inspect(ir_path: str) -> dict[str, Any]:
        """Inspect a Documa IR file."""

        return inspect_document_tool(ir_path=ir_path)

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
