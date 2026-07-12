"""BM25-lite ranking and next-action policy for the agent-facing block search.

Pure functions over per-call statistics — no pipeline imports, no state. The
single-document search cannot afford a persisted index, so this module gives
it the two properties naive weighted counts lack: rare terms outrank common
ones (IDF) and long blocks cannot win by repeating one term (TF saturation
plus mild length normalization on body hits).
"""

from __future__ import annotations

import math

# Score multipliers by document region: navigation and boilerplate regions are
# demoted — never excluded — so body evidence outranks TOC/header noise while
# region hits stay reachable for explicitly structural queries.
DOC_REGION_MULTIPLIERS = {
    "toc": 0.3,
    "header_footer": 0.3,
    "references": 0.6,
    "metadata": 0.6,
}


def inverse_block_frequency(block_count: int, matching_block_count: int) -> float:
    """IDF over document blocks: terms present in few blocks weigh more."""
    return math.log(1.0 + (block_count - matching_block_count + 0.5) / (matching_block_count + 0.5))


def saturated_term_frequency(hit_count: int) -> float:
    """Diminishing returns per extra hit, so repetition cannot dominate."""
    if hit_count <= 0:
        return 0.0
    return hit_count / (hit_count + 1.2)


def body_length_normalization(body_char_count: int, average_body_char_count: float) -> float:
    """Mild penalty for above-average body length, applied to body hits only."""
    if average_body_char_count <= 0 or body_char_count <= 0:
        return 1.0
    ratio = body_char_count / average_body_char_count
    return 1.0 / (0.75 + 0.25 * ratio)


def doc_region_multiplier(doc_region: str) -> float:
    return DOC_REGION_MULTIPLIERS.get(doc_region, 1.0)
