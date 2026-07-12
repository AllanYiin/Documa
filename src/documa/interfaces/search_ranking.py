"""BM25-lite ranking and next-action policy for the agent-facing block search.

Pure functions over per-call statistics — no pipeline imports, no state. The
single-document search cannot afford a persisted index, so this module gives
it the two properties naive weighted counts lack: rare terms outrank common
ones (IDF) and long blocks cannot win by repeating one term (TF saturation
plus mild length normalization on body hits).
"""

from __future__ import annotations

import math
from typing import Any

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


# Confidence rule for recommending an immediate read: the top hit either
# matched at least this many query terms, or leads the runner-up by this ratio.
_CONFIDENT_MATCHED_TERMS = 2
_CONFIDENT_SCORE_LEAD = 1.35


def recommended_next_action(ranked_page: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministic read recommendation from ranked search rows.

    Returns a ready-to-issue documa_read_block call: the top hit alone when it
    is confidently ahead, or the top two when scores are too close to separate.
    """
    if not ranked_page:
        return None
    top = ranked_page[0]
    runner_up = ranked_page[1] if len(ranked_page) > 1 else None
    confident = top.get("matched_terms_count", 0) >= _CONFIDENT_MATCHED_TERMS or (
        runner_up is None or top["score"] >= _CONFIDENT_SCORE_LEAD * max(runner_up["score"], 1e-9)
    )
    block_ids = [top["block_id"]]
    if not confident and runner_up is not None:
        block_ids.append(runner_up["block_id"])
    return {
        "tool": "documa_read_block",
        "block_ids": block_ids,
        "include_children": bool(top.get("neighbors", {}).get("needs_next")),
        "max_chars": top.get("recommended_read_chars"),
    }


def search_hints(
    *,
    result_count: int,
    total_matches: int,
    offset: int,
    search_body: bool,
    term_count: int,
    top_matched_terms: int,
) -> list[str]:
    """At most two short, deterministic follow-up hints for the calling agent."""
    hints: list[str] = []
    if result_count == 0:
        if not search_body:
            hints.append("No matches with search_body=false; retry with search_body=true.")
        hints.append("No matches; retry with any_of synonyms or browse structure via documa_list_blocks.")
    else:
        if total_matches > offset + result_count:
            hints.append(f"More matches available: retry with offset={offset + result_count}.")
        if term_count > 1 and top_matched_terms < term_count:
            hints.append("No returned block matches every term; consider splitting the question or dropping a term.")
    return hints[:2]
