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


def _single_document_action(ir_path: str, row: dict[str, Any]) -> dict[str, Any]:
    block_id = row["block_id"]
    block_type = row.get("block_type") or row.get("kind")
    if block_type == "section":
        return {
            "tool": "documa_list_blocks",
            "arguments": {"ir_path": ir_path, "parent_id": block_id, "limit": 10},
        }
    if row.get("neighbors", {}).get("needs_next"):
        return {
            "tool": "documa_source_window",
            "arguments": {"ir_path": ir_path, "block_id": block_id, "before": 0, "after": 1},
        }
    arguments: dict[str, Any] = {"ir_path": ir_path, "block_id": block_id}
    if row.get("recommended_read_chars") is not None:
        arguments["max_chars"] = row["recommended_read_chars"]
    return {"tool": "documa_read_block", "arguments": arguments}


def recommended_next_action(ir_path: str, ranked_page: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return schema-valid calls that can be executed without model rewriting."""
    if not ranked_page:
        return None
    top = ranked_page[0]
    runner_up = ranked_page[1] if len(ranked_page) > 1 else None
    confident = top.get("matched_terms_count", 0) >= _CONFIDENT_MATCHED_TERMS or (
        runner_up is None or top["score"] >= _CONFIDENT_SCORE_LEAD * max(runner_up["score"], 1e-9)
    )
    selected = [top]
    if not confident and runner_up is not None:
        selected.append(runner_up)
    return {"actions": [_single_document_action(ir_path, row) for row in selected]}


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


def collection_recommended_next_action(results: list[dict[str, Any]], *, grouped: bool) -> dict[str, Any] | None:
    """Return schema-valid cross-document read calls."""
    if not results:
        return None
    if grouped:
        top_blocks = results[0].get("top_blocks") or []
        if not top_blocks:
            return None
        read_ref = top_blocks[0]["read_ref"]
        return {
            "actions": [
                {
                    "tool": "documa_read_block",
                    "arguments": {"ir_path": read_ref["ir_path"], "block_id": read_ref["block_id"]},
                }
            ]
        }
    top = results[0]
    read_ref = top["read_ref"]
    read_refs = [read_ref]
    runner_up = results[1] if len(results) > 1 else None
    if (
        runner_up is not None
        and runner_up["read_ref"]["ir_path"] == read_ref["ir_path"]
        and top["score"] < _CONFIDENT_SCORE_LEAD * max(runner_up["score"], 1e-9)
    ):
        read_refs.append(runner_up["read_ref"])
    return {
        "actions": [
            {
                "tool": "documa_read_block",
                "arguments": {"ir_path": ref["ir_path"], "block_id": ref["block_id"]},
            }
            for ref in read_refs
        ]
    }


def collection_search_hints(
    *,
    result_count: int,
    has_more: bool,
    offset: int,
    match_mode: str | None,
    distinct_documents: int,
    per_document_limit: int | None,
    group_by_document: bool,
) -> list[str]:
    """At most two deterministic hints for cross-document search, by priority."""
    hints: list[str] = []
    if result_count == 0:
        hints.append("No matches; loosen terms or check index freshness via documa_doctor with store_dir.")
        return hints
    if match_mode == "any_term":
        hints.append("Precision degraded to any-term matching; tighten terms or quote phrases.")
    if has_more:
        hints.append(f"More matches available: retry with offset={offset + result_count}.")
    if not group_by_document and per_document_limit is None and distinct_documents >= 4:
        hints.append(
            f"Hits span {distinct_documents} documents; retry with group_by_document=true or per_document_limit=1."
        )
    return hints[:2]
