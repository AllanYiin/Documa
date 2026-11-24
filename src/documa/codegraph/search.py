"""Deterministic lexical entry ranking for repository exploration."""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z\d])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = frozenset("a an and are by does for from how in is of on or that the to use what where which with".split())
_SUPPORTING_PATHS = {"tests", "examples", "fixtures", "tools", "scripts"}
_SUPPORTING_TERMS = _SUPPORTING_PATHS | {"test", "example", "fixture", "tool", "script", "benchmark"}
_DOCUMENT_TERMS = {"doc", "docs", "document", "documented", "documentation", "spec", "adr", "rfc", "guide", "why"}


def _terms(text: str) -> set[str]:
    split = _CAMEL_BOUNDARY.sub(" ", text)
    terms = {word.casefold() for word in _WORDS.findall(split) if len(word) > 1}
    terms.update(word.casefold() for word in _WORDS.findall(text) if len(word) > 1)
    return terms - _STOP_WORDS


def rank_entries(connection: sqlite3.Connection, query: str, *, limit: int) -> list[str]:
    """Prefer identifier matches and rare term coverage. Keep candidate files distinct."""
    terms = _terms(query)
    if not terms:
        return []
    query_path = query.strip().replace("\\", "/").casefold()
    asks_for_supporting_code = bool(terms & _SUPPORTING_TERMS)
    asks_for_documentation = bool(terms & _DOCUMENT_TERMS)
    frequencies: Counter[str] = Counter()
    candidates: list[tuple[sqlite3.Row, dict[str, int]]] = []
    for row in connection.execute("""
        SELECT node_id,kind,qualified_name,display_name,source_locator,start_line,signature,summary,docstring
        FROM nodes WHERE source_locator IS NOT NULL AND kind != 'workspace'
    """):
        scores: dict[str, int] = {}
        for field, weight in (
            ("display_name", 7), ("qualified_name", 5), ("source_locator", 4),
            ("signature", 4), ("summary", 2), ("docstring", 1),
        ):
            for term in terms & _terms(row[field] or ""):
                scores.setdefault(term, weight)
        if not scores:
            continue
        frequencies.update(scores.keys())
        candidates.append((row, scores))

    def rank(candidate: tuple[sqlite3.Row, dict[str, int]]) -> tuple:
        row, matches = candidate
        locator = row["source_locator"]
        exact = query_path in {locator.casefold(), row["qualified_name"].casefold(), row["display_name"].casefold()}
        # Fixed iteration order keeps floating point tie-breaking stable across processes.
        score = sum(matches[term] / (1 + math.log(frequencies[term])) for term in sorted(matches))
        if not asks_for_supporting_code and set(locator.split("/")[:-1]) & _SUPPORTING_PATHS:
            score *= 0.55
        if row["kind"] == "document_block":
            score *= 1.45 if asks_for_documentation else 0.65
        return (not exact, -score, -len(matches), locator, row["start_line"] or 0, row["node_id"])

    entries: list[str] = []
    secondary: list[str] = []
    seen_paths: set[str] = set()
    for row, _ in sorted(candidates, key=rank):
        if row["source_locator"] in seen_paths:
            secondary.append(row["node_id"])
        else:
            seen_paths.add(row["source_locator"])
            entries.append(row["node_id"])
            if len(entries) == limit:
                break
    return (entries + secondary)[:limit]
