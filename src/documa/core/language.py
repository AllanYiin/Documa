"""Language and script hints for Documa IR."""

from __future__ import annotations

from dataclasses import dataclass
import re


SUPPORTED_LANGUAGES = {"auto", "zh-Hant", "zh-Hans", "en"}
SUPPORTED_SCRIPTS = {None, "Traditional", "Simplified", "Latin", "Mixed", "Unknown"}


@dataclass(frozen=True, slots=True)
class LanguageHint:
    language: str = "auto"
    script: str | None = None
    text_direction: str = "ltr"

    def __post_init__(self) -> None:
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language hint: {self.language}")
        if self.script not in SUPPORTED_SCRIPTS:
            raise ValueError(f"Unsupported script hint: {self.script}")
        if self.text_direction not in {"ltr", "rtl", "vertical"}:
            raise ValueError(f"Unsupported text direction: {self.text_direction}")


def coerce_language_hint(value: str | None) -> LanguageHint:
    """Create a language hint from a BCP-47-like string."""

    return LanguageHint(language=value or "auto")


def detect_text_script(text: str) -> str:
    """Return a coarse script hint for Chinese/English text.

    This is intentionally conservative. It does not convert text; it only
    provides a metadata hint for downstream chunking and search.
    """

    has_cjk = bool(re.search(r"[\u3400-\u9fff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    if has_cjk and has_latin:
        return "Mixed"
    if has_cjk:
        traditional_markers = set("繁體臺灣國與學術資料圖說頁")
        simplified_markers = set("简体台湾国与学术资料图说页")
        trad_count = sum(1 for char in text if char in traditional_markers)
        simp_count = sum(1 for char in text if char in simplified_markers)
        return "Simplified" if simp_count > trad_count else "Traditional"
    if has_latin:
        return "Latin"
    return "Unknown"
