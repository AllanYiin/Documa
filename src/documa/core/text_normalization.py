"""Unicode and text normalization helpers."""

from __future__ import annotations

import unicodedata


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
