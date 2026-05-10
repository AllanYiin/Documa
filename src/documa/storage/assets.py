"""Asset storage for page previews and extracted document images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_asset_name(name: str) -> str:
    """Return a filesystem-safe asset filename segment."""

    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._")
    return cleaned or "asset"


@dataclass(slots=True)
class AssetStore:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, relative_path: str | Path, data: bytes) -> str:
        """Write bytes under the asset root and return the relative asset ref."""

        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe asset path: {relative_path}")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return relative.as_posix()

    def write_text(self, relative_path: str | Path, text: str) -> str:
        """Write UTF-8 text under the asset root and return the relative ref."""

        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe asset path: {relative_path}")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return relative.as_posix()

