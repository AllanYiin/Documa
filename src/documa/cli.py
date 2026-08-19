"""Documa command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from documa import __version__
from documa.core.ir import to_plain_data
from documa.demo import run_block_reading_demo
from documa.interfaces import (
    benchmark_tool,
    block_tree_tool,
    block_xref_tool,
    cite_block_tool,
    cite_chunk_tool,
    delete_document_tool,
    doctor_tool,
    index_collection_tool,
    ingest_document_tool,
    list_documents_tool,
    export_document_tool,
    inspect_block_tool,
    inspect_document_tool,
    inspect_store_tool,
    inspect_skill_graph_tool,
    ingest_mailbox_tool,
    list_blocks_tool,
    list_documa_tools,
    load_skill_tool,
    parse_document_tool,
    process_document_tool,
    read_block_tool,
    read_blocks_tool,
    read_skill_resource_tool,
    search_blocks_tool,
    search_collection_tool,
    source_window_tool,
    skill_status_tool,
    sync_skills_tool,
    view_document_tool,
)


def _emit_json(data: dict[str, Any], *, exit_code: int = 0) -> int:
    sys.stdout.write(json.dumps(to_plain_data(data), ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="documa", description="LLM-ready document understanding package."
    )
    parser.add_argument("--version", action="store_true", help="Show Documa version.")

    subparsers = parser.add_subparsers(dest="command")

    parse_cmd = subparsers.add_parser("parse", help="Parse a document into Documa IR.")
    parse_cmd.add_argument("source", help="Path to the source document.")
    parse_cmd.add_argument("--out", help="Output directory.")
    parse_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    parse_cmd.add_argument("--progress", choices=["text", "jsonl"], default="text")
    parse_cmd.add_argument("--pdf-provider", choices=["auto", "rust", "pymupdf"], default="auto")
    parse_cmd.add_argument("--office-provider", choices=["auto", "rust", "python"], default="auto")

    process_cmd = subparsers.add_parser("process", help="Parse and run the default Documa pipeline.")
    process_cmd.add_argument("source", help="Path to the source document.")
    process_cmd.add_argument("--out", help="Output directory.")
    process_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    process_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")
    process_cmd.add_argument("--ocr", action="store_true", help="Run OCR on image-only pages and embedded images (requires documa[all]).")
    process_cmd.add_argument("--pdf-provider", choices=["auto", "rust", "pymupdf"], default="auto")
    process_cmd.add_argument("--office-provider", choices=["auto", "rust", "python"], default="auto")
    process_cmd.add_argument("--keyword-provider", choices=["lingxi", "ngram"], default="lingxi")
    process_cmd.add_argument(
        "--export-format",
        action="append",
        choices=["json", "markdown", "rag-json", "block-json"],
        dest="export_formats",
        help="Additional export format to write when --out is provided. Can be repeated.",
    )

    mailbox_cmd = subparsers.add_parser("ingest-mailbox", help="Ingest a directory of .eml/.msg messages.")
    mailbox_cmd.add_argument("source", help="Path to a directory containing email messages.")
    mailbox_cmd.add_argument("--out", required=True, help="Output directory for the mailbox manifest and message IRs.")
    mailbox_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    mailbox_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")
    mailbox_cmd.add_argument(
        "--export-format",
        action="append",
        choices=["json", "markdown", "rag-json", "block-json"],
        dest="export_formats",
        help="Export format for each message. Defaults to rag-json and block-json. Can be repeated.",
    )
    mailbox_cmd.add_argument("--recursive", action="store_true", help="Recursively scan nested folders.")
    mailbox_cmd.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first message that fails to parse or process.",
    )
    mailbox_cmd.add_argument("--progress", choices=["text", "jsonl"], default="text")

    export_cmd = subparsers.add_parser("export", help="Export a Documa IR document.")
    export_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    export_cmd.add_argument("--format", choices=["json", "markdown", "rag-json", "block-json"], default="json")
    export_cmd.add_argument("--out", help="Output file path.")
    export_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")

    inspect_cmd = subparsers.add_parser("inspect", help="Inspect a Documa IR document.")
    inspect_cmd.add_argument("ir_path", help="Path to documa.ir.json.")

    view_cmd = subparsers.add_parser("view", help="Build a universal human viewer for any Documa-supported document.")
    view_cmd.add_argument("target", help="Source document path, or documa.ir.json when --from-ir is set.")
    view_cmd.add_argument("--from-ir", action="store_true", help="Read target as an existing Documa IR JSON file.")
    view_cmd.add_argument("--out", help="Output file path.")
    view_cmd.add_argument("--format", choices=["json", "markdown", "html"], default="json")
    view_cmd.add_argument("--query", default="", help="Optional lexical query to highlight matching blocks.")
    view_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints when target is a source document.")
    view_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")
    view_cmd.add_argument("--max-depth", type=int, help="Maximum hierarchy depth to include.")
    view_cmd.add_argument("--include-body", action="store_true", help="Include bounded block body text in the viewer payload.")
    view_cmd.add_argument("--body-chars", type=int, default=1200, help="Maximum body characters per block when included.")
    view_cmd.add_argument("--result-limit", type=int, default=10, help="Maximum query result count.")
    view_cmd.add_argument("--pdf-provider", choices=["auto", "rust", "pymupdf"], default="auto")
    view_cmd.add_argument("--office-provider", choices=["auto", "rust", "python"], default="auto")
    view_cmd.add_argument("--keyword-provider", choices=["lingxi", "ngram"], default="lingxi")

    blocks_cmd = subparsers.add_parser("blocks", help="List Documa document blocks.")
    blocks_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    blocks_cmd.add_argument("--depth", type=int, help="Maximum block depth to return.")
    blocks_cmd.add_argument("--parent-id", help="Only return direct children of this block id.")
    blocks_cmd.add_argument("--no-metadata-summary", action="store_true", help="Omit keyword metadata summaries.")
    blocks_cmd.add_argument("--limit", type=int, default=None, help="Maximum blocks to return (paging).")
    blocks_cmd.add_argument("--offset", type=int, default=0, help="Skip this many blocks (paging).")

    block_cmd = subparsers.add_parser("block", help="Inspect or read a Documa document block.")
    block_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    block_cmd.add_argument("--id", required=True, dest="block_id", help="Document block id.")
    block_cmd.add_argument("--read", action="store_true", help="Read block body instead of metadata.")
    block_cmd.add_argument("--include-children", action="store_true", help="Include descendant block bodies when reading.")
    block_cmd.add_argument("--max-chars", type=int, help="Limit returned body text.")
    block_cmd.add_argument("--max-tokens", type=int, help="Limit returned body to an estimated token budget (CJK-aware).")
    block_cmd.add_argument("--start", type=int, default=0, help="Continuation character cursor.")

    read_blocks_cmd = subparsers.add_parser("read-blocks", help="Batch-read evidence blocks under one token budget.")
    read_blocks_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    read_blocks_cmd.add_argument("--id", action="append", required=True, dest="block_ids", help="Block id; repeatable.")
    read_blocks_cmd.add_argument("--total-max-tokens", type=int, default=800)
    read_blocks_cmd.add_argument("--per-block-max-tokens", type=int, default=None)
    read_blocks_cmd.add_argument("--context-mode", choices=["none", "auto", "neighbors", "children"], default="none")


    search_blocks_cmd = subparsers.add_parser("search-blocks", help="Search Documa document blocks.")
    search_blocks_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    search_blocks_cmd.add_argument("--query", required=True, help="Lexical query.")
    search_blocks_cmd.add_argument("--limit", type=int, default=10, help="Maximum result count.")
    search_blocks_cmd.add_argument("--offset", type=int, default=0, help="Skip this many ranked results (paging).")
    search_blocks_cmd.add_argument("--term", action="append", dest="any_of", help="Additional OR search term.")
    search_blocks_cmd.add_argument("--field", action="append", dest="fields", help="Restrict searched fields. Repeatable.")
    search_blocks_cmd.add_argument("--snippet-field", action="append", dest="snippet_fields", help="Field allowed to produce snippets. Repeatable.")
    search_blocks_cmd.add_argument("--response-profile", choices=["nav", "evidence", "debug"], default="nav", help="Search response profile; nav is the smallest routing-only shape.")
    search_blocks_cmd.add_argument("--verbosity", choices=["compact", "standard", "debug"], default=None, help="Deprecated legacy result detail alias.")
    search_blocks_cmd.add_argument("--no-snippets", action="store_true", help="Return matches without snippet context.")
    search_blocks_cmd.add_argument("--no-body", action="store_true", help="Do not search full block body text.")
    search_blocks_cmd.add_argument("--max-snippets-per-block", type=int, default=2, help="Maximum snippets per result block.")
    search_blocks_cmd.add_argument("--context-chars", type=int, default=24, help="CJK characters around snippet matches.")
    search_blocks_cmd.add_argument("--context-words", type=int, default=8, help="ASCII words around snippet matches.")
    search_blocks_cmd.add_argument("--max-response-tokens", type=int, default=None, help="Hard ceiling on estimated response tokens; lowest-ranked rows drop first.")
    search_blocks_cmd.add_argument("--granularity", choices=["auto", "section", "leaf", "mixed"], default="auto")
    search_blocks_cmd.add_argument("--scope-block-id", default=None)
    search_blocks_cmd.add_argument("--max-evidence-tokens", type=int, default=None)
    search_blocks_cmd.add_argument("--no-adaptive-top-k", action="store_true")
    search_blocks_cmd.add_argument("--max-results-per-branch", type=int, default=None)

    index_collection_cmd = subparsers.add_parser("index-collection", help="Build the local collection search index.")
    index_collection_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")
    index_collection_cmd.add_argument("--collection-id", default="default", help="Collection identifier.")

    search_collection_cmd = subparsers.add_parser("search-collection", help="Search all active documents in the local collection index.")
    search_collection_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")
    search_collection_cmd.add_argument("--collection-id", default="default", help="Collection identifier.")
    search_collection_cmd.add_argument("--query", required=True, help="Lexical query.")
    search_collection_cmd.add_argument("--limit", type=int, default=20, help="Maximum result count.")
    search_collection_cmd.add_argument("--offset", type=int, default=0, help="Skip this many results (paging).")
    search_collection_cmd.add_argument("--per-document-limit", type=int, default=None, help="Maximum results per document.")
    search_collection_cmd.add_argument("--document-id", action="append", dest="document_ids", help="Restrict to this registry document id. Repeatable.")
    search_collection_cmd.add_argument("--group-by-document", action="store_true", help="Return document-level rollups instead of a flat block list.")
    search_collection_cmd.add_argument("--response-profile", choices=["nav", "evidence"], default="nav")
    search_collection_cmd.add_argument("--max-response-tokens", type=int, default=None, help="Hard ceiling on counted response tokens; lowest-ranked rows drop first.")

    block_tree_cmd = subparsers.add_parser("block-tree", help="Return the Documa document block tree (outline).")
    block_tree_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    block_tree_cmd.add_argument("--max-depth", type=int, default=None, help="Collapse subtrees below this depth to children_count.")
    block_tree_cmd.add_argument("--max-nodes", type=int, default=500, help="Maximum nodes emitted before truncation.")
    block_tree_cmd.add_argument("--citations", action="store_true", help="Add per-node page citation labels (omitted by default).")
    block_tree_cmd.add_argument("--no-citations", action="store_true", help=argparse.SUPPRESS)  # deprecated: lean default
    block_tree_cmd.add_argument(
        "--include-sketches",
        action="store_true",
        help="Attach precomputed per-section sketches and read costs from the search sidecar.",
    )

    block_xref_cmd = subparsers.add_parser("block-xref", help="Return parent, children, source, and relation refs.")
    block_xref_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    block_xref_cmd.add_argument("--id", required=True, dest="block_id", help="Document block id.")

    demo_cmd = subparsers.add_parser("block-demo", help="Run a block-based reading trace demo for a PDF.")
    demo_cmd.add_argument("source", help="Path to the source PDF.")
    demo_cmd.add_argument("--question", required=True, help="Question to answer through block-based reading.")
    demo_cmd.add_argument("--out", help="Output directory for IR, blocks, and trace JSON.")
    demo_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    demo_cmd.add_argument("--top-k", type=int, default=3, help="Number of metadata-selected blocks to read.")
    demo_cmd.add_argument(
        "--max-chars-per-block",
        type=int,
        default=2000,
        help="Maximum body characters loaded for each selected block.",
    )

    tools_cmd = subparsers.add_parser("tools", help="List Documa tool-calling schemas.")
    tools_cmd.add_argument("--profile", choices=["agent", "advanced", "admin"], default="admin")

    skills_cmd = subparsers.add_parser("skills", help="Configure, compile, load, and inspect dynamic Agent Skills.")
    skills_subparsers = skills_cmd.add_subparsers(dest="skills_command", required=True)
    skill_root_cmd = skills_subparsers.add_parser("root-add", help="Add or update one trusted local skill root.")
    skill_root_cmd.add_argument("root_id", help="Stable lowercase root identifier.")
    skill_root_cmd.add_argument("path", help="Local directory containing skill folders.")
    skill_root_cmd.add_argument("--priority", type=int, default=0)
    skill_root_cmd.add_argument("--disabled", action="store_true")
    skill_root_cmd.add_argument("--untrusted", action="store_true")
    skill_root_cmd.add_argument(
        "--allow-native-scan-overlap",
        action="store_true",
        help="Explicitly allow this trusted root to overlap Codex/shared native skill scan paths.",
    )
    skill_root_cmd.add_argument("--store-dir", default=".documa")

    skill_sync_cmd = skills_subparsers.add_parser("sync", help="Incrementally compile configured skill roots.")
    skill_sync_cmd.add_argument("--store-dir", default=".documa")

    skill_load_cmd = skills_subparsers.add_parser("load", help="Materialize a bounded skill bundle for a task.")
    skill_load_cmd.add_argument("task", help="Task description used for deterministic routing.")
    skill_load_cmd.add_argument("--skill", action="append", dest="skill_names", help="Exact skill name or qualified root:name; repeatable.")
    skill_load_cmd.add_argument("--max-tokens", type=int, default=3000)
    skill_load_cmd.add_argument("--max-skills", type=int, default=3)
    skill_load_cmd.add_argument("--store-dir", default=".documa")
    skill_load_cmd.add_argument("--refresh", action="store_true")
    skill_load_cmd.add_argument("--no-render", action="store_true")

    skill_read_cmd = skills_subparsers.add_parser("read-resource", help="Read a selected indexed text resource.")
    skill_read_cmd.add_argument("skill_id")
    skill_read_cmd.add_argument("resource_path")
    skill_read_cmd.add_argument("--block-id", action="append", dest="block_ids")
    skill_read_cmd.add_argument("--max-tokens", type=int, default=1200)
    skill_read_cmd.add_argument("--cursor", type=int, default=0)
    skill_read_cmd.add_argument("--store-dir", default=".documa")

    skill_status_cmd = skills_subparsers.add_parser("status", help="Report dynamic skill store health.")
    skill_status_cmd.add_argument("--store-dir", default=".documa")
    skill_graph_cmd = skills_subparsers.add_parser("graph", help="Inspect the catalog or one skill graph.")
    skill_graph_cmd.add_argument("--skill-id")
    skill_graph_cmd.add_argument("--limit", type=int, default=100)
    skill_graph_cmd.add_argument("--cursor", type=int, default=0)
    skill_graph_cmd.add_argument("--store-dir", default=".documa")
    benchmark_cmd = subparsers.add_parser("benchmark", help="Run Documa benchmark fixtures.")
    benchmark_cmd.add_argument("--manifest", default="fixtures/pdf/manifest.json", help="Path to fixture manifest JSON.")
    benchmark_cmd.add_argument("--fixtures-dir", default="fixtures/pdf", help="Directory containing fixture files.")
    benchmark_cmd.add_argument("--out", help="Output JSON file path.")
    benchmark_cmd.add_argument("--require-files", action="store_true", help="Fail cases with missing fixture files.")
    benchmark_cmd.add_argument("--mode", choices=["readiness", "quality"], default="readiness", help="readiness checks files; quality scores against gold annotations.")
    benchmark_cmd.add_argument("--gold-dir", default="fixtures/pdf/gold", help="Directory holding gold expected.partial.json files.")
    benchmark_cmd.add_argument("--quality-threshold", type=float, default=0.85, help="Minimum score for a quality case to pass.")
    benchmark_cmd.add_argument("--pdf-provider", choices=["auto", "rust", "pymupdf"], default="auto")
    benchmark_cmd.add_argument("--keyword-provider", choices=["lingxi", "ngram"], default="lingxi")

    diff_cmd = subparsers.add_parser("diff", help="Structured diff between two Documa IR JSON files.")
    diff_cmd.add_argument("actual", help="Path to the actual IR JSON.")
    diff_cmd.add_argument("expected", help="Path to the expected IR JSON.")

    validate_ir_cmd = subparsers.add_parser("validate-ir", help="Validate an IR JSON file against the Documa schema.")
    validate_ir_cmd.add_argument("ir_path", help="Path to documa.ir.json.")

    ingest_cmd = subparsers.add_parser("ingest", help="Ingest a document into the local store; returns a stable document_id.")
    ingest_cmd.add_argument("source", nargs="?", help="Path to the source document.")
    ingest_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")
    ingest_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    ingest_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")
    ingest_cmd.add_argument("--ocr", action="store_true", help="Run OCR on image-only pages and embedded images (requires documa[all]).")
    ingest_cmd.add_argument("--rebuild-index", action="store_true", help="Rebuild registry.json from stored documents.")
    ingest_cmd.add_argument("--no-update-index", action="store_true", help="Skip the incremental collection index update after ingest.")
    ingest_cmd.add_argument("--pdf-provider", choices=["auto", "rust", "pymupdf"], default="auto")
    ingest_cmd.add_argument("--office-provider", choices=["auto", "rust", "python"], default="auto")
    ingest_cmd.add_argument("--keyword-provider", choices=["lingxi", "ngram"], default="lingxi")

    list_documents_cmd = subparsers.add_parser("list-documents", help="List documents in the local store.")
    list_documents_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")

    delete_document_cmd = subparsers.add_parser("delete-document", help="Delete a document from the local store.")
    delete_document_cmd.add_argument("document_id", help="Registry document id (doc-...).")
    delete_document_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")
    delete_document_cmd.add_argument("--yes", action="store_true", help="Confirm deletion.")
    delete_document_cmd.add_argument("--no-update-index", action="store_true", help="Skip the incremental collection index update after delete.")

    cite_block_cmd = subparsers.add_parser("cite-block", help="Return page/bbox citation for a block id.")
    cite_block_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    cite_block_cmd.add_argument("--id", required=True, dest="block_id", help="Block id (document block or page block).")
    cite_block_cmd.add_argument("--style", choices=["page-bbox", "markdown", "inline"], default="page-bbox")

    cite_chunk_cmd = subparsers.add_parser("cite-chunk", help="Return page/bbox citation for a chunk id.")
    cite_chunk_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    cite_chunk_cmd.add_argument("--id", required=True, dest="chunk_id", help="Chunk id.")
    cite_chunk_cmd.add_argument("--style", choices=["page-bbox", "markdown", "inline"], default="page-bbox")

    source_window_cmd = subparsers.add_parser("source-window", help="Read neighboring blocks around a block id.")
    source_window_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    source_window_cmd.add_argument("--id", required=True, dest="block_id", help="Block id.")
    source_window_cmd.add_argument("--before", type=int, default=1, help="Blocks before the target.")
    source_window_cmd.add_argument("--after", type=int, default=1, help="Blocks after the target.")

    doctor_cmd = subparsers.add_parser("doctor", help="Run Documa environment diagnostics.")
    doctor_cmd.add_argument("--project-root", default=".", help="Project root for local readiness checks.")
    doctor_cmd.add_argument("--no-benchmark", action="store_true", help="Skip fixture benchmark readiness checks.")
    doctor_cmd.add_argument("--store-dir", default=None, help="Also report document store health for this directory.")

    inspect_store_cmd = subparsers.add_parser("inspect-store", help="Report local document store health.")
    inspect_store_cmd.add_argument("--store-dir", default=".documa", help="Document store directory.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        return _emit_json({"documa_version": __version__})

    if args.command == "parse":
        payload = parse_document_tool(
            source=args.source,
            out=args.out,
            lang=args.lang,
            progress=args.progress,
            pdf_provider=args.pdf_provider,
            office_provider=args.office_provider,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "process":
        payload = process_document_tool(
            source=args.source,
            out=args.out,
            lang=args.lang,
            max_chars=args.max_chars,
            export_formats=args.export_formats,
            ocr=args.ocr,
            pdf_provider=args.pdf_provider,
            office_provider=args.office_provider,
            keyword_provider=args.keyword_provider,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "ingest-mailbox":
        payload = ingest_mailbox_tool(
            source=args.source,
            out=args.out,
            lang=args.lang,
            max_chars=args.max_chars,
            export_formats=args.export_formats,
            recursive=args.recursive,
            continue_on_error=not args.fail_fast,
            progress=args.progress,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") in {"ok", "partial"} else 1)

    if args.command == "export":
        payload = export_document_tool(
            ir_path=args.ir_path,
            format=args.format,
            out=args.out,
            max_chars=args.max_chars,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "inspect":
        payload = inspect_document_tool(args.ir_path)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "view":
        payload = view_document_tool(
            source=None if args.from_ir else args.target,
            ir_path=args.target if args.from_ir else None,
            out=args.out,
            format=args.format,
            query=args.query,
            lang=args.lang,
            max_chars=args.max_chars,
            max_depth=args.max_depth,
            include_body=args.include_body,
            body_chars=args.body_chars,
            result_limit=args.result_limit,
            pdf_provider=args.pdf_provider,
            office_provider=args.office_provider,
            keyword_provider=args.keyword_provider,
        )
        if payload.get("status") != "ok" or args.out or args.format == "json":
            return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)
        sys.stdout.write(str(payload.get("content") or ""))
        if not str(payload.get("content") or "").endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if args.command == "blocks":
        payload = list_blocks_tool(
            ir_path=args.ir_path,
            depth=args.depth,
            parent_id=args.parent_id,
            include_metadata_summary=not args.no_metadata_summary,
            limit=args.limit,
            offset=args.offset,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block":
        if args.read:
            payload = read_block_tool(
                ir_path=args.ir_path,
                block_id=args.block_id,
                include_children=args.include_children,
                max_chars=args.max_chars,
                max_tokens=args.max_tokens,
                start=args.start,
            )
        else:
            payload = inspect_block_tool(ir_path=args.ir_path, block_id=args.block_id)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)


    if args.command == "read-blocks":
        payload = read_blocks_tool(
            ir_path=args.ir_path,
            block_ids=args.block_ids,
            total_max_tokens=args.total_max_tokens,
            per_block_max_tokens=args.per_block_max_tokens,
            context_mode=args.context_mode,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)
    if args.command == "search-blocks":
        payload = search_blocks_tool(
            ir_path=args.ir_path,
            query=args.query,
            limit=args.limit,
            offset=args.offset,
            any_of=args.any_of,
            fields=args.fields,
            snippet_fields=args.snippet_fields,
            verbosity=args.verbosity,
            response_profile=args.response_profile,
            granularity=args.granularity,
            scope_block_id=args.scope_block_id,
            max_evidence_tokens=args.max_evidence_tokens,
            adaptive_top_k=not args.no_adaptive_top_k,
            max_results_per_branch=args.max_results_per_branch,
            include_snippets=not args.no_snippets,
            max_snippets_per_block=args.max_snippets_per_block,
            search_body=not args.no_body,
            context_chars=args.context_chars,
            context_words=args.context_words,
            max_response_tokens=args.max_response_tokens,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "index-collection":
        payload = index_collection_tool(store_dir=args.store_dir, collection_id=args.collection_id)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "search-collection":
        payload = search_collection_tool(
            store_dir=args.store_dir,
            collection_id=args.collection_id,
            query=args.query,
            limit=args.limit,
            offset=args.offset,
            per_document_limit=args.per_document_limit,
            document_ids=args.document_ids,
            group_by_document=args.group_by_document,
            response_profile=args.response_profile,
            max_response_tokens=args.max_response_tokens,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block-tree":
        payload = block_tree_tool(
            ir_path=args.ir_path,
            max_depth=args.max_depth,
            max_nodes=args.max_nodes,
            include_citations=args.citations,
            include_sketches=args.include_sketches,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block-xref":
        payload = block_xref_tool(ir_path=args.ir_path, block_id=args.block_id)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block-demo":
        payload = run_block_reading_demo(
            source=args.source,
            question=args.question,
            out=args.out,
            lang=args.lang,
            top_k=args.top_k,
            max_chars_per_block=args.max_chars_per_block,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "tools":
        return _emit_json({"status": "ok", "profile": args.profile, "tools": list_documa_tools(profile=args.profile)})

    if args.command == "skills":
        if args.skills_command == "root-add":
            from documa.skills import add_skill_root

            try:
                payload = add_skill_root(
                    args.root_id,
                    args.path,
                    priority=args.priority,
                    enabled=not args.disabled,
                    trusted=not args.untrusted,
                    allow_native_scan_overlap=args.allow_native_scan_overlap,
                    store_dir=args.store_dir,
                )
            except (OSError, ValueError) as exc:
                return _emit_json({"status": "error", "code": "SKILL_CONFIG_INVALID", "message": str(exc)}, exit_code=1)
            return _emit_json({"status": "ok", **payload})
        if args.skills_command == "sync":
            payload = sync_skills_tool(store_dir=args.store_dir)
        elif args.skills_command == "load":
            payload = load_skill_tool(
                task=args.task,
                skill_names=args.skill_names,
                max_tokens=args.max_tokens,
                max_skills=args.max_skills,
                store_dir=args.store_dir,
                refresh=args.refresh,
                render=not args.no_render,
            )
        elif args.skills_command == "read-resource":
            payload = read_skill_resource_tool(
                skill_id=args.skill_id,
                resource_path=args.resource_path,
                block_ids=args.block_ids,
                max_tokens=args.max_tokens,
                cursor=args.cursor,
                store_dir=args.store_dir,
            )
        elif args.skills_command == "status":
            payload = skill_status_tool(store_dir=args.store_dir)
        else:
            payload = inspect_skill_graph_tool(
                skill_id=args.skill_id,
                limit=args.limit,
                cursor=args.cursor,
                store_dir=args.store_dir,
            )
        return _emit_json(payload, exit_code=0 if payload.get("status") in {"ok", "warning"} else 1)

    if args.command == "benchmark":
        payload = benchmark_tool(
            manifest_path=args.manifest,
            fixtures_dir=args.fixtures_dir,
            out=args.out,
            require_files=args.require_files,
            mode=args.mode,
            gold_dir=args.gold_dir,
            quality_threshold=args.quality_threshold,
            pdf_provider=args.pdf_provider,
            keyword_provider=args.keyword_provider,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "diff":
        from documa.quality.ir_diff import diff_documents

        try:
            actual = json.loads(Path(args.actual).read_text(encoding="utf-8"))
            expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _emit_json({"status": "error", "message": f"Cannot read IR JSON: {exc}"}, exit_code=1)
        payload = diff_documents(actual, expected)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "ingest":
        if args.rebuild_index:
            from documa.collections.registry import rebuild_index

            payload = rebuild_index(store_dir=args.store_dir)
            return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)
        if not args.source:
            return _emit_json({"status": "error", "message": "ingest requires a source path (or --rebuild-index)."}, exit_code=1)
        payload = ingest_document_tool(
            source=args.source,
            store_dir=args.store_dir,
            lang=args.lang,
            max_chars=args.max_chars,
            ocr=args.ocr,
            update_index=not args.no_update_index,
            pdf_provider=args.pdf_provider,
            office_provider=args.office_provider,
            keyword_provider=args.keyword_provider,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "list-documents":
        payload = list_documents_tool(store_dir=args.store_dir)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "delete-document":
        payload = delete_document_tool(
            document_id=args.document_id,
            store_dir=args.store_dir,
            yes=args.yes,
            update_index=not args.no_update_index,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "cite-block":
        payload = cite_block_tool(ir_path=args.ir_path, block_id=args.block_id, style=args.style)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "cite-chunk":
        payload = cite_chunk_tool(ir_path=args.ir_path, chunk_id=args.chunk_id, style=args.style)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "source-window":
        payload = source_window_tool(
            ir_path=args.ir_path, block_id=args.block_id, before=args.before, after=args.after
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "validate-ir":
        from documa.core.schema_validation import validate_document_payload

        try:
            payload_data = json.loads(Path(args.ir_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return _emit_json(
                {"status": "error", "ir_path": args.ir_path, "message": f"Cannot read IR JSON: {exc}"},
                exit_code=1,
            )
        result = validate_document_payload(payload_data)
        result.update({"status": "ok" if result["valid"] else "invalid", "ir_path": args.ir_path})
        return _emit_json(result, exit_code=0 if result["valid"] else 1)

    if args.command == "doctor":
        payload = doctor_tool(
            project_root=args.project_root,
            include_benchmark=not args.no_benchmark,
            store_dir=args.store_dir,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") in {"ok", "warning"} else 1)

    if args.command == "inspect-store":
        payload = inspect_store_tool(store_dir=args.store_dir)
        return _emit_json(payload, exit_code=0 if payload.get("status") in {"ok", "warning"} else 1)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
