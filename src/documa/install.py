"""Guarded Documa installer for upgrades while MCP hosts may be active."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence

from documa.interfaces.mcp_lifecycle import disconnect_mcp_servers, guarded_install


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m documa.install",
        description="Disconnect active Documa MCP servers before installing or upgrading Documa.",
    )
    parser.add_argument("requirement", nargs="?", default="documa", help="Documa requirement passed to pip.")
    parser.add_argument("--upgrade", action="store_true", help="Pass --upgrade to pip.")
    parser.add_argument("--force-reinstall", action="store_true", help="Pass --force-reinstall to pip.")
    parser.add_argument("--user", action="store_true", help="Pass --user to pip.")
    parser.add_argument("--pre", action="store_true", help="Pass --pre to pip.")
    return parser


def _pip_command(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "-m", "pip", "install"]
    for enabled, option in (
        (args.upgrade, "--upgrade"),
        (args.force_reinstall, "--force-reinstall"),
        (args.user, "--user"),
        (args.pre, "--pre"),
    ):
        if enabled:
            command.append(option)
    command.append(args.requirement)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with guarded_install():
        shutdown = disconnect_mcp_servers()
        if not shutdown["disconnected"]:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "mcp_disconnect",
                        "mcp": shutdown,
                        "error": "Active Documa MCP servers could not be disconnected; pip was not started.",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2

        command = _pip_command(args)
        completed = subprocess.run(command, check=False)

    print(
        json.dumps(
            {
                "ok": completed.returncode == 0,
                "stage": "complete" if completed.returncode == 0 else "pip_install",
                "mcp": shutdown,
                "pip_returncode": completed.returncode,
            },
            ensure_ascii=False,
        )
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
