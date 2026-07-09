"""Reading-order scoring: normalized edit distance (NED) over block sequences.

Gold annotations are text prefixes in expected reading order; each prefix is
matched to the first unused actual block whose text starts with it. The score
is 1 - NED, where NED = editdist(gold order, actual order) / gold length and
unmatched gold prefixes count as deletions. 1.0 means a perfect order.

Quality metrics operate on IR data only and must not import pipeline internals.
"""

from __future__ import annotations


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _sequence_edit_distance(a: list[int], b: list[int]) -> int:
    rows, cols = len(a) + 1, len(b) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            table[i][j] = min(
                table[i - 1][j] + 1,
                table[i][j - 1] + 1,
                table[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else 1),
            )
    return table[-1][-1]


def match_gold_prefixes(gold_prefixes: list[str], actual_texts: list[str]) -> list[int | None]:
    """Match each gold prefix to the first unused actual block index."""
    used: set[int] = set()
    positions: list[int | None] = []
    normalized_actual = [_normalize(text) for text in actual_texts]
    for prefix in gold_prefixes:
        needle = _normalize(prefix)
        hit = next(
            (i for i, text in enumerate(normalized_actual) if i not in used and text.startswith(needle)),
            None,
        )
        if hit is not None:
            used.add(hit)
        positions.append(hit)
    return positions


def reading_order_score(gold_prefixes: list[str], actual_texts: list[str]) -> dict:
    """Score actual block order against gold prefix order; 1.0 is perfect."""
    total = len(gold_prefixes)
    if total == 0:
        return {"matched": 0, "total": 0, "edit_distance": 0, "ned": 0.0, "score": 1.0}

    positions = match_gold_prefixes(gold_prefixes, actual_texts)
    matched_gold = [gold_idx for gold_idx, pos in enumerate(positions) if pos is not None]
    # Gold ids sorted by where they actually appear = the observed reading order.
    observed_order = [gold_idx for gold_idx, _pos in sorted(
        ((g, positions[g]) for g in matched_gold), key=lambda item: item[1]
    )]
    distance = _sequence_edit_distance(matched_gold, observed_order)
    distance += total - len(matched_gold)  # unmatched prefixes count as deletions
    ned = distance / total
    return {
        "matched": len(matched_gold),
        "total": total,
        "edit_distance": distance,
        "ned": round(ned, 4),
        "score": round(max(0.0, 1.0 - ned), 4),
    }
