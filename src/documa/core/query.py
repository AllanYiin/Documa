"""Shared, deterministic parsing for lexical document queries."""

from __future__ import annotations

from dataclasses import dataclass
import re


_QUERY_TERM_RE = re.compile(r"[\w\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", re.UNICODE)
_QUOTED_PHRASE_RE = re.compile(r'"([^"]+)"')


@dataclass(frozen=True)
class QuerySpec:
    """Minimal shared query AST used by single- and multi-document search."""

    units: tuple[str, ...]
    phrases: tuple[str, ...]


def parse_query(query: str | None, any_of: list[str] | None = None) -> QuerySpec:
    """Parse quoted phrases as units, then add bare terms and synonyms.

    The parser intentionally stays lexical and dependency-free. It fixes the
    important contract that ``"capital buffer"`` is searched without quote
    characters and as one adjacent phrase instead of two impossible tokens.
    """

    raw_query = str(query or "")
    phrases = [phrase.strip() for phrase in _QUOTED_PHRASE_RE.findall(raw_query) if phrase.strip()]
    remainder = _QUOTED_PHRASE_RE.sub(" ", raw_query)
    bare_terms = []
    for term in remainder.split():
        cleaned = term.strip('"')
        if cleaned and _QUERY_TERM_RE.search(cleaned):
            bare_terms.append(cleaned)
    candidates = [*phrases, *bare_terms]
    if any_of:
        candidates.extend(str(term).strip() for term in any_of if str(term).strip())
    if not candidates and raw_query.strip():
        candidates.append(raw_query.strip().strip('"'))

    units: list[str] = []
    seen: set[str] = set()
    for unit in candidates:
        folded = unit.casefold()
        if not folded or folded in seen:
            continue
        seen.add(folded)
        units.append(unit)
    phrase_keys = {phrase.casefold() for phrase in phrases}
    normalized_phrases = tuple(unit for unit in units if unit.casefold() in phrase_keys)
    return QuerySpec(units=tuple(units), phrases=normalized_phrases)
