"""Relation-linking accuracy: precision/recall/F1 against gold relation annotations.

Gold relations describe expected links by text-prefix endpoints (stable across
pipeline changes, unlike block ids). Endpoints resolve to actual block ids
before comparison with the document's relations. ``caption_to_image`` gold may
target an image via ``to_image_on_page`` since images carry no text.

Quality metrics operate on IR data only and must not import pipeline internals.
"""

from __future__ import annotations

from typing import Any


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _block_ids_by_prefix(document: dict[str, Any], prefix: str) -> set[str]:
    needle = _normalize(prefix)
    matches: set[str] = set()
    for page in document.get("pages", []) or []:
        for block in page.get("blocks", []) or []:
            text = _normalize((block.get("text") or {}).get("raw_text", ""))
            if text.startswith(needle):
                matches.add(str(block.get("id")))
    return matches


def _image_ids_on_page(document: dict[str, Any], page_number: int) -> set[str]:
    for page in document.get("pages", []) or []:
        if page.get("page_number") == page_number:
            return {str(image.get("id")) for image in page.get("images", []) or []}
    return set()


def relation_link_score(document: dict[str, Any], gold_relations: list[dict[str, Any]]) -> dict[str, Any]:
    """Score actual RelationIR links against gold expectations.

    recall: fraction of gold links matched by at least one actual relation.
    precision: fraction of actual relations (of the annotated types) that
    correspond to some gold link. score = F1.
    """
    if not gold_relations:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0, "score": 1.0,
                "matched": 0, "missing": [], "spurious": 0}

    annotated_types = {str(g.get("type")) for g in gold_relations}
    actual = [
        rel for rel in document.get("relations", []) or []
        if str(rel.get("type")) in annotated_types
    ]

    resolved_gold = []
    for gold in gold_relations:
        from_ids = _block_ids_by_prefix(document, gold.get("from_text", ""))
        if gold.get("to_image_on_page") is not None:
            to_ids = _image_ids_on_page(document, int(gold["to_image_on_page"]))
        else:
            to_ids = _block_ids_by_prefix(document, gold.get("to_text", ""))
        resolved_gold.append({"type": str(gold.get("type")), "from_ids": from_ids, "to_ids": to_ids, "gold": gold})

    def rel_matches(rel: dict[str, Any], resolved: dict[str, Any]) -> bool:
        return (
            str(rel.get("type")) == resolved["type"]
            and str(rel.get("from_id")) in resolved["from_ids"]
            and str(rel.get("to_id")) in resolved["to_ids"]
        )

    matched_gold = []
    missing = []
    for resolved in resolved_gold:
        if any(rel_matches(rel, resolved) for rel in actual):
            matched_gold.append(resolved)
        else:
            missing.append(
                {
                    "type": resolved["type"],
                    "from_text": resolved["gold"].get("from_text"),
                    "endpoint_resolved": bool(resolved["from_ids"]) and bool(resolved["to_ids"]),
                }
            )

    correct_actual = sum(
        1 for rel in actual if any(rel_matches(rel, resolved) for resolved in resolved_gold)
    )
    recall = len(matched_gold) / len(gold_relations)
    precision = correct_actual / len(actual) if actual else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "score": round(f1, 4),
        "matched": len(matched_gold),
        "missing": missing,
        "spurious": len(actual) - correct_actual,
    }
