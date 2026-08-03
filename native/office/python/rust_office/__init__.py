"""Structured Python facade for the Rust Office parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from . import _core


class OfficeEventStream(Iterator[dict[str, Any]]):
    """Decode the native stream one event at a time."""

    def __init__(self, native: _core.OfficeEventStream):
        self._native = native
        self.metadata = json.loads(native.metadata_json)

    def __iter__(self) -> "OfficeEventStream":
        return self

    def __next__(self) -> dict[str, Any]:
        return json.loads(next(self._native))

    def remaining(self) -> int:
        return self._native.remaining()


def version_info() -> tuple[str, str]:
    return _core.version_info()


def capabilities() -> dict[str, Any]:
    return json.loads(_core.capabilities_json())


def detect_format(path: str | Path) -> str:
    return _core.detect_format(Path(path))


def open(
    path: str | Path,
    options: dict[str, Any] | None = None,
    **overrides: Any,
) -> OfficeEventStream:
    settings = {
        "extract_images": True,
        "include_hidden": False,
        "revision_mode": "final",
        "formula_mode": "formula_and_cached_value",
        "external_links": "metadata_only",
    }
    settings.update(options or {})
    settings.update(overrides)
    return OfficeEventStream(_core.open_native(Path(path), **settings))


__all__ = ["OfficeEventStream", "capabilities", "detect_format", "open", "version_info"]
