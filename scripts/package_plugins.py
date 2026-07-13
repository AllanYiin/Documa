"""Package agent plugin zip artifacts deterministically.

The tracked zips are what plugin users install, so they must never drift from
the plugin source directories (this happened once: an installed zip shipped a
stale skill). Zips are built with sorted entries and fixed timestamps, so the
same source tree always produces byte-identical output and ``--check`` can
fail CI when a tracked zip is out of date.

Usage:
    python scripts/package_plugins.py          # rebuild the tracked zips
    python scripts/package_plugins.py --check  # exit 1 if any zip is stale
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

# Plugin directories that ship as tracked zip artifacts (flat layout: the zip
# root contains the plugin files directly, no wrapping folder).
ZIPPED_PLUGINS = ["claude-code-documa"]

# Deterministic metadata: fixed DOS timestamp and regular-file permissions.
_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_SKIP_NAMES = {"__pycache__", ".DS_Store"}


def _plugin_files(plugin_dir: Path) -> list[Path]:
    files = [
        path
        for path in sorted(plugin_dir.rglob("*"))
        if path.is_file() and not (_SKIP_NAMES & set(path.relative_to(plugin_dir).parts))
    ]
    if not files:
        raise SystemExit(f"no files found under {plugin_dir}")
    return files


def build_zip_bytes(plugin_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _plugin_files(plugin_dir):
            relative = path.relative_to(plugin_dir).as_posix()
            info = zipfile.ZipInfo(relative, date_time=_FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify tracked zips match plugin sources; exit 1 on drift.")
    args = parser.parse_args()

    stale: list[str] = []
    for name in ZIPPED_PLUGINS:
        plugin_dir = PLUGINS / name
        zip_path = PLUGINS / f"{name}.zip"
        expected = build_zip_bytes(plugin_dir)
        current = zip_path.read_bytes() if zip_path.exists() else b""
        if args.check:
            if current != expected:
                stale.append(str(zip_path.relative_to(ROOT)))
            continue
        if current != expected:
            zip_path.write_bytes(expected)
            print(f"rebuilt {zip_path.relative_to(ROOT)}")
        else:
            print(f"up to date {zip_path.relative_to(ROOT)}")

    if args.check:
        if stale:
            print(f"plugin zip out of date: {', '.join(stale)} (run: python scripts/package_plugins.py)")
            return 1
        print("plugin zips in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
