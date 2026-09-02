"""Fail builds if the pinned, redistributable LingXi assets are incomplete."""

import hashlib
import json
from pathlib import Path


def verify(root: Path | None = None) -> None:
    root = root or Path(__file__).resolve().parents[2]
    vendor = json.loads((root / "native/lingxi/VENDOR.json").read_text(encoding="utf-8"))
    assets = root / "src/documa/_vendor/lingxi/assets"
    if {path.name for path in assets.iterdir()} != set(vendor["models"]):
        raise RuntimeError("LingXi assets must contain exactly the approved required models")
    for name, expected in vendor["models"].items():
        digest = hashlib.sha256()
        with (assets / name).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"LingXi model {name} does not match the approved SHA-256")


if __name__ == "__main__":
    verify()
    print("LingXi 0.4.5 approved models: PASS")
