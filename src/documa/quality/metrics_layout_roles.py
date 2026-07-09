"""Layout-role and OCR-recall scoring against gold annotations.

``header_footer_role_score``: every block whose text starts with an
``excluded_texts`` prefix must be classified as page_header/page_footer.
``ocr_text_recall``: each ``ocr_expected_texts`` string must appear in block
text or image OCR metadata after the OCR-enabled pipeline ran.

Quality metrics operate on IR data only and must not import pipeline internals.
"""

from __future__ import annotations

from typing import Any

_FURNITURE_TYPES = {"page_header", "page_footer"}


def _normalize(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def header_footer_role_score(document: dict[str, Any], excluded_texts: list[str]) -> dict[str, Any]:
    if not excluded_texts:
        return {"score": 1.0, "matched_blocks": 0, "correctly_typed": 0, "unmatched_prefixes": []}

    matched = 0
    correct = 0
    unmatched: list[str] = []
    for prefix in excluded_texts:
        needle = _normalize(prefix)
        hits = [
            block
            for page in document.get("pages", []) or []
            for block in page.get("blocks", []) or []
            if _normalize((block.get("text") or {}).get("raw_text", "")).startswith(needle)
        ]
        if not hits:
            unmatched.append(prefix)
            continue
        matched += len(hits)
        correct += sum(1 for block in hits if str(block.get("type")) in _FURNITURE_TYPES)

    denominator = matched + len(unmatched)  # unmatched prefixes count as misses
    score = correct / denominator if denominator else 0.0
    return {
        "score": round(score, 4),
        "matched_blocks": matched,
        "correctly_typed": correct,
        "unmatched_prefixes": unmatched,
    }


def ocr_text_recall(document: dict[str, Any], expected_texts: list[str]) -> dict[str, Any]:
    if not expected_texts:
        return {"score": 1.0, "expected": 0, "found": 0, "missing": []}

    haystacks: list[str] = []
    for page in document.get("pages", []) or []:
        for block in page.get("blocks", []) or []:
            haystacks.append(_normalize((block.get("text") or {}).get("raw_text", "")))
        for image in page.get("images", []) or []:
            haystacks.append(_normalize((image.get("metadata") or {}).get("ocr_text", "")))
    combined = " \n ".join(haystacks)

    missing = [text for text in expected_texts if _normalize(text) not in combined]
    found = len(expected_texts) - len(missing)
    return {
        "score": round(found / len(expected_texts), 4),
        "expected": len(expected_texts),
        "found": found,
        "missing": missing,
    }
