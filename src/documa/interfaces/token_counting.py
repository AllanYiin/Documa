"""Pluggable token counting for query budgets and selection metadata.

Policy: token numbers come from a real tokenizer or counting API — never from
character-ratio guesses (chars/4 and friends are banned). When no counter is
available, tools report token fields as null and token-budget parameters fail
with TOKEN_COUNTER_UNAVAILABLE instead of returning fabricated numbers.

Core stays dependency-free; concrete counters resolve lazily in this order:

1. ``set_token_counter(...)`` — explicit programmatic choice (``None`` disables
   counting entirely; ``reset_token_counter()`` restores auto-detection).
2. ``DOCUMA_TOKEN_COUNTER`` environment variable:
   ``tiktoken`` / ``tiktoken:<encoding>`` — local BPE counting (OpenAI models);
   ``anthropic`` / ``anthropic:<model>`` — Claude counting via the Anthropic
   count-tokens API (needs the ``anthropic`` package and ``ANTHROPIC_API_KEY``);
   ``none`` — disable counting.
3. Auto-detection: tiktoken when importable, otherwise no counter.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Protocol

DEFAULT_TIKTOKEN_ENCODING = "o200k_base"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
UNAVAILABLE_MESSAGE = (
    "TOKEN_COUNTER_UNAVAILABLE: no token counter is configured. Install the "
    "'tokens' extra (tiktoken), set DOCUMA_TOKEN_COUNTER, or call "
    "documa.interfaces.token_counting.set_token_counter()."
)


class TokenCounter(Protocol):
    """A real tokenizer or counting API; heuristics do not qualify."""

    name: str

    def count(self, text: str) -> int: ...

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]: ...


class TiktokenCounter:
    """Local BPE counting via tiktoken — the counter for OpenAI-family models."""

    def __init__(self, encoding: str = DEFAULT_TIKTOKEN_ENCODING) -> None:
        import tiktoken

        self.name = f"tiktoken:{encoding}"
        self._encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text))

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        if not text:
            return text, False
        tokens = self._encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text, False
        return self._encoding.decode(tokens[:max_tokens]), True


class AnthropicTokenCounter:
    """Claude counting via the Anthropic count-tokens API.

    Every uncached ``count`` is a network round-trip, so results are cached by
    content hash for the counter's lifetime. Counts include the single-message
    envelope the API wraps around raw text (a small constant). ``truncate``
    binary-searches the character prefix, costing O(log n) counted prefixes.
    Best suited to bounding final reads; for high-volume per-row estimates
    prefer tiktoken and accept the cross-tokenizer approximation explicitly.
    """

    _CACHE_MAX_ENTRIES = 4096

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, client: Any = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.name = f"anthropic:{model}"
        self._model = model
        self._client = client
        self._cache: dict[str, int] = {}

    def count(self, text: str) -> int:
        if not text:
            return 0
        key = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        response = self._client.messages.count_tokens(
            model=self._model,
            messages=[{"role": "user", "content": text}],
        )
        tokens = int(response.input_tokens)
        if len(self._cache) >= self._CACHE_MAX_ENTRIES:
            self._cache.clear()
        self._cache[key] = tokens
        return tokens

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        if not text or self.count(text) <= max_tokens:
            return text, False
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self.count(text[:mid]) <= max_tokens:
                low = mid
            else:
                high = mid - 1
        return text[:low], True


_UNSET = object()
_override: Any = _UNSET
_resolved: Any = _UNSET


def set_token_counter(counter: TokenCounter | None) -> None:
    """Set the active counter explicitly; ``None`` disables token counting."""
    global _override, _resolved
    _override = counter
    _resolved = _UNSET


def reset_token_counter() -> None:
    """Clear any override and cached resolution; auto-detection applies again."""
    global _override, _resolved
    _override = _UNSET
    _resolved = _UNSET


def _resolve() -> TokenCounter | None:
    spec = os.environ.get("DOCUMA_TOKEN_COUNTER", "").strip()
    if spec:
        kind, _, argument = spec.partition(":")
        kind = kind.casefold()
        try:
            if kind == "none":
                return None
            if kind == "tiktoken":
                return TiktokenCounter(argument or DEFAULT_TIKTOKEN_ENCODING)
            if kind == "anthropic":
                return AnthropicTokenCounter(argument or DEFAULT_ANTHROPIC_MODEL)
        except Exception:
            return None
        return None
    try:
        return TiktokenCounter()
    except Exception:
        return None


def get_token_counter() -> TokenCounter | None:
    """Return the active token counter, or None when counting is unavailable."""
    global _resolved
    if _override is not _UNSET:
        return _override
    if _resolved is _UNSET:
        candidate = _resolve()
        # Cache successful resolution and an explicit opt-out.  An import or
        # tokenizer initialization failure can be transient during host/MCP
        # startup, so do not poison the process for its entire lifetime by
        # caching an automatically detected ``None`` result.
        spec = os.environ.get("DOCUMA_TOKEN_COUNTER", "").strip()
        if candidate is not None or spec.partition(":")[0].casefold() == "none":
            _resolved = candidate
        return candidate
    return _resolved
