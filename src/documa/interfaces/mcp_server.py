"""Optional MCP server wrapper for Documa tools."""

from typing import Any

from documa.interfaces.tools import (
    benchmark_tool,
    block_tree_tool,
    block_xref_tool,
    doctor_tool,
    export_document_tool,
    index_collection_tool,
    inspect_block_tool,
    inspect_document_tool,
    ingest_mailbox_tool,
    list_blocks_tool,
    parse_document_tool,
    process_document_tool,
    read_block_tool,
    search_blocks_tool,
    search_collection_tool,
    view_document_tool,
)


def create_mcp_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("Install Documa with the 'mcp' extra to run the MCP server.") from exc

    mcp = FastMCP(
        "Documa",
        instructions=(
            "Use Documa for large PDFs, long documents, document QA, evidence search, "
            "citations, and source-grounded summaries. Parse files into Documa IR, "
            "search progressive document blocks first, then read selected blocks instead of "
            "loading whole documents into context."
        ),
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
        export_formats: list[str] | None = None,
    ) -> dict[str, Any]:
        """Parse a document and run the default Documa processing pipeline."""

        return process_document_tool(
            source=source,
            out=out,
            lang=lang,
            max_chars=max_chars,
            export_formats=export_formats,
        )

    @mcp.tool()
    def documa_ingest_mailbox(
        source: str,
        out: str,
        lang: str = "auto",
        max_chars: int = 1200,
        export_formats: list[str] | None = None,
        recursive: bool = False,
        continue_on_error: bool = True,
        progress: str = "text",
    ) -> dict[str, Any]:
        """Ingest a directory of EML/MSG messages as a Documa email collection."""

        return ingest_mailbox_tool(
            source=source,
            out=out,
            lang=lang,
            max_chars=max_chars,
            export_formats=export_formats,
            recursive=recursive,
            continue_on_error=continue_on_error,
            progress=progress,
        )

    @mcp.tool()
    def documa_export(
        ir_path: str,
        format: str = "json",
        out: str | None = None,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        """Export Documa IR as JSON, Markdown, RAG JSON, or block JSON."""

        return export_document_tool(ir_path=ir_path, format=format, out=out, max_chars=max_chars)

    @mcp.tool()
    def documa_inspect(ir_path: str) -> dict[str, Any]:
        """Inspect a Documa IR file."""

        return inspect_document_tool(ir_path=ir_path)

    @mcp.tool()
    def documa_view(
        source: str | None = None,
        ir_path: str | None = None,
        out: str | None = None,
        format: str = "json",
        query: str = "",
        lang: str = "auto",
        max_chars: int = 1200,
        max_depth: int | None = None,
        include_body: bool = False,
        body_chars: int = 1200,
        result_limit: int = 10,
    ) -> dict[str, Any]:
        """Build a universal hierarchical human viewer for any Documa-supported document."""

        return view_document_tool(
            source=source,
            ir_path=ir_path,
            out=out,
            format=format,
            query=query,
            lang=lang,
            max_chars=max_chars,
            max_depth=max_depth,
            include_body=include_body,
            body_chars=body_chars,
            result_limit=result_limit,
        )

    @mcp.tool()
    def documa_list_blocks(
        ir_path: str,
        depth: int | None = None,
        parent_id: str | None = None,
        include_metadata_summary: bool = True,
    ) -> dict[str, Any]:
        """List progressive document blocks without full block bodies."""

        return list_blocks_tool(
            ir_path=ir_path,
            depth=depth,
            parent_id=parent_id,
            include_metadata_summary=include_metadata_summary,
        )

    @mcp.tool()
    def documa_inspect_block(ir_path: str, block_id: str) -> dict[str, Any]:
        """Inspect metadata for one progressive document block."""

        return inspect_block_tool(ir_path=ir_path, block_id=block_id)

    @mcp.tool()
    def documa_read_block(
        ir_path: str,
        block_id: str,
        include_children: bool = False,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """Read one selected document block body."""

        return read_block_tool(
            ir_path=ir_path,
            block_id=block_id,
            include_children=include_children,
            max_chars=max_chars,
        )

    @mcp.tool()
    def documa_search_blocks(
        ir_path: str,
        query: str = "",
        limit: int = 10,
        any_of: list[str] | None = None,
        fields: list[str] | None = None,
        snippet_fields: list[str] | None = None,
        verbosity: str = "compact",
        include_snippets: bool = True,
        max_snippets_per_block: int = 2,
        search_body: bool = True,
        context_chars: int = 24,
        context_words: int = 8,
    ) -> dict[str, Any]:
        """Search progressive document blocks with bounded snippets."""

        return search_blocks_tool(
            ir_path=ir_path,
            query=query,
            limit=limit,
            any_of=any_of,
            fields=fields,
            snippet_fields=snippet_fields,
            verbosity=verbosity,
            include_snippets=include_snippets,
            max_snippets_per_block=max_snippets_per_block,
            search_body=search_body,
            context_chars=context_chars,
            context_words=context_words,
        )

    @mcp.tool()
    def documa_index_collection(store_dir: str = ".documa", collection_id: str = "default") -> dict[str, Any]:
        """Build the local collection search index from active registry documents."""

        return index_collection_tool(store_dir=store_dir, collection_id=collection_id)

    @mcp.tool()
    def documa_search_collection(
        query: str,
        store_dir: str = ".documa",
        collection_id: str = "default",
        limit: int = 20,
        per_document_limit: int | None = None,
    ) -> dict[str, Any]:
        """Search active registry documents through the local collection index."""

        return search_collection_tool(
            query=query,
            store_dir=store_dir,
            collection_id=collection_id,
            limit=limit,
            per_document_limit=per_document_limit,
        )

    @mcp.tool()
    def documa_block_tree(ir_path: str) -> dict[str, Any]:
        """Return the progressive document block hierarchy."""

        return block_tree_tool(ir_path=ir_path)

    @mcp.tool()
    def documa_block_xref(ir_path: str, block_id: str) -> dict[str, Any]:
        """Return references around one progressive document block."""

        return block_xref_tool(ir_path=ir_path, block_id=block_id)

    @mcp.tool()
    def documa_benchmark(
        manifest_path: str = "fixtures/pdf/manifest.json",
        fixtures_dir: str = "fixtures/pdf",
        out: str | None = None,
        require_files: bool = False,
    ) -> dict[str, Any]:
        """Run the Documa fixture benchmark."""

        return benchmark_tool(
            manifest_path=manifest_path,
            fixtures_dir=fixtures_dir,
            out=out,
            require_files=require_files,
        )

    @mcp.tool()
    def documa_doctor(
        project_root: str = ".",
        include_benchmark: bool = True,
        store_dir: str | None = None,
    ) -> dict[str, Any]:
        """Run Documa environment diagnostics."""

        return doctor_tool(project_root=project_root, include_benchmark=include_benchmark, store_dir=store_dir)

    return mcp


def main() -> None:
    create_mcp_server().run()


if __name__ == "__main__":
    main()
