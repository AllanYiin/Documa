"""Unicode and text normalization helpers."""

from __future__ import annotations

import unicodedata
import re


DEFAULT_UNICODE_FORM = "NFC"


def normalize_unicode(text: str, form: str = DEFAULT_UNICODE_FORM) -> str:
    """Normalize text with an explicit Unicode normalization form."""

    if form not in {"NFC", "NFD", "NFKC", "NFKD"}:
        raise ValueError(f"Unsupported Unicode normalization form: {form}")
    return unicodedata.normalize(form, text)


def remove_utf8_bom(text: str) -> str:
    """Remove a leading UTF-8 BOM marker if present."""

    return text[1:] if text.startswith("\ufeff") else text


def normalize_for_search(text: str) -> str:
    """Return a conservative search key without mutating source text.

    NFKC is useful for matching full-width ASCII and compatibility forms, but
    it can alter evidence text. Keep it isolated from formal output fields.
    """

    return unicodedata.normalize("NFKC", text).casefold()


_DOCUMA_PAGE_MARKER_RE = re.compile(
    r"<!--\s*page(?:-(?:header|footer))?\s*:\s*.*?-->",
    flags=re.IGNORECASE | re.DOTALL,
)


def clean_retrieval_text(text: str) -> str:
    """Remove Documa page furniture comments from retrieval-facing text."""

    cleaned = _DOCUMA_PAGE_MARKER_RE.sub(" ", text)
    return "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
