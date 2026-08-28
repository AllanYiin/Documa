from __future__ import annotations

import io
import zipfile
from pathlib import Path

from scripts.package_plugins import build_zip_bytes


def _write_plugin(root: Path, *, newline: str) -> None:
    root.mkdir()
    (root / "README.md").write_text(
        f"# Plugin{newline}{newline}Cross-platform archive.{newline}",
        encoding="utf-8",
        newline="",
    )
    (root / "manifest.json").write_text(
        f'{{{newline}  "name": "documa"{newline}}}{newline}',
        encoding="utf-8",
        newline="",
    )
    (root / "asset.bin").write_bytes(b"binary\r\npayload\x00")


def test_plugin_zip_is_identical_for_lf_and_crlf_checkouts(tmp_path: Path) -> None:
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    _write_plugin(lf, newline="\n")
    _write_plugin(crlf, newline="\r\n")

    lf_zip = build_zip_bytes(lf)
    crlf_zip = build_zip_bytes(crlf)

    assert lf_zip == crlf_zip
    with zipfile.ZipFile(io.BytesIO(crlf_zip)) as archive:
        assert b"\r" not in archive.read("README.md")
        assert b"\r" not in archive.read("manifest.json")
        assert archive.read("asset.bin") == b"binary\r\npayload\x00"
