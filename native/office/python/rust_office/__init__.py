"""Local, atomic Office extraction with typed errors and event-wise decoding."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from . import _core
from .types import ParseOptions, ParseLimits, OfficeEvent

class OfficeError(ValueError):
    """Stable parser error. Remains compatible with callers catching ValueError."""
    def __init__(self, code: str, message: str, recoverable: bool = False, context: dict[str, Any] | None = None):
        self.code, self.message, self.recoverable, self.context = code, message, recoverable, context or {}
        super().__init__(f"{code}: {message}")

def _call(function, *args):
    try:
        return function(*args)
    except ValueError as error:
        try:
            payload = json.loads(str(error))
        except (ValueError, TypeError):
            raise OfficeError("OFFICE_PARSE_FAILED", str(error)) from error
        raise OfficeError(**payload) from error

class OfficeEventStream(Iterator[dict[str, Any]]):
    """Atomic parse, followed by lazy event serialization/decoding."""
    def __init__(self, native):
        self._native = native
        self.metadata = json.loads(native.metadata_json)
    def __iter__(self):
        return self
    def __next__(self) -> dict[str, Any]:
        return json.loads(next(self._native))
    def remaining(self) -> int:
        return self._native.remaining()

def version_info() -> tuple[str, str]:
    return _core.version_info()

def capabilities() -> dict[str, Any]:
    return json.loads(_core.capabilities_json())

def schema() -> dict[str, Any]:
    return json.loads(_core.schema_json())

def detect_format(path: str | Path) -> str:
    return _call(_core.detect_format, Path(path))

def open(path: str | Path | bytes | BinaryIO, options: ParseOptions | None = None, *, source_name: str | None = None, **overrides: Any) -> OfficeEventStream:
    if options is not None and not isinstance(options, dict):
        raise OfficeError("INVALID_OPTIONS", "options must be a dictionary.", True)
    try:
        settings = dict(options or {})
    except (TypeError, ValueError) as error:
        raise OfficeError("INVALID_OPTIONS", str(error), True) from error
    settings.update(overrides)
    try:
        encoded = json.dumps(settings)
    except (TypeError, ValueError) as error:
        raise OfficeError("INVALID_OPTIONS", str(error), True) from error
    if isinstance(path, (str, Path)):
        return OfficeEventStream(_call(_core.open_native, Path(path), encoded))
    _call(_core.validate_options_json, encoded)
    name = source_name or getattr(path, "name", None)
    if not isinstance(name, (str, Path)):
        raise OfficeError("INVALID_OPTIONS", "bytes/file-like input requires source_name with an Office extension.", True)
    limit = settings.get("limits", {}).get("max_input_bytes", 512 * 1024 * 1024)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise OfficeError("INVALID_OPTIONS", "max_input_bytes must be a positive integer.", True)
    if isinstance(path, bytes):
        data = path
    elif hasattr(path, "read"):
        chunks, length = [], 0
        while length <= limit:
            chunk = path.read(min(1024 * 1024, limit + 1 - length))
            if not isinstance(chunk, bytes):
                raise OfficeError("INVALID_OPTIONS", "Input stream must return bytes.", True)
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        data = b"".join(chunks)
    else:
        raise OfficeError("INVALID_OPTIONS", "Expected path, bytes, or binary file-like object.", True)
    if len(data) > limit:
        raise OfficeError("INPUT_LIMIT_EXCEEDED", "Input exceeds max_input_bytes.")
    return OfficeEventStream(_call(_core.open_bytes_native, data, str(name), encoded))

__all__ = ["OfficeError", "OfficeEvent", "OfficeEventStream", "ParseLimits", "ParseOptions", "capabilities", "detect_format", "open", "schema", "version_info"]
