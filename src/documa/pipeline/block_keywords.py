"""Bottom-up keyword aggregation for document blocks."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from documa.adapters.lingxi_binding import lingxi_binding, load_segmenter

from documa.core.ir import DocumentBlockIR, DocumentBlockType, DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult
from documa.pipeline.block_tree import document_block_text


_EN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]{1,}")
_MIXED_TOKEN = re.compile(r"(?:[A-Za-z0-9]+[\u4e00-\u9fff]+|[\u4e00-\u9fff]+[A-Za-z0-9]+)[A-Za-z0-9\u4e00-\u9fff]*")
_SENTENCE_SPLIT = re.compile(r"[，。！？；：、,.!?;:\n\r\t ]+")
DEFAULT_KEYWORD_PROVIDER = "lingxi"
REQUIRED_LINGXI_VERSION = "0.2.1"
SUPPORTED_LINGXI_VERSIONS = ("0.2.1", "0.3.0", "0.4.5")
PREFERRED_LINGXI_VERSION = "0.4.5"


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _children_by_parent(blocks: list[DocumentBlockIR]) -> dict[str | None, list[DocumentBlockIR]]:
    children: dict[str | None, list[DocumentBlockIR]] = {}
    by_id = {block.id: block for block in blocks}
    for block in blocks:
        children.setdefault(block.parent_id, [])
    for block in blocks:
        if block.parent_id in by_id:
            children.setdefault(block.parent_id, []).append(block)
    for child_list in children.values():
        child_list.sort(key=lambda item: (item.order_index is None, item.order_index or 0))
    return children


def _postorder(block: DocumentBlockIR, children: dict[str | None, list[DocumentBlockIR]]) -> list[DocumentBlockIR]:
    ordered: list[DocumentBlockIR] = []
    for child in children.get(block.id, []):
        ordered.extend(_postorder(child, children))
    ordered.append(block)
    return ordered


def _text_terms(text: str, *, min_len: int = 2, max_len: int = 8) -> Counter[str]:
    terms: Counter[str] = Counter()
    for match in _EN_TOKEN.finditer(text):
        terms[match.group(0).casefold()] += 1
    for match in _MIXED_TOKEN.finditer(text):
        terms[match.group(0)] += 1
    for sentence in _SENTENCE_SPLIT.split(text):
        chars = [ch for ch in sentence if _is_cjk(ch)]
        for start in range(len(chars)):
            for length in range(min_len, max_len + 1):
                end = start + length
                if end > len(chars):
                    break
                terms["".join(chars[start:end])] += 1
    return terms


def _neighbor_counters(text: str, *, min_len: int = 2, max_len: int = 8) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
    left: dict[str, Counter[str]] = {}
    right: dict[str, Counter[str]] = {}
    for sentence in _SENTENCE_SPLIT.split(text):
        chars = [ch for ch in sentence if _is_cjk(ch)]
        for start in range(len(chars)):
            for length in range(min_len, max_len + 1):
                end = start + length
                if end > len(chars):
                    break
                token = "".join(chars[start:end])
                left.setdefault(token, Counter())[chars[start - 1] if start > 0 else "<BOS>"] += 1
                right.setdefault(token, Counter())[chars[end] if end < len(chars) else "<EOS>"] += 1
    return left, right


def _merge_neighbor_counters(items: list[dict[str, Counter[str]]]) -> dict[str, Counter[str]]:
    merged: dict[str, Counter[str]] = {}
    for item in items:
        for token, counter in item.items():
            merged.setdefault(token, Counter()).update(counter)
    return merged


def _entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    return -sum((count / total) * math.log(count / total) for count in counter.values())


def _thresholds(block: DocumentBlockIR, leaf_count: int, text_length: int) -> tuple[int, int]:
    if block.type in {
        DocumentBlockType.PARAGRAPH,
        DocumentBlockType.TABLE,
        DocumentBlockType.IMAGE,
        DocumentBlockType.FOOTNOTE,
    }:
        return (1, 1)
    if block.type == DocumentBlockType.PAGE:
        return (max(1, math.ceil(leaf_count * 0.05)), max(2, math.ceil(text_length / 2000)))
    if block.type == DocumentBlockType.SECTION:
        return (max(2, math.ceil(leaf_count * 0.03)), max(2, math.ceil(text_length / 3000)))
    if text_length < 1000:
        return (max(1, math.ceil(leaf_count * 0.01)), 2)
    return (max(3, math.ceil(leaf_count * 0.01)), max(5, math.ceil(text_length / 5000)))


def _rank_terms(
    term_freq: Counter[str],
    child_support: Counter[str],
    *,
    min_support: int,
    min_freq: int,
    top_k: int,
) -> list[str]:
    ranked = []
    for term, freq in term_freq.items():
        support = child_support.get(term, 1)
        if freq < min_freq or support < min_support:
            continue
        score = freq * max(1, support) * min(len(term), 8)
        ranked.append((score, freq, support, term))
    ranked.sort(reverse=True)
    return [term for _, _, _, term in ranked[:top_k]]


def _rank_weighted_terms(
    scores: Counter[str],
    support: Counter[str],
    *,
    top_k: int,
) -> list[str]:
    ranked = [
        (float(score) * max(1, support.get(term, 1)), support.get(term, 1), term)
        for term, score in scores.items()
        if term
    ]
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [term for _, _, term in ranked[:top_k]]


@lru_cache(maxsize=1)
def _load_lingxi_segmenter():
    lingxi, installed_version = lingxi_binding()
    if installed_version not in SUPPORTED_LINGXI_VERSIONS:
        raise ImportError(
            f"LingXi {' or '.join(SUPPORTED_LINGXI_VERSIONS)} is required; found {installed_version}"
        )
    return load_segmenter(lingxi), installed_version


def _rank_new_words(
    term_freq: Counter[str],
    child_support: Counter[str],
    left_neighbors: dict[str, Counter[str]],
    right_neighbors: dict[str, Counter[str]],
    *,
    min_support: int,
    min_freq: int,
    top_k: int,
) -> list[dict[str, Any]]:
    ranked = []
    for term, freq in term_freq.items():
        if not all(_is_cjk(ch) for ch in term):
            continue
        support = child_support.get(term, 1)
        if freq < min_freq or support < min_support:
            continue
        left_entropy = _entropy(left_neighbors.get(term, Counter()))
        right_entropy = _entropy(right_neighbors.get(term, Counter()))
        freedom = min(left_entropy, right_entropy)
        if freedom <= 0 and support > 1:
            freedom = math.log(support + 1)
        score = freq * max(1, support) * max(freedom, 0.1) * min(len(term), 6)
        ranked.append(
            (
                score,
                {
                    "term": term,
                    "freq": freq,
                    "support": support,
                    "left_entropy": round(left_entropy, 4),
                    "right_entropy": round(right_entropy, 4),
                    "score": round(score, 4),
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in ranked[:top_k]]


def extract_new_word_terms(text: str, *, top_k: int = 12) -> list[dict[str, Any]]:
    """Return deterministic CJK new-word metadata for non-DocumentIR callers."""

    term_frequency = _text_terms(text)
    left_neighbors, right_neighbors = _neighbor_counters(text)
    support = Counter({term: 1 for term in term_frequency})
    return _rank_new_words(
        term_frequency,
        support,
        left_neighbors,
        right_neighbors,
        min_support=1,
        min_freq=2,
        top_k=max(0, top_k),
    )


@dataclass(slots=True)
class BlockKeywordExtractionStage(PipelineStage):
    """Aggregate block keywords bottom-up instead of rescanning every level."""

    name: str = "block_keywords"
    default_top_k: int = 12
    max_stats_terms: int = 500

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        if not document.document_blocks:
            return StageResult(
                document=document,
                stage_name=self.name,
                changed=False,
                report={"skipped": "missing_document_blocks"},
            )

        settings = context.settings if context else {}
        top_k = int(settings.get("keyword_top_k", self.default_top_k))
        requested_provider = str(settings.get("keyword_provider", DEFAULT_KEYWORD_PROVIDER)).casefold()
        if requested_provider not in {"lingxi", "ngram"}:
            raise ValueError(f"Unsupported keyword_provider: {requested_provider}")
        segmenter = None
        lingxi_version = None
        provider_fallback_reason = None
        if requested_provider == "lingxi":
            try:
                segmenter, lingxi_version = _load_lingxi_segmenter()
            except Exception as exc:
                provider_fallback_reason = f"{type(exc).__name__}: {exc}"
        by_id = {block.id: block for block in document.document_blocks}
        children = _children_by_parent(document.document_blocks)
        roots = [block for block in document.document_blocks if block.parent_id is None]
        ordered: list[DocumentBlockIR] = []
        for root in roots:
            ordered.extend(_postorder(root, children))

        aggregate: dict[str, dict[str, Any]] = {}
        changed = 0
        provider_counts: Counter[str] = Counter()

        for block in ordered:
            child_blocks = [by_id[child_id] for child_id in block.child_ids if child_id in by_id]
            if child_blocks:
                term_freq = Counter()
                child_support = Counter()
                keyword_scores: Counter[str] = Counter()
                keyword_support: Counter[str] = Counter()
                left_sources = []
                right_sources = []
                leaf_count = 0
                text_length = 0
                for child in child_blocks:
                    stats = aggregate.get(child.id, {})
                    child_terms = Counter(stats.get("term_freq", {}))
                    term_freq.update(child_terms)
                    child_support.update(Counter(stats.get("child_support", {})) or Counter(child_terms.keys()))
                    child_scores = Counter(stats.get("keyword_scores", {}))
                    keyword_scores.update(child_scores)
                    keyword_support.update(Counter(stats.get("keyword_support", {})) or Counter(child_scores.keys()))
                    left_sources.append(stats.get("left_neighbors", {}))
                    right_sources.append(stats.get("right_neighbors", {}))
                    leaf_count += int(stats.get("leaf_count", 0))
                    text_length += int(stats.get("text_length", 0))
                left_neighbors = _merge_neighbor_counters(left_sources)
                right_neighbors = _merge_neighbor_counters(right_sources)
            else:
                text = document_block_text(document, block)
                term_freq = _text_terms(text)
                child_support = Counter({term: 1 for term in term_freq})
                keyword_scores = Counter()
                keyword_support = Counter()
                if segmenter is not None and text.strip():
                    try:
                        for term, weight in segmenter.extract_keywords(text, max(top_k * 2, top_k), None):
                            keyword_scores[str(term)] += float(weight)
                            keyword_support[str(term)] = 1
                    except Exception as exc:
                        provider_fallback_reason = f"{type(exc).__name__}: {exc}"
                left_neighbors, right_neighbors = _neighbor_counters(text)
                leaf_count = 1 if text.strip() else 0
                text_length = len(text)

            min_support, min_freq = _thresholds(block, leaf_count, text_length)
            # LingXi selects keywords for text-bearing leaves. Ancestors retain
            # the established n-gram/support aggregation so the same evidence
            # is not published as duplicate hits at every hierarchy level.
            block_provider = "lingxi" if not child_blocks and keyword_scores else "ngram"
            if block_provider == "lingxi":
                keyword_terms = _rank_weighted_terms(keyword_scores, keyword_support, top_k=top_k)
            else:
                keyword_terms = _rank_terms(
                    term_freq,
                    child_support,
                    min_support=min_support,
                    min_freq=min_freq,
                    top_k=top_k,
                )
            provider_counts[block_provider] += 1
            new_words = _rank_new_words(
                term_freq,
                child_support,
                left_neighbors,
                right_neighbors,
                min_support=min_support,
                min_freq=min_freq,
                top_k=top_k,
            )
            search_terms = []
            for value in [block.title, *(keyword_terms or []), *(item["term"] for item in new_words)]:
                if value and value not in search_terms:
                    search_terms.append(value)

            block.metadata["keyword_terms"] = keyword_terms
            block.metadata["new_word_terms"] = new_words
            block.metadata["search_terms"] = search_terms
            block.metadata["keyword_provider"] = block_provider
            block.metadata["keyword_provider_requested"] = requested_provider
            if block_provider == "lingxi":
                block.metadata["keyword_provider_version"] = lingxi_version
            else:
                block.metadata.pop("keyword_provider_version", None)
            if requested_provider == "lingxi":
                block.metadata["keyword_provider_requested_version"] = lingxi_version or PREFERRED_LINGXI_VERSION
            else:
                block.metadata.pop("keyword_provider_requested_version", None)
            if requested_provider == "lingxi" and block_provider != "lingxi":
                block.metadata["keyword_provider_fallback"] = provider_fallback_reason or "no_lingxi_candidates"
            block.metadata["keyword_thresholds"] = {
                "min_support": min_support,
                "min_freq": min_freq,
                "leaf_count": leaf_count,
                "text_length": text_length,
                "strategy": "bottom_up_aggregation",
            }
            keyword_stats = {
                "term_freq": dict(term_freq.most_common(self.max_stats_terms)),
                "child_support": dict(child_support.most_common(self.max_stats_terms)),
            }
            if keyword_scores:
                keyword_stats["keyword_scores"] = dict(keyword_scores.most_common(self.max_stats_terms))
                keyword_stats["keyword_support"] = dict(keyword_support.most_common(self.max_stats_terms))
            block.metadata["keyword_stats"] = keyword_stats

            aggregate[block.id] = {
                "term_freq": dict(term_freq.most_common(self.max_stats_terms)),
                "child_support": dict(child_support.most_common(self.max_stats_terms)),
                "keyword_scores": dict(keyword_scores.most_common(self.max_stats_terms)),
                "keyword_support": dict(keyword_support.most_common(self.max_stats_terms)),
                "left_neighbors": left_neighbors,
                "right_neighbors": right_neighbors,
                "leaf_count": leaf_count,
                "text_length": text_length,
            }
            changed += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=changed > 0,
            report={
                "blocks_enriched": changed,
                "strategy": "bottom_up_aggregation",
                "keyword_provider_requested": requested_provider,
                "keyword_provider_counts": dict(provider_counts),
                "keyword_provider_fallback": provider_fallback_reason,
                "new_word_provider": "ngram_boundary_entropy",
            },
        )
