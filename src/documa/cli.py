"""Documa command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from documa import __version__
from documa.demo import run_block_reading_demo
from documa.interfaces import (
    benchmark_tool,
    block_tree_tool,
    block_xref_tool,
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


def _emit_json(data: dict[str, Any], *, exit_code: int = 0) -> int:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="documa",
        description="LLM-ready document understanding package.",
    )
    parser.add_argument("--version", action="store_true", help="Show Documa version.")

    subparsers = parser.add_subparsers(dest="command")

    parse_cmd = subparsers.add_parser("parse", help="Parse a document into Documa IR.")
    parse_cmd.add_argument("source", help="Path to the source document.")
    parse_cmd.add_argument("--out", help="Output directory.")
    parse_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    parse_cmd.add_argument("--progress", choices=["text", "jsonl"], default="text")

    process_cmd = subparsers.add_parser("process", help="Parse and run the default Documa pipeline.")
    process_cmd.add_argument("source", help="Path to the source document.")
    process_cmd.add_argument("--out", help="Output directory.")
    process_cmd.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    process_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")
    process_cmd.add_argument(
        "--export-format",
        action="append",
        choices=["json", "markdown", "rag-json", "block-json"],
        dest="export_formats",
        help="Additional export format to write when --out is provided. Can be repeated.",
    )

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

    blocks_cmd = subparsers.add_parser("blocks", help="List Documa document blocks.")
    blocks_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    blocks_cmd.add_argument("--depth", type=int, help="Maximum block depth to return.")
    blocks_cmd.add_argument("--parent-id", help="Only return direct children of this block id.")
    blocks_cmd.add_argument("--no-metadata-summary", action="store_true", help="Omit keyword metadata summaries.")

    block_cmd = subparsers.add_parser("block", help="Inspect or read a Documa document block.")
    block_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    block_cmd.add_argument("--id", required=True, dest="block_id", help="Document block id.")
    block_cmd.add_argument("--read", action="store_true", help="Read block body instead of metadata.")
    block_cmd.add_argument("--include-children", action="store_true", help="Include descendant block bodies when reading.")
    block_cmd.add_argument("--max-chars", type=int, help="Limit returned body text.")

    search_blocks_cmd = subparsers.add_parser("search-blocks", help="Search Documa document blocks.")
    search_blocks_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    search_blocks_cmd.add_argument("--query", required=True, help="Lexical query.")
    search_blocks_cmd.add_argument("--limit", type=int, default=10, help="Maximum result count.")
    search_blocks_cmd.add_argument("--term", action="append", dest="any_of", help="Additional OR search term.")
    search_blocks_cmd.add_argument("--field", action="append", dest="fields", help="Restrict searched fields. Repeatable.")
    search_blocks_cmd.add_argument("--snippet-field", action="append", dest="snippet_fields", help="Field allowed to produce snippets. Repeatable.")
    search_blocks_cmd.add_argument("--verbosity", choices=["compact", "standard", "debug"], default="compact", help="Search result detail level.")
    search_blocks_cmd.add_argument("--no-snippets", action="store_true", help="Return matches without snippet context.")
    search_blocks_cmd.add_argument("--no-body", action="store_true", help="Do not search full block body text.")
    search_blocks_cmd.add_argument("--max-snippets-per-block", type=int, default=5, help="Maximum snippets per result block.")
    search_blocks_cmd.add_argument("--context-chars", type=int, default=24, help="CJK characters around snippet matches.")
    search_blocks_cmd.add_argument("--context-words", type=int, default=8, help="ASCII words around snippet matches.")

    block_tree_cmd = subparsers.add_parser("block-tree", help="Return the full Documa document block tree.")
    block_tree_cmd.add_argument("ir_path", help="Path to documa.ir.json.")

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

    subparsers.add_parser("tools", help="List Documa tool-calling schemas.")
    benchmark_cmd = subparsers.add_parser("benchmark", help="Run Documa benchmark fixtures.")
    benchmark_cmd.add_argument("--manifest", default="fixtures/pdf/manifest.json", help="Path to fixture manifest JSON.")
    benchmark_cmd.add_argument("--fixtures-dir", default="fixtures/pdf", help="Directory containing fixture files.")
    benchmark_cmd.add_argument("--out", help="Output JSON file path.")
    benchmark_cmd.add_argument("--require-files", action="store_true", help="Fail cases with missing fixture files.")

    doctor_cmd = subparsers.add_parser("doctor", help="Run Documa environment diagnostics.")
    doctor_cmd.add_argument("--project-root", default=".", help="Project root for local readiness checks.")
    doctor_cmd.add_argument("--no-benchmark", action="store_true", help="Skip fixture benchmark readiness checks.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        return _emit_json({"documa_version": __version__})

    if args.command == "parse":
        payload = parse_document_tool(source=args.source, out=args.out, lang=args.lang, progress=args.progress)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "process":
        payload = process_document_tool(
            source=args.source,
            out=args.out,
            lang=args.lang,
            max_chars=args.max_chars,
            export_formats=args.export_formats,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

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
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block":
        if args.read:
            payload = read_block_tool(
                ir_path=args.ir_path,
                block_id=args.block_id,
                include_children=args.include_children,
                max_chars=args.max_chars,
            )
        else:
            payload = inspect_block_tool(ir_path=args.ir_path, block_id=args.block_id)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "search-blocks":
        payload = search_blocks_tool(
            ir_path=args.ir_path,
            query=args.query,
            limit=args.limit,
            any_of=args.any_of,
            fields=args.fields,
            snippet_fields=args.snippet_fields,
            verbosity=args.verbosity,
            include_snippets=not args.no_snippets,
            max_snippets_per_block=args.max_snippets_per_block,
            search_body=not args.no_body,
            context_chars=args.context_chars,
            context_words=args.context_words,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "block-tree":
        payload = block_tree_tool(ir_path=args.ir_path)
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
        return _emit_json({"status": "ok", "tools": list_documa_tools()})

    if args.command == "benchmark":
        payload = benchmark_tool(
            manifest_path=args.manifest,
            fixtures_dir=args.fixtures_dir,
            out=args.out,
            require_files=args.require_files,
        )
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    if args.command == "doctor":
        payload = doctor_tool(project_root=args.project_root, include_benchmark=not args.no_benchmark)
        return _emit_json(payload, exit_code=0 if payload.get("status") == "ok" else 1)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
