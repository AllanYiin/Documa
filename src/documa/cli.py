"""Documa command line entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from documa import __version__
from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.errors import DocumaError
from documa.core.ir import to_plain_data


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

    subparsers.add_parser("inspect", help="Inspect a Documa IR document.")
    subparsers.add_parser("benchmark", help="Run Documa benchmark fixtures.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        return _emit_json({"documa_version": __version__})

    if args.command == "parse":
        output_dir = Path(args.out) if args.out else None
        asset_dir = output_dir / "assets" if output_dir else None
        languages = [part.strip() for part in args.lang.split(",") if part.strip()]
        try:
            document = PyMuPDFAdapter().parse(
                args.source,
                ParseOptions(
                    languages=languages or ["auto"],
                    asset_dir=asset_dir,
                    metadata={"progress": args.progress},
                ),
            )
        except DocumaError as exc:
            return _emit_json(exc.to_dict(), exit_code=1)

        payload = to_plain_data(document)
        output_path = None
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "documa.ir.json"
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )

        return _emit_json(
            {
                "status": "ok",
                "document_id": document.id,
                "page_count": document.page_count,
                "parser": document.parser,
                "output_path": str(output_path) if output_path else None,
                "document": None if output_path else payload,
            }
        )

    if args.command in {"export", "inspect", "benchmark"}:
        return _emit_json(
            {
                "status": "not_implemented",
                "message": f"The '{args.command}' command is reserved by the Stage 0 CLI skeleton.",
            },
            exit_code=2,
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
