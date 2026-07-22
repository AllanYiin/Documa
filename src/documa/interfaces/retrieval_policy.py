"""Deterministic intent, novelty, and token-aware retrieval policy."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_INTENT_PATTERNS = {
    "definition": re.compile(r"\b(?:define|definition|meaning|what is)\b|(?:定義|是什麼|何謂)", re.IGNORECASE),
    "numeric": re.compile(r"\b(?:how many|how much|percent|rate|amount)\b|(?:多少|幾個|比例|金額|數值)", re.IGNORECASE),
    "date": re.compile(r"\b(?:when|date|year|month)\b|(?:何時|日期|年度|哪一年)", re.IGNORECASE),
    "cause": re.compile(r"\b(?:why|cause|reason|because|due to)\b|(?:為何|原因|因為|導致)", re.IGNORECASE),
    "comparison": re.compile(r"\b(?:compare|versus|vs\.?|difference|higher|lower)\b|(?:比較|差異|高於|低於)", re.IGNORECASE),
    "overview": re.compile(r"\b(?:overview|outline|summary|structure|sections?)\b|(?:概覽|大綱|章節|結構|摘要)", re.IGNORECASE),
}


def query_intents(query: str) -> list[str]:
    return [name for name, pattern in _INTENT_PATTERNS.items() if pattern.search(query)]


def coverage_score(matched_terms: list[str], required_terms: list[str]) -> float:
    if not required_terms:
        return 0.0
    matched = {term.casefold() for term in matched_terms}
    return len(matched) / len({term.casefold() for term in required_terms})


def proximity_score(text: str, required_terms: list[str]) -> float:
    positions = []
    folded = text.casefold()
    for term in required_terms:
        position = folded.find(term.casefold())
        if position >= 0:
            positions.append(position)
    if not positions:
        return 0.0
    if len(positions) == 1:
        return 1.0
    return 1.0 / (1.0 + (max(positions) - min(positions)) / 80.0)


def intent_fit_score(answer_tags: list[str], intents: list[str]) -> float:
    factual = [intent for intent in intents if intent != "overview"]
    if not factual:
        return 1.0
    return len(set(answer_tags).intersection(factual)) / len(set(factual))


def stable_simhash(text: str, bits: int = 64) -> int:
    """Stable SimHash; Python's process-randomized hash() is never used."""
    tokens = re.findall(r"[a-z0-9_+\-]{2,}|[\u4e00-\u9fff]{2,}", text.casefold())
    weights = [0] * bits
    for token in tokens:
        digest = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=bits // 8).digest(), "big")
        for index in range(bits):
            weights[index] += 1 if digest & (1 << index) else -1
    return sum((1 << index) for index, value in enumerate(weights) if value >= 0)


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def mmr_select(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    max_evidence_tokens: int | None = None,
    lambda_relevance: float = 0.75,
) -> list[dict[str, Any]]:
    """Select relevant, non-redundant rows under an optional token cap."""
    remaining = list(rows)
    selected: list[dict[str, Any]] = []
    spent = 0
    max_score = max((float(row["score"]) for row in remaining), default=1.0) or 1.0
    while remaining and len(selected) < max(0, int(limit)):
        best: tuple[float, float, int, str, dict[str, Any]] | None = None
        for row in remaining:
            cost = max(1, int(row.get("evidence_tokens") or 1))
            if max_evidence_tokens is not None and spent + cost > max_evidence_tokens:
                continue
            fingerprint = int(row.get("simhash") or 0)
            redundancy = 0.0
            if selected:
                redundancy = max(
                    1.0 - hamming_distance(fingerprint, int(item.get("simhash") or 0)) / 64.0
                    for item in selected
                )
            relevance = float(row["score"]) / max_score
            mmr = lambda_relevance * relevance - (1.0 - lambda_relevance) * redundancy
            utility = (
                mmr
                * float(row.get("coverage_score", 1.0))
                * max(0.25, float(row.get("intent_fit", 1.0)))
                / (1.0 + cost) ** 0.15
            )
            candidate = (utility, float(row["score"]), -int(row.get("order_index") or 0), str(row["block_id"]), row)
            if best is None or candidate[:4] > best[:4]:
                best = candidate
        if best is None:
            break
        chosen = best[4]
        chosen["utility"] = round(best[0], 6)
        selected.append(chosen)
        spent += max(1, int(chosen.get("evidence_tokens") or 1))
        remaining.remove(chosen)
    return selected
