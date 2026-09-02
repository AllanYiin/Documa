"""LingXi 繁體中文分詞與詞性標註（Rust 核心）。

用法::

    import lingxi
    seg = lingxi.load()          # 載入 wheel 內附模型；或 load(asset_dir=...)
    seg.cut("金管會前主委")       # -> list[str]
    seg.tokenize("...")          # -> list[Token(word, tag, start, end)]，字元座標
    lingxi.cut("...")            # 模組級便利函數（惰性單例）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from collections.abc import Iterable, Mapping

from ._core import (
    __version__,
    AnnotatedToken, Clause, Keyphrase, Segmenter, Sentence, SummaryBlock,
    SummaryBudget, SummaryDocument, SummaryScore, SummarySignals, Token,
)

__all__ = [
    "__version__",
    "AnnotatedToken", "Clause", "Keyphrase", "Segmenter", "Sentence", "SummaryBlock",
    "SummaryBudget", "SummaryDocument", "SummaryScore", "SummarySignals", "Token",
    "load", "cut", "tokenize", "annotate", "cut_batch", "extract_keywords",
    "split_sentences", "split_clauses", "extract_summary", "extract_keyphrases",
]

# wheel 內附模型目錄（maturin 將 python/lingxi/assets/ 打包進套件）。
_BUNDLED_ASSETS = Path(__file__).parent / "assets"


def load(
    asset_dir: str | os.PathLike | None = None,
    user_dict: str | os.PathLike | Iterable[str] | None = None,
    lexicons: Iterable[str | os.PathLike | Mapping] | None = None,
) -> Segmenter:
    """建立分詞器。

    asset_dir 省略時依序找：環境變數 LINGXI_ASSETS → wheel 內附模型。
    user_dict 為自訂詞典：檔案路徑，或詞條行的可迭代物件。
    詞條格式：``詞 頻率 [詞性]``；詞必須至少兩字，頻率必須是有限正數。
    詞性省略時固定使用 CKIP ``Na``。
    lexicons 可傳多個 schemaVersion 1 JSON 檔路徑或 Mapping；新格式不接受
    frequency，也不改變主詞典 total／log_prob。缺少 affect.bin 時仍可建立，
    annotate() 的 polarity 會為 None。
    """
    if asset_dir is None:
        asset_dir = os.environ.get("LINGXI_ASSETS") or _BUNDLED_ASSETS
    lines: list[str] | None = None
    if user_dict is not None:
        if isinstance(user_dict, (str, os.PathLike)):
            lines = Path(user_dict).read_text(encoding="utf-8").splitlines()
        else:
            lines = list(user_dict)
    lexicon_json: list[str] | None = None
    if lexicons is not None:
        lexicon_json = []
        for item in lexicons:
            if isinstance(item, Mapping):
                lexicon_json.append(json.dumps(dict(item), ensure_ascii=False))
            else:
                lexicon_json.append(Path(item).read_text(encoding="utf-8"))
    return Segmenter(
        str(asset_dir),
        lines,
        lexicon_json,
    )


_default: Segmenter | None = None


def _default_segmenter() -> Segmenter:
    global _default
    if _default is None:
        _default = load()
    return _default


def cut(text: str) -> list[str]:
    """惰性單例版分詞。"""
    return _default_segmenter().cut(text)


def tokenize(text: str) -> list[Token]:
    """惰性單例版分詞＋詞性。"""
    return _default_segmenter().tokenize(text)


def annotate(text: str) -> list[AnnotatedToken]:
    """惰性單例版分詞＋詞性＋詞級情感。"""
    return _default_segmenter().annotate(text)


def cut_batch(texts: list[str]) -> list[list[str]]:
    """惰性單例版批次分詞（平行、釋放 GIL）。"""
    return _default_segmenter().cut_batch(texts)


def extract_keywords(
    text: str,
    top_k: int = 20,
    allow_tags: list[str] | None = None,
    **options,
) -> list[tuple[str, float]]:
    """惰性單例版 TextRank 關鍵字抽取 → [(詞, 權重)]，權重降冪。"""
    return _default_segmenter().extract_keywords(text, top_k, allow_tags, **options)


def split_sentences(text: str, semicolon_boundary: bool = False) -> list[Sentence]:
    """惰性單例版中文斷句；start/end 為 Python 字元座標。"""
    return _default_segmenter().split_sentences(text, semicolon_boundary)


def split_clauses(text: str, **options) -> list[Clause]:
    """惰性單例版結構感知子句抽取；start/end 為 Python 字元座標。"""
    return _default_segmenter().split_clauses(text, **options)


def extract_summary(text: str, max_blocks: int = 3, **options) -> SummaryDocument:
    """惰性單例版 schema v2 結構感知摘要。"""
    return _default_segmenter().extract_summary(text, max_blocks, **options)


def extract_keyphrases(text: str, top_k: int = 10, **options) -> list[Keyphrase]:
    """惰性單例版相鄰關鍵短語抽取。"""
    return _default_segmenter().extract_keyphrases(text, top_k, **options)
