"""Documa command line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from documa import __version__
from documa.interfaces import (
    benchmark_tool,
    doctor_tool,
    export_document_tool,
    inspect_document_tool,
    list_documa_tools,
    parse_document_tool,
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

    export_cmd = subparsers.add_parser("export", help="Export a Documa IR document.")
    export_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
    export_cmd.add_argument("--format", choices=["json", "markdown", "rag-json"], default="json")
    export_cmd.add_argument("--out", help="Output file path.")
    export_cmd.add_argument("--max-chars", type=int, default=1200, help="Target max characters per generated chunk.")

    inspect_cmd = subparsers.add_parser("inspect", help="Inspect a Documa IR document.")
    inspect_cmd.add_argument("ir_path", help="Path to documa.ir.json.")
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
