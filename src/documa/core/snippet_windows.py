"""CJK-aware snippet windowing shared by single-document and collection search.

Leaf module: no imports from documa.interfaces or documa.collections, so both
layers can use identical hit-centered snippet behavior without import cycles.
"""

from __future__ import annotations

import re

CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
WORD_RE = re.compile(r"\S+")


def is_cjk(keyword: str) -> bool:
    return bool(CJK_RE.search(keyword))


def find_hits(text: str, keyword: str) -> list[tuple[int, int]]:
    """Case-folded substring hit spans of keyword in text."""
    if not text or not keyword:
        return []
    haystack = text.casefold()
    needle = keyword.casefold()
    output: list[tuple[int, int]] = []
    position = 0
    while True:
        index = haystack.find(needle, position)
        if index < 0:
            return output
        output.append((index, index + len(needle)))
        position = index + max(1, len(needle))


def make_snippet(text: str, start: int, end: int, keyword: str, *, chars: int = 24, words: int = 8) -> str:
    """Window around one hit: ±chars for CJK keywords, ±words for ASCII."""
    if is_cjk(keyword):
        left = max(0, start - chars)
        right = min(len(text), end + chars)
    else:
        prefix_matches = list(WORD_RE.finditer(text[:start]))
        suffix_matches = list(WORD_RE.finditer(text[end:]))
        left = prefix_matches[-words].start() if len(prefix_matches) > words else 0
        right = end + suffix_matches[words - 1].end() if len(suffix_matches) > words else len(text)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def centered_snippet(
    text: str,
    terms: list[str],
    *,
    chars: int = 24,
    words: int = 8,
    fallback_chars: int = 240,
) -> str:
    """Snippet centered on the first term that hits; prefix fallback otherwise.

    A prefix snippet that misses the match is pure token waste, so the window
    follows the query whenever any term is present in the text.
    """
    if not text:
        return ""
    for term in terms:
        hits = find_hits(text, term)
        if hits:
            start, end = hits[0]
            return make_snippet(text, start, end, term, chars=chars, words=words)
    return text[:fallback_chars]
