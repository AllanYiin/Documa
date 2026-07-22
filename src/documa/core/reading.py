"""Boundary-aware text windows for progressive document reading."""

from __future__ import annotations

import re


_SENTENCE_END = re.compile(r"[。！？!?](?:[\"'”’）】》〉」』〕］}])?|\.(?=\s|$)")


def complete_unit_for_kind(kind: str) -> str:
    if kind == "table":
        return "table_row"
    if kind == "code":
        return "code_line"
    if kind == "list":
        return "list_item"
    return "sentence"


def boundary_end(text: str, hard_end: int, *, kind: str = "paragraph", start: int = 0) -> int:
    """Return a stable unit boundary at or before ``hard_end`` when possible."""
    hard_end = min(len(text), max(start, hard_end))
    if hard_end >= len(text):
        return len(text)
    window = text[start:hard_end]
    if kind in {"table", "code", "list"}:
        newline = window.rfind("\n")
        if newline >= 0:
            return start + newline + 1
    matches = list(_SENTENCE_END.finditer(window))
    if matches:
        return start + matches[-1].end()
    newline = window.rfind("\n")
    if newline >= 0:
        return start + newline + 1
    # A boundary-free unit may exceed the requested window. Preserve progress
    # instead of returning an empty slice or looping on the same cursor.
    return hard_end


def bounded_window(
    text: str,
    *,
    start: int = 0,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    counter: object | None = None,
    kind: str = "paragraph",
) -> tuple[str, int, bool]:
    """Slice text from a continuation cursor without splitting useful units."""
    start = max(0, min(int(start), len(text)))
    remaining = text[start:]
    hard_end = len(text)
    if max_chars is not None:
        hard_end = min(hard_end, start + max(1, int(max_chars)))
    if max_tokens is not None and counter is not None:
        token_text, token_truncated = counter.truncate(remaining, max(1, int(max_tokens)))
        if token_truncated:
            hard_end = min(hard_end, start + len(token_text))
    end = boundary_end(text, hard_end, kind=kind, start=start)
    return text[start:end], end, end < len(text)
