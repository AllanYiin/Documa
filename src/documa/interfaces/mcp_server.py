"""Optional MCP server wrapper for Documa tools."""

import functools
import inspect
import json
import os
import sys
import threading
import time
from typing import Any

from documa.interfaces.tools import (
    benchmark_tool,
    block_tree_tool,
    block_xref_tool,
    cite_block_tool,
    doctor_tool,
    export_document_tool,
    index_collection_tool,
    ingest_document_tool,
    inspect_block_tool,
    inspect_document_tool,
    inspect_skill_graph_tool,
    ingest_mailbox_tool,
    list_blocks_tool,
    list_documents_tool,
    load_skill_tool,
    parse_document_tool,
    process_document_tool,
    read_block_tool,
    read_blocks_tool,
    read_skill_resource_tool,
    render_citation_tool,
    search_blocks_tool,
    search_collection_tool,
    source_window_tool,
    skill_status_tool,
    sync_skills_tool,
    verify_citations_tool,
    view_document_tool,
)
from documa.interfaces.tool_profiles import allowed_tool_names


_MCP_REGISTERED_TOOLS = {
    "documa_parse",
    "documa_process",
    "documa_ingest_mailbox",
    "documa_export",
    "documa_inspect",
    "documa_view",
    "documa_list_blocks",
    "documa_inspect_block",
    "documa_read_block",
    "documa_read_blocks",
    "documa_search_blocks",
    "documa_ingest",
    "documa_index_collection",
    "documa_search_collection",
    "documa_block_tree",
    "documa_block_xref",
    "documa_cite_block",
    "documa_render_citation",
    "documa_source_window",
    "documa_verify_citations",
    "documa_list_documents",
    "documa_benchmark",
    "documa_doctor",
    "documa_load_skill",
    "documa_read_skill_resource",
    "documa_sync_skills",
    "documa_skill_status",
    "documa_inspect_skill_graph",
}


def create_mcp_server(profile: str | None = None) -> Any:
    active_profile = (profile or os.environ.get("DOCUMA_MCP_PROFILE") or "admin").casefold()
    allowed = allowed_tool_names(active_profile)

    # A configured catalog is compiled once when the MCP process starts.  Do
    # not create .documa state for users who have not opted into skill roots.
    try:
        from documa.skills.store import config_path, ensure_skill_store

        if config_path().exists():
            ensure_skill_store(refresh=True)
    except (OSError, ValueError):
        # Discovery must remain available so the status/admin tools can report
        # the broken configuration instead of preventing the server from booting.
        pass

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - broken or incomplete installation
        raise RuntimeError("Install or repair Documa to run the MCP server: pip install --upgrade documa") from exc

    mcp = FastMCP(
        "Documa",
        instructions=(
            "Use Documa for large PDFs, long documents, document QA, evidence search, "
            "citations, source-grounded summaries, and bounded dynamic skill loading. Parse files into Documa IR, "
            "search progressive document blocks first, then read selected blocks instead of "
            "loading whole documents into context."
        ),
    )

    def _documa_tool(fn):
        # Ship each payload exactly once: compact JSON text, no structuredContent
        # mirror. FastMCP would otherwise serialize the dict twice (structured +
        # indent=2 text), doubling the tokens every response costs the host.
        @functools.wraps(fn)
        def wrapper(**kwargs: Any) -> str:
            return json.dumps(fn(**kwargs), ensure_ascii=False, separators=(",", ":"))

        wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=str)
        wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}), "return": str}
        return mcp.tool(structured_output=False)(wrapper)

    @_documa_tool
    def documa_parse(
        source: str,
        out: str | None = None,
        lang: str = "auto",
        pdf_provider: str = "auto",
        office_provider: str = "auto",
    ) -> dict[str, Any]:
        """Parse a document into Documa IR."""

        return parse_document_tool(
            source=source,
            out=out,
            lang=lang,
            pdf_provider=pdf_provider,
            office_provider=office_provider,
        )

    @_documa_tool
    def documa_process(
        source: str,
        out: str | None = None,
        lang: str = "auto",
        max_chars: int = 1200,
        export_formats: list[str] | None = None,
        pdf_provider: str = "auto",
        office_provider: str = "auto",
        keyword_provider: str = "lingxi",
    ) -> dict[str, Any]:
        """Parse and process a document.

        Required input field: source (not input_path). For large documents,
        set out so the full IR is written to disk instead of returned inline.
        The response carries a one-line-per-stage pipeline summary; full stage
        diagnostics are written to pipeline_report_path when out is set.
        """

        return process_document_tool(
            source=source,
            out=out,
            lang=lang,
            max_chars=max_chars,
            export_formats=export_formats,
            pdf_provider=pdf_provider,
            office_provider=office_provider,
            keyword_provider=keyword_provider,
        )

    @_documa_tool
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

    @_documa_tool
    def documa_export(
        ir_path: str,
        format: str = "json",
        out: str | None = None,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        """Export Documa IR as JSON, Markdown, RAG JSON, or block JSON."""

        return export_document_tool(ir_path=ir_path, format=format, out=out, max_chars=max_chars)

    @_documa_tool
    def documa_inspect(ir_path: str) -> dict[str, Any]:
        """Inspect a Documa IR file."""

        return inspect_document_tool(ir_path=ir_path)

    @_documa_tool
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
        pdf_provider: str = "auto",
        office_provider: str = "auto",
        keyword_provider: str = "lingxi",
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
            pdf_provider=pdf_provider,
            office_provider=office_provider,
            keyword_provider=keyword_provider,
        )

    @_documa_tool
    def documa_list_blocks(
        ir_path: str,
        depth: int | None = None,
        parent_id: str | None = None,
        include_metadata_summary: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List progressive document blocks without full block bodies."""

        return list_blocks_tool(
            ir_path=ir_path,
            depth=depth,
            parent_id=parent_id,
            include_metadata_summary=include_metadata_summary,
            limit=limit,
            offset=offset,
        )

    @_documa_tool
    def documa_inspect_block(ir_path: str, block_id: str) -> dict[str, Any]:
        """Inspect metadata for one progressive document block."""

        return inspect_block_tool(ir_path=ir_path, block_id=block_id)

    @_documa_tool
    def documa_read_block(
        ir_path: str,
        block_id: str,
        include_children: bool = False,
        max_chars: int | None = None,
        max_tokens: int | None = None,
        start: int = 0,
    ) -> dict[str, Any]:
        """Read one selected document block body."""

        return read_block_tool(
            ir_path=ir_path,
            block_id=block_id,
            include_children=include_children,
            max_chars=max_chars,
            max_tokens=max_tokens,
            start=start,
        )

    @_documa_tool
    def documa_read_blocks(
        ir_path: str,
        block_ids: list[str],
        total_max_tokens: int = 800,
        per_block_max_tokens: int | None = None,
        context_mode: str = "none",
    ) -> dict[str, Any]:
        """Batch-read evidence blocks under one exact shared token budget."""

        return read_blocks_tool(
            ir_path=ir_path,
            block_ids=block_ids,
            total_max_tokens=total_max_tokens,
            per_block_max_tokens=per_block_max_tokens,
            context_mode=context_mode,
        )


    @_documa_tool
    def documa_search_blocks(
        ir_path: str,
        query: str = "",
        limit: int = 10,
        offset: int = 0,
        any_of: list[str] | None = None,
        fields: list[str] | None = None,
        snippet_fields: list[str] | None = None,
        verbosity: str | None = None,
        include_snippets: bool = True,
        max_snippets_per_block: int = 2,
        search_body: bool = True,
        context_chars: int = 24,
        context_words: int = 8,
        max_response_tokens: int | None = None,
        response_profile: str = "nav",
        granularity: str = "auto",
        scope_block_id: str | None = None,
        max_evidence_tokens: int | None = None,
        adaptive_top_k: bool = True,
        max_results_per_branch: int | None = None,
    ) -> dict[str, Any]:
        """Search progressive document blocks with bounded snippets."""

        return search_blocks_tool(
            ir_path=ir_path,
            query=query,
            limit=limit,
            offset=offset,
            any_of=any_of,
            fields=fields,
            snippet_fields=snippet_fields,
            verbosity=verbosity,
            include_snippets=include_snippets,
            max_snippets_per_block=max_snippets_per_block,
            search_body=search_body,
            context_chars=context_chars,
            context_words=context_words,
            max_response_tokens=max_response_tokens,
            response_profile=response_profile,
            granularity=granularity,
            scope_block_id=scope_block_id,
            max_evidence_tokens=max_evidence_tokens,
            adaptive_top_k=adaptive_top_k,
            max_results_per_branch=max_results_per_branch,
        )

    @_documa_tool
    def documa_ingest(
        source: str,
        store_dir: str = ".documa",
        lang: str = "auto",
        max_chars: int = 1200,
        ocr: bool = False,
        update_index: bool = True,
        pdf_provider: str = "auto",
        office_provider: str = "auto",
        keyword_provider: str = "lingxi",
    ) -> dict[str, Any]:
        """Ingest a document into the local store; returns a stable doc- registry id and keeps the collection index fresh."""

        return ingest_document_tool(
            source=source,
            store_dir=store_dir,
            lang=lang,
            max_chars=max_chars,
            ocr=ocr,
            update_index=update_index,
            pdf_provider=pdf_provider,
            office_provider=office_provider,
            keyword_provider=keyword_provider,
        )

    @_documa_tool
    def documa_index_collection(store_dir: str = ".documa", collection_id: str = "default") -> dict[str, Any]:
        """Rebuild the local collection search index (repair path; ingest maintains it incrementally)."""

        return index_collection_tool(store_dir=store_dir, collection_id=collection_id)

    @_documa_tool
    def documa_search_collection(
        query: str,
        store_dir: str = ".documa",
        collection_id: str = "default",
        limit: int = 20,
        offset: int = 0,
        per_document_limit: int | None = None,
        document_ids: list[str] | None = None,
        group_by_document: bool = False,
        response_profile: str = "nav",
        max_response_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Search active registry documents through the local collection index."""

        return search_collection_tool(
            query=query,
            store_dir=store_dir,
            collection_id=collection_id,
            limit=limit,
            offset=offset,
            per_document_limit=per_document_limit,
            document_ids=document_ids,
            group_by_document=group_by_document,
            response_profile=response_profile,
            max_response_tokens=max_response_tokens,
        )

    @_documa_tool
    def documa_block_tree(
        ir_path: str,
        max_depth: int | None = None,
        max_nodes: int = 500,
        include_citations: bool = False,
        include_sketches: bool = False,
    ) -> dict[str, Any]:
        """Return the progressive document block hierarchy (the document outline).

        include_sketches=true attaches a precomputed one-glance summary and
        read cost per section — the cheapest way to get a document overview.
        """

        return block_tree_tool(
            ir_path=ir_path,
            max_depth=max_depth,
            max_nodes=max_nodes,
            include_citations=include_citations,
            include_sketches=include_sketches,
        )

    @_documa_tool
    def documa_block_xref(ir_path: str, block_id: str) -> dict[str, Any]:
        """Return references around one progressive document block."""

        return block_xref_tool(ir_path=ir_path, block_id=block_id)

    @_documa_tool
    def documa_cite_block(ir_path: str, block_id: str, style: str = "page-bbox") -> dict[str, Any]:
        """Build a stable citation record with a bounded excerpt for one block."""

        return cite_block_tool(ir_path=ir_path, block_id=block_id, style=style)

    @_documa_tool
    def documa_render_citation(ir_path: str, ref_id: str, style: str = "page-bbox") -> dict[str, Any]:
        """Render a citation string for a block or chunk reference."""

        return render_citation_tool(ir_path=ir_path, ref_id=ref_id, style=style)

    @_documa_tool
    def documa_source_window(ir_path: str, block_id: str, before: int = 1, after: int = 1) -> dict[str, Any]:
        """Read neighbor blocks around a hit in reading order, without another search."""

        return source_window_tool(ir_path=ir_path, block_id=block_id, before=before, after=after)

    @_documa_tool
    def documa_verify_citations(ir_path: str, block_ids: list[str] | str) -> dict[str, Any]:
        """Verify that cited block ids exist and their excerpts match source text."""

        return verify_citations_tool(ir_path=ir_path, block_ids=block_ids)

    @_documa_tool
    def documa_list_documents(store_dir: str = ".documa") -> dict[str, Any]:
        """List registry documents so collection read_refs can be resolved."""

        return list_documents_tool(store_dir=store_dir)

    @_documa_tool
    def documa_load_skill(
        task: str,
        skill_names: list[str] | None = None,
        max_tokens: int = 3000,
        max_skills: int = 3,
        store_dir: str = ".documa",
        refresh: bool = False,
        render: bool = True,
    ) -> dict[str, Any]:
        """Load a deterministic, graph-aware skill bundle under a real token budget."""

        return load_skill_tool(
            task=task,
            skill_names=skill_names,
            max_tokens=max_tokens,
            max_skills=max_skills,
            store_dir=store_dir,
            refresh=refresh,
            render=render,
        )

    @_documa_tool
    def documa_read_skill_resource(
        skill_id: str,
        resource_path: str,
        block_ids: list[str] | None = None,
        max_tokens: int = 1200,
        cursor: int = 0,
        store_dir: str = ".documa",
    ) -> dict[str, Any]:
        """Read a selected text reference without executing scripts or loading assets."""

        return read_skill_resource_tool(
            skill_id=skill_id,
            resource_path=resource_path,
            block_ids=block_ids,
            max_tokens=max_tokens,
            cursor=cursor,
            store_dir=store_dir,
        )

    @_documa_tool
    def documa_sync_skills(roots: list[dict[str, Any]] | None = None, store_dir: str = ".documa") -> dict[str, Any]:
        """Synchronize explicitly configured trusted local skill roots."""

        return sync_skills_tool(roots=roots, store_dir=store_dir)

    @_documa_tool
    def documa_skill_status(store_dir: str = ".documa") -> dict[str, Any]:
        """Report skill catalog lifecycle and index health."""

        return skill_status_tool(store_dir=store_dir)

    @_documa_tool
    def documa_inspect_skill_graph(
        skill_id: str | None = None,
        limit: int = 100,
        cursor: int = 0,
        store_dir: str = ".documa",
    ) -> dict[str, Any]:
        """Inspect the global skill catalog or one compiled skill graph."""

        return inspect_skill_graph_tool(skill_id=skill_id, limit=limit, cursor=cursor, store_dir=store_dir)

    @_documa_tool
    def documa_benchmark(
        manifest_path: str = "fixtures/pdf/manifest.json",
        fixtures_dir: str = "fixtures/pdf",
        out: str | None = None,
        require_files: bool = False,
        mode: str = "readiness",
        gold_dir: str = "fixtures/pdf/gold",
        quality_threshold: float = 0.85,
        pdf_provider: str = "auto",
        keyword_provider: str = "lingxi",
    ) -> dict[str, Any]:
        """Run the Documa fixture benchmark."""

        return benchmark_tool(
            manifest_path=manifest_path,
            fixtures_dir=fixtures_dir,
            out=out,
            require_files=require_files,
            mode=mode,
            gold_dir=gold_dir,
            quality_threshold=quality_threshold,
            pdf_provider=pdf_provider,
            keyword_provider=keyword_provider,
        )

    @_documa_tool
    def documa_doctor(
        project_root: str = ".",
        include_benchmark: bool = True,
        store_dir: str | None = None,
    ) -> dict[str, Any]:
        """Run Documa environment diagnostics."""

        return doctor_tool(project_root=project_root, include_benchmark=include_benchmark, store_dir=store_dir)

    for tool_name in sorted(_MCP_REGISTERED_TOOLS - allowed):
        mcp.remove_tool(tool_name)
    mcp.documa_profile = active_profile

    return mcp


def _stdin_pipe_broken_windows() -> bool:
    """Non-consuming probe: has the host's end of the stdin pipe gone away?

    PeekNamedPipe never removes data, so it is safe to call while the MCP
    transport is concurrently reading stdin.
    """
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    available = ctypes.c_ulong(0)
    ok = kernel32.PeekNamedPipe(ctypes.c_void_p(handle), None, 0, None, ctypes.byref(available), None)
    return not ok


def install_stdio_exit_watchdog(poll_seconds: float = 2.0) -> bool:
    """Exit the process when the stdio host disappears (orphan guard).

    MCP-over-stdio contract: the server's lifetime is bounded by the client
    that spawned it. On abrupt host termination the transport read loop does
    not always observe EOF (seen on Windows), stranding documa-mcp processes
    that keep serving the code they were started with and lock the console
    script against reinstalls. The watchdog detects the dead peer without
    consuming transport bytes: PeekNamedPipe on Windows, POLLHUP/POLLERR on
    POSIX. Returns False when stdin is not a pipe (interactive/manual runs),
    in which case no watchdog is installed.
    """
    try:
        stdin_fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return False

    if os.name == "nt":
        import ctypes
        import msvcrt

        FILE_TYPE_PIPE = 3
        handle = msvcrt.get_osfhandle(stdin_fd)
        if ctypes.windll.kernel32.GetFileType(ctypes.c_void_p(handle)) != FILE_TYPE_PIPE:
            return False

        def watch() -> None:
            while not _stdin_pipe_broken_windows():
                time.sleep(poll_seconds)
            os._exit(0)

    else:
        import select

        if not hasattr(select, "poll"):  # pragma: no cover - exotic platforms
            return False
        poller = select.poll()
        # Event mask 0: POLLHUP/POLLERR are always reported, POLLIN never is,
        # so normal transport traffic cannot trip the watchdog.
        poller.register(stdin_fd, 0)

        def watch() -> None:
            while True:
                events = poller.poll(int(poll_seconds * 1000))
                if any(flags & (select.POLLHUP | select.POLLERR) for _, flags in events):
                    os._exit(0)

    threading.Thread(target=watch, daemon=True, name="documa-mcp-stdio-watchdog").start()
    return True


def main() -> None:
    from documa.interfaces.mcp_lifecycle import start_install_shutdown_watchdog

    registration = start_install_shutdown_watchdog()
    if registration is None:
        raise SystemExit("A Documa install is in progress; refusing to start documa-mcp.")
    try:
        install_stdio_exit_watchdog()
        create_mcp_server().run()
    finally:
        registration.close()


if __name__ == "__main__":
    main()
