"""Verify Documa release archives against source, native models and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def release_version() -> str:
    return re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]


def verify(wheel: Path, sdist: Path) -> dict:
    version = release_version()
    vendor = json.loads((ROOT / "native/lingxi/VENDOR.json").read_text(encoding="utf-8"))
    checks = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_path, = [name for name in names if name.endswith(".dist-info/METADATA")]
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        assert metadata["Name"] == "documa" and metadata["Version"] == version
        assert not any("lingxi" in item.lower() for item in metadata.get_all("Requires-Dist", []))
        assert not any(name.startswith("lingxi/") for name in names)
        checks.append("private_namespace_without_public_lingxi_dependency")
        for prefix in ("documa/_vendor/lingxi/_core", "rust_pdf/_native", "rust_office/_core"):
            native, = [name for name in names if name.startswith(prefix) and name.endswith((".pyd", ".so"))]
            data = archive.read(native)
            if "aarch64" in wheel.name or "x86_64" in wheel.name:
                assert data[:5] == b"\x7fELF\x02", native
                assert data[5] == 1, "Only little-endian ELF is supported"
                machine = int.from_bytes(data[18:20], "little")
                assert machine == (183 if "aarch64" in wheel.name else 62), native
            elif "win_amd64" in wheel.name:
                assert data[:2] == b"MZ", native
                pe = int.from_bytes(data[60:64], "little")
                assert data[pe:pe + 6] == b"PE\x00\x00\x64\x86", native
            else:
                raise AssertionError(f"Unvalidated release architecture: {wheel.name}")
        checks.append("three_native_extensions")
        checks.append("native_binary_architecture_matches_wheel")
        prefix = "documa/_vendor/lingxi/assets/"
        assert {item.filename[len(prefix):] for item in archive.infolist()
                if item.filename.startswith(prefix) and not item.is_dir()} == set(vendor["models"])
        for name, digest in vendor["models"].items():
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest, name
        checks.append("wheel_approved_model_hashes")
        for license_name in ("LICENSE", "ASSETS.md"):
            assert any(name.endswith(f"licenses/native/lingxi/{license_name}") for name in names)
        checks.append("lingxi_license_and_model_authorization")
        source_paths = {path.relative_to(ROOT / "src").as_posix(): path for path in (ROOT / "src/documa").rglob("*.py")}
        assert {name for name in names if name.startswith("documa/") and name.endswith(".py")} == set(source_paths)
        for name, path in source_paths.items():
            assert archive.read(name) == path.read_bytes(), f"Stale build cache: {name}"
        checks.append("wheel_python_source_exact_match")
        assert archive.testzip() is None

    with tarfile.open(sdist, "r:gz") as archive:
        names = set(archive.getnames())
        prefix = f"documa-{version}/"
        for name in ("Cargo.toml", "Cargo.lock", "VENDOR.json", "LICENSE", "ASSETS.md", "verify.py"):
            assert prefix + "native/lingxi/" + name in names
        for crate in ("lingxi-core", "lingxi-py"):
            assert prefix + f"native/lingxi/crates/{crate}/src/lib.rs" in names
        for name, digest in vendor["models"].items():
            stream = archive.extractfile(prefix + "src/documa/_vendor/lingxi/assets/" + name)
            assert stream is not None and hashlib.sha256(stream.read()).hexdigest() == digest
        assert not any("/target/" in name or "/.corpus-work/" in name for name in names)
        checks.append("sdist_native_source_lockfile_and_approved_models")
        checked_sources = set()
        # Walk in physical tar order: random reads in a compressed archive
        # repeatedly decompress the bundled models and become quadratic.
        for member in archive.getmembers():
            name = member.name
            relative = name.removeprefix(prefix)
            source = ROOT / relative
            if relative.startswith(("src/", "native/")) and source.suffix in {".py", ".rs", ".toml", ".lock"}:
                assert archive.extractfile(name).read() == source.read_bytes(), f"Stale sdist source: {relative}"
                checked_sources.add(relative)
        assert {"src/" + name for name in source_paths} <= checked_sources
        checks.append("sdist_python_and_native_source_exact_match")
    return {"status": "PASS", "wheel": wheel.name, "sdist": sdist.name, "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.wheel, args.sdist), ensure_ascii=False, indent=2))
