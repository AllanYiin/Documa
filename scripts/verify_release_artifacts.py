"""Verify Documa release archives against source, native models and metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def verify(wheel: Path, sdist: Path) -> dict:
    vendor = json.loads((ROOT / "native/lingxi/VENDOR.json").read_text(encoding="utf-8"))
    checks = []
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_path, = [name for name in names if name.endswith(".dist-info/METADATA")]
        metadata = BytesParser().parsebytes(archive.read(metadata_path))
        assert metadata["Name"] == "documa" and metadata["Version"] == "0.8.0"
        assert not any("lingxi" in item.lower() for item in metadata.get_all("Requires-Dist", []))
        assert not any(name.startswith("lingxi/") for name in names)
        checks.append("private_namespace_without_public_lingxi_dependency")
        for prefix in ("documa/_vendor/lingxi/_core", "rust_pdf/_native", "rust_office/_core"):
            assert any(name.startswith(prefix) and name.endswith((".pyd", ".so")) for name in names), prefix
        checks.append("three_native_extensions")
        prefix = "documa/_vendor/lingxi/assets/"
        assert {name[len(prefix):] for name in names if name.startswith(prefix)} == set(vendor["models"])
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
        prefix = "documa-0.8.0/"
        for name in ("Cargo.toml", "Cargo.lock", "VENDOR.json", "LICENSE", "ASSETS.md", "verify.py"):
            assert prefix + "native/lingxi/" + name in names
        for crate in ("lingxi-core", "lingxi-py"):
            assert prefix + f"native/lingxi/crates/{crate}/src/lib.rs" in names
        for name, digest in vendor["models"].items():
            stream = archive.extractfile(prefix + "src/documa/_vendor/lingxi/assets/" + name)
            assert stream is not None and hashlib.sha256(stream.read()).hexdigest() == digest
        assert not any("/target/" in name or "/.corpus-work/" in name for name in names)
        checks.append("sdist_native_source_lockfile_and_approved_models")
    return {"status": "PASS", "wheel": wheel.name, "sdist": sdist.name, "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("sdist", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.wheel, args.sdist), ensure_ascii=False, indent=2))
