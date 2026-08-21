"""Source-preserving extractive summaries backed by local providers.

The summary is a disposable, derived view of ``DocumentIR``.  It never
rewrites source text or claims that selected clauses were generated facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as distribution_version
from typing import Any, Protocol

from documa.core.ir import DocumentIR
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata


DEFAULT_SUMMARY_PROVIDER = "lingxi"
MINIMUM_LINGXI_SUMMARY_VERSION = "0.3.0"
SUMMARY_ALGORITHM = "textrank_extractive"
_SUMMARY_FIELDS = (
    "text",
    "start",
    "end",
    "index",
    "clause_index",
    "weight",
    "explainability",
    "novelty",
    "coverage_gain",
)
_SIGNAL_FIELDS = (
    "proper_noun_count",
    "negation_count",
    "emphasis_count",
    "list_item",
    "object_name_count",
    "date_count",
    "number_count",
    "quantity_count",
    "acronym_count",
)
_SAFE_BREAK = re.compile(r"[\n\r。！？!?；;]", re.MULTILINE)


class SummaryError(RuntimeError):
    """A stable, structured summary capability error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SummaryProvider(Protocol):
    """Minimal local extractive-summary provider contract."""

    name: str
    version: str

    def extract_summary(self, text: str, top_k: int, **options: Any) -> list[Any]: ...


class SummaryTokenCounter(Protocol):
    """Optional exact/local tokenizer used only to report context reduction."""

    name: str

    def count(self, text: str) -> int: ...


@dataclass(slots=True)
class SummaryOptions:
    """Provider-neutral options supported by LingXi's extractive summarizer."""

    top_k: int = 8
    similarity: str = "bm25"
    min_explainability: float | None = 0.35
    redundancy_threshold: float | None = 0.8
    min_sentence_chars: int = 8
    min_token_chars: int = 1
    preserve_order: bool = True
    comma_boundary: bool = True
    semicolon_boundary: bool = True
    colon_boundary: bool = True
    max_window_chars: int = 40_000
    text_form: str = "normalized"

    def validate(self) -> None:
        if self.top_k < 1:
            raise SummaryError("SUMMARY_OPTIONS_INVALID", "top_k must be at least 1.")
        if self.similarity not in {"bm25", "lexical"}:
            raise SummaryError("SUMMARY_OPTIONS_INVALID", "similarity must be 'bm25' or 'lexical'.")
        if self.min_sentence_chars < 1 or self.min_token_chars < 1:
            raise SummaryError(
                "SUMMARY_OPTIONS_INVALID",
                "min_sentence_chars and min_token_chars must be at least 1.",
            )
        if self.max_window_chars < 1_000:
            raise SummaryError("SUMMARY_OPTIONS_INVALID", "max_window_chars must be at least 1000.")
        if self.text_form not in {"raw", "normalized"}:
            raise SummaryError("SUMMARY_OPTIONS_INVALID", "text_form must be 'raw' or 'normalized'.")
        for name, value in (
            ("min_explainability", self.min_explainability),
            ("redundancy_threshold", self.redundancy_threshold),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise SummaryError("SUMMARY_OPTIONS_INVALID", f"{name} must be between 0 and 1 or null.")

    def provider_arguments(self) -> dict[str, Any]:
        return {
            "similarity": self.similarity,
            "min_explainability": self.min_explainability,
            "redundancy_threshold": self.redundancy_threshold,
            "min_sentence_chars": self.min_sentence_chars,
            "min_token_chars": self.min_token_chars,
            "preserve_order": self.preserve_order,
            "comma_boundary": self.comma_boundary,
            "semicolon_boundary": self.semicolon_boundary,
            "colon_boundary": self.colon_boundary,
        }


@dataclass(slots=True)
class SummarySentence:
    """One exact clause selected from the input, plus evidence metadata."""

    text: str
    start: int
    end: int
    sentence_index: int
    clause_index: int
    weight: float
    explainability: float
    novelty: float
    coverage_gain: float
    signals: dict[str, Any] = field(default_factory=dict)
    block_ids: list[str] = field(default_factory=list)
    source_block_ids: list[str] = field(default_factory=list)
    page_refs: list[int] = field(default_factory=list)
    page: str | None = None


@dataclass(slots=True)
class SummaryResult:
    """A local, zero-LLM-token extractive summary result."""

    summary: str
    sentences: list[SummarySentence]
    provider: str
    provider_version: str
    algorithm: str = SUMMARY_ALGORITHM
    extractive: bool = True
    uses_llm: bool = False
    llm_tokens_used: int = 0
    input_chars: int = 0
    summary_chars: int = 0
    compression_ratio: float = 0.0
    requested_top_k: int = 0
    selection_count: int = 0
    selection_limit_kind: str = "soft"
    text_form: str = "input"
    offset_space: str = "summary_input_unicode_codepoint"
    input_tokens: int | None = None
    summary_tokens: int | None = None
    tokens_saved: int | None = None
    token_counter: str | None = None
    strategy: str = "single_pass"
    window_count: int = 1
    document_id: str | None = None
    scope_block_id: str | None = None


@dataclass(slots=True)
class _SourceSpan:
    start: int
    end: int
    block_id: str
    source_block_ids: list[str]
    page_refs: list[int]


@dataclass(slots=True)
class _SelectedClause:
    text: str
    start: int
    end: int
    sentence_index: int
    clause_index: int
    weight: float
    explainability: float
    novelty: float
    coverage_gain: float
    signals: dict[str, Any]


class _LingxiProvider:
    name = "lingxi"

    def __init__(self, segmenter: Any, version: str) -> None:
        self._segmenter = segmenter
        self.version = version

    def extract_summary(self, text: str, top_k: int, **options: Any) -> list[Any]:
        return self._segmenter.extract_summary(text, top_k, **options)


def _numeric_version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts[:3]) or (0,)


@lru_cache(maxsize=1)
def load_lingxi_summary_provider() -> SummaryProvider:
    """Load and validate the local LingXi extractive-summary capability."""

    try:
        installed_version = distribution_version("lingxi")
    except PackageNotFoundError as exc:
        raise SummaryError(
            "SUMMARY_PROVIDER_UNAVAILABLE",
            "LingXi with extract_summary() is not installed.",
        ) from exc
    if _numeric_version(installed_version) < _numeric_version(MINIMUM_LINGXI_SUMMARY_VERSION):
        raise SummaryError(
            "SUMMARY_PROVIDER_VERSION_UNSUPPORTED",
            f"LingXi >= {MINIMUM_LINGXI_SUMMARY_VERSION} is required for extractive summaries; "
            f"found {installed_version}.",
        )
    try:
        import lingxi

        segmenter = lingxi.load()
    except Exception as exc:
        raise SummaryError("SUMMARY_PROVIDER_UNAVAILABLE", f"LingXi could not be loaded: {exc}") from exc
    if not callable(getattr(segmenter, "extract_summary", None)):
        raise SummaryError(
            "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
            "The installed LingXi binding does not expose Segmenter.extract_summary().",
        )
    return _LingxiProvider(segmenter, installed_version)


def _provider(provider: str | SummaryProvider | None) -> SummaryProvider:
    if provider is None or provider == DEFAULT_SUMMARY_PROVIDER:
        return load_lingxi_summary_provider()
    if isinstance(provider, str):
        raise SummaryError("SUMMARY_PROVIDER_UNSUPPORTED", f"Unsupported summary provider: {provider}")
    if not callable(getattr(provider, "extract_summary", None)):
        raise SummaryError(
            "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
            "A summary provider requires name, version, and extract_summary(text, top_k, **options).",
        )
    return provider


def _coerce_clause(row: Any, source_text: str, *, offset: int = 0) -> _SelectedClause:
    missing = [name for name in _SUMMARY_FIELDS if not hasattr(row, name)]
    if missing:
        raise SummaryError(
            "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
            f"LingXi summary result is missing fields: {', '.join(missing)}.",
        )
    start = int(row.start)
    end = int(row.end)
    if start < 0 or end <= start or end > len(source_text):
        raise SummaryError(
            "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
            f"LingXi returned an invalid source span ({start}, {end}).",
        )
    exact_text = source_text[start:end]
    if exact_text.strip() != str(row.text).strip():
        raise SummaryError(
            "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
            "LingXi returned summary text that does not match its source span.",
        )
    return _SelectedClause(
        text=exact_text,
        start=start + offset,
        end=end + offset,
        sentence_index=int(row.index),
        clause_index=int(row.clause_index),
        weight=float(row.weight),
        explainability=float(row.explainability),
        novelty=float(row.novelty),
        coverage_gain=float(row.coverage_gain),
        signals={name: getattr(row, name) for name in _SIGNAL_FIELDS if hasattr(row, name)},
    )


def _extract_once(
    text: str,
    provider: SummaryProvider,
    options: SummaryOptions,
    *,
    top_k: int | None = None,
    offset: int = 0,
) -> list[_SelectedClause]:
    try:
        rows = provider.extract_summary(
            text,
            top_k if top_k is not None else options.top_k,
            **options.provider_arguments(),
        )
    except SummaryError:
        raise
    except Exception as exc:
        raise SummaryError("SUMMARY_PROVIDER_FAILED", f"{provider.name} summary failed: {exc}") from exc
    if not isinstance(rows, list):
        raise SummaryError("SUMMARY_PROVIDER_CONTRACT_MISMATCH", "Summary provider must return a list.")
    return [_coerce_clause(row, text, offset=offset) for row in rows]


def _text_windows(text: str, max_chars: int) -> list[tuple[int, int]]:
    if len(text) <= max_chars:
        return [(0, len(text))]
    windows = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            floor = start + max_chars // 2
            candidates = [match.end() for match in _SAFE_BREAK.finditer(text, floor, end)]
            if candidates:
                end = candidates[-1]
        windows.append((start, end))
        start = end
    return windows


def _extract_hierarchical(
    text: str,
    provider: SummaryProvider,
    options: SummaryOptions,
) -> tuple[list[_SelectedClause], str, int]:
    windows = _text_windows(text, options.max_window_chars)
    if len(windows) == 1:
        return _extract_once(text, provider, options), "single_pass", 1

    local_top_k = max(options.top_k, 8)
    candidates: list[_SelectedClause] = []
    seen: set[tuple[int, int]] = set()
    for start, end in windows:
        for clause in _extract_once(text[start:end], provider, options, top_k=local_top_k, offset=start):
            marker = (clause.start, clause.end)
            if marker not in seen:
                seen.add(marker)
                candidates.append(clause)
    candidates.sort(key=lambda item: (item.start, item.end))
    if not candidates:
        return [], "hierarchical_windows", len(windows)

    candidate_text_parts = []
    candidate_spans: list[tuple[int, int, _SelectedClause]] = []
    cursor = 0
    for candidate in candidates:
        if candidate_text_parts:
            candidate_text_parts.append("\n\n")
            cursor += 2
        start = cursor
        candidate_text_parts.append(candidate.text)
        cursor += len(candidate.text)
        candidate_spans.append((start, cursor, candidate))
    candidate_text = "".join(candidate_text_parts)
    final_rows = _extract_once(candidate_text, provider, options)
    selected = []
    for final in final_rows:
        matches = [item for start, end, item in candidate_spans if final.start < end and final.end > start]
        if len(matches) != 1:
            raise SummaryError(
                "SUMMARY_PROVIDER_CONTRACT_MISMATCH",
                "Hierarchical summary clause does not map to exactly one source clause.",
            )
        original = matches[0]
        selected.append(
            _SelectedClause(
                text=original.text,
                start=original.start,
                end=original.end,
                sentence_index=original.sentence_index,
                clause_index=original.clause_index,
                weight=final.weight,
                explainability=final.explainability,
                novelty=final.novelty,
                coverage_gain=final.coverage_gain,
                signals=final.signals,
            )
        )
    if options.preserve_order:
        selected.sort(key=lambda item: (item.start, item.end))
    return selected, "hierarchical_windows", len(windows)


def _result(
    text: str,
    selected: list[_SelectedClause],
    provider: SummaryProvider,
    options: SummaryOptions,
    *,
    strategy: str,
    window_count: int,
    source_spans: list[_SourceSpan] | None = None,
    page_citations: dict[str, dict[str, Any]] | None = None,
    document_id: str | None = None,
    scope_block_id: str | None = None,
    token_counter: SummaryTokenCounter | None = None,
    text_form: str = "input",
) -> SummaryResult:
    sentences = []
    for clause in selected:
        overlaps = [
            span
            for span in source_spans or []
            if clause.start < span.end and clause.end > span.start
        ]
        page_refs = list(dict.fromkeys(page for span in overlaps for page in span.page_refs))
        citation = page_citation_metadata(page_refs, page_citations or {}).get("citation_label") if page_refs else None
        sentences.append(
            SummarySentence(
                text=clause.text,
                start=clause.start,
                end=clause.end,
                sentence_index=clause.sentence_index,
                clause_index=clause.clause_index,
                weight=clause.weight,
                explainability=clause.explainability,
                novelty=clause.novelty,
                coverage_gain=clause.coverage_gain,
                signals=clause.signals,
                block_ids=list(dict.fromkeys(span.block_id for span in overlaps)),
                source_block_ids=list(
                    dict.fromkeys(source_id for span in overlaps for source_id in span.source_block_ids)
                ),
                page_refs=page_refs,
                page=citation,
            )
        )
    summary = "\n".join(sentence.text for sentence in sentences)
    input_tokens = token_counter.count(text) if token_counter is not None else None
    summary_tokens = token_counter.count(summary) if token_counter is not None else None
    return SummaryResult(
        summary=summary,
        sentences=sentences,
        provider=provider.name,
        provider_version=provider.version,
        input_chars=len(text),
        summary_chars=len(summary),
        compression_ratio=round(len(summary) / len(text), 6) if text else 0.0,
        requested_top_k=options.top_k,
        selection_count=len(sentences),
        text_form=text_form,
        input_tokens=input_tokens,
        summary_tokens=summary_tokens,
        tokens_saved=(input_tokens - summary_tokens) if input_tokens is not None and summary_tokens is not None else None,
        token_counter=token_counter.name if token_counter is not None else None,
        strategy=strategy,
        window_count=window_count,
        document_id=document_id,
        scope_block_id=scope_block_id,
    )


def summarize_text(
    text: str,
    options: SummaryOptions | None = None,
    *,
    provider: str | SummaryProvider | None = None,
    token_counter: SummaryTokenCounter | None = None,
) -> SummaryResult:
    """Summarize plain text locally without using an LLM or changing the text."""

    if not isinstance(text, str):
        raise SummaryError("SUMMARY_INPUT_INVALID", "text must be a Unicode string.")
    options = options or SummaryOptions()
    options.validate()
    active_provider = _provider(provider)
    if not text.strip():
        return _result(
            text,
            [],
            active_provider,
            options,
            strategy="single_pass",
            window_count=1,
            token_counter=token_counter,
        )
    selected, strategy, window_count = _extract_hierarchical(text, active_provider, options)
    return _result(
        text,
        selected,
        active_provider,
        options,
        strategy=strategy,
        window_count=window_count,
        token_counter=token_counter,
    )


def _descendant_ids(document: DocumentIR, scope_block_id: str) -> set[str]:
    by_id = {block.id: block for block in document.document_blocks}
    if scope_block_id not in by_id:
        raise SummaryError("SUMMARY_SCOPE_NOT_FOUND", f"Unknown scope_block_id: {scope_block_id}")
    selected = {scope_block_id}
    pending = [scope_block_id]
    while pending:
        current = by_id[pending.pop()]
        for child_id in current.child_ids:
            if child_id in by_id and child_id not in selected:
                selected.add(child_id)
                pending.append(child_id)
    return selected


def _document_text(
    document: DocumentIR,
    scope_block_id: str | None,
    text_form: str,
) -> tuple[str, list[_SourceSpan]]:
    if not document.document_blocks:
        raise SummaryError(
            "SUMMARY_BLOCKS_REQUIRED",
            "DocumentIR has no document_blocks; run the block-tree pipeline first.",
        )
    scoped = _descendant_ids(document, scope_block_id) if scope_block_id else None
    source_text = {}
    for page in document.pages:
        for source in page.blocks:
            if source.text is None:
                source_text[source.id] = ""
            elif text_form == "raw":
                source_text[source.id] = source.text.raw_text
            else:
                source_text[source.id] = source.text.normalized_text or source.text.raw_text
    seen_sources: set[str] = set()
    parts: list[str] = []
    spans: list[_SourceSpan] = []
    cursor = 0
    ordered = sorted(document.document_blocks, key=lambda block: (block.order_index is None, block.order_index or 0))
    for block in ordered:
        if scoped is not None and block.id not in scoped:
            continue
        source_ids = [
            source_id
            for source_id in block.source_block_ids
            if source_id not in seen_sources and source_text.get(source_id, "").strip()
        ]
        if not source_ids:
            continue
        for source_id in source_ids:
            if parts:
                parts.append("\n\n")
                cursor += 2
            start = cursor
            value = source_text[source_id].strip()
            parts.append(value)
            cursor += len(value)
            spans.append(
                _SourceSpan(
                    start=start,
                    end=cursor,
                    block_id=block.id,
                    source_block_ids=[source_id],
                    page_refs=block.page_refs,
                )
            )
        seen_sources.update(source_ids)
    return "".join(parts), spans


def summarize_document(
    document: DocumentIR,
    options: SummaryOptions | None = None,
    *,
    scope_block_id: str | None = None,
    provider: str | SummaryProvider | None = None,
    token_counter: SummaryTokenCounter | None = None,
) -> SummaryResult:
    """Summarize a document or subtree and retain block/page evidence refs."""

    options = options or SummaryOptions()
    options.validate()
    active_provider = _provider(provider)
    text, source_spans = _document_text(document, scope_block_id, options.text_form)
    selected, strategy, window_count = _extract_hierarchical(text, active_provider, options) if text else ([], "single_pass", 1)
    return _result(
        text,
        selected,
        active_provider,
        options,
        strategy=strategy,
        window_count=window_count,
        source_spans=source_spans,
        page_citations=ensure_page_citation_map(document),
        document_id=document.id,
        scope_block_id=scope_block_id,
        token_counter=token_counter,
        text_form=options.text_form,
    )


__all__ = [
    "DEFAULT_SUMMARY_PROVIDER",
    "MINIMUM_LINGXI_SUMMARY_VERSION",
    "SUMMARY_ALGORITHM",
    "SummaryError",
    "SummaryOptions",
    "SummaryProvider",
    "SummaryTokenCounter",
    "SummaryResult",
    "SummarySentence",
    "load_lingxi_summary_provider",
    "summarize_document",
    "summarize_text",
]
