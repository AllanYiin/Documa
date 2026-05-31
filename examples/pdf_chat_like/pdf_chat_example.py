"""PDF chat-like progressive reading example.

This script is an example, not a Documa core UI. It loads a PDF once, builds
Documa document blocks, then answers one or more questions through a traceable
tool-like sequence: search blocks, read selected blocks, synthesize.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from documa.adapters.base import ParseOptions  # noqa: E402
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter  # noqa: E402
from documa.core.ir import DocumentBlockIR, DocumentBlockType, DocumentIR  # noqa: E402
from documa.exporters import BlockJsonExporter, ExportOptions, JsonExporter  # noqa: E402
from documa.pipeline import PipelineContext, run_default_pipeline  # noqa: E402
from documa.pipeline.block_tree import document_block_text  # noqa: E402


_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_+\-]{2,}")
_EN_KEYWORD = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]{1,}(?:\.[A-Za-z0-9_+\-]+)*")
_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w.\-]+\.[A-Za-z]{2,}\b")
_ARXIV_RE = re.compile(r"\barxiv\s*:?\s*\d{4}\.\d{4,5}(?:v\d+)?\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{4}\b", re.IGNORECASE)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*|\n+")
_SEARCH_WORD_WINDOW = re.compile(r"\S+")
_SEARCH_FIELD_WEIGHTS = {
    "title": 4.0,
    "keywords": 3.0,
    "new_words": 3.0,
    "search_terms": 2.0,
    "preview": 1.0,
    "body": 1.0,
}
_DEFAULT_SNIPPET_FIELDS = {"body", "title", "preview"}
_SEARCH_VERBOSITIES = {"compact", "standard", "debug"}
_LOW_VALUE_HEADINGS = {
    "abstract",
    "acknowledgements",
    "acknowledgments",
    "appendix",
    "conclusion",
    "contents",
    "executive summary",
    "introduction",
    "overview",
    "references",
    "summary",
    "table of contents",
    "top takeaways",
}
_LOW_VALUE_TERMS = _LOW_VALUE_HEADINGS | {
    "and",
    "are",
    "between",
    "can",
    "does",
    "edu",
    "for",
    "from",
    "has",
    "have",
    "into",
    "its",
    "may",
    "page",
    "pdf",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "using",
    "was",
    "were",
    "will",
    "with",
}
_PATH_LOW_VALUE_TERMS = {"users", "appdata", "local", "temp", "tmp", "downloads", "documents"}
_OVERVIEW_HINTS = ["abstract", "introduction", "overview", "summary", "conclusion"]
_OVERVIEW_QUERY_RE = re.compile(
    r"(論文|文章|文件|paper|document).*(主要|大意|摘要|講|說|about|summary|summarize)|"
    r"(主要|大意|摘要|講|說|about|summary|summarize).*(論文|文章|文件|paper|document)",
    re.IGNORECASE,
)
SYSTEM_PROMPT_ZH_HANT = (
    "你是 Documa PDF 文件理解助理。請一律以繁體中文回答使用者問題；"
    "回答要先給結論，再列出可追溯依據。若引用 PDF 原文作為證據，"
    "可以保留原文語言，但解釋與總結必須使用繁體中文。"
)
LLM_SYNTHESIS_PROMPT = (
    SYSTEM_PROMPT_ZH_HANT
    + "\n你會收到使用者問題、搜尋計畫，以及已讀取的 PDF block 原文證據。"
    + "請用繁體中文回答，先給結論，再列重點與依據。"
    + "引用證據時保留原文短句並標明頁碼/block id；不要捏造未出現在證據中的內容。"
)
LLM_SEARCH_TERMS_PROMPT = (
    SYSTEM_PROMPT_ZH_HANT
    + "\n你只負責為 search_blocks 產生少量、高語意覆蓋度的雙語搜尋關鍵詞，不要回答問題。"
    + "請輸出 JSON 物件，欄位 query_terms 為 4 到 6 個 phrases。"
    + "每個 phrase 要短、可直接匹配文件內容；若使用者是中文但文件關鍵詞偏英文，英文詞優先，"
    + "並補少量中文語意詞。避免同義詞堆疊、避免過多泛詞、不要輸出 markdown。"
)
LLM_TOOL_CALLING_PROMPT = (
    SYSTEM_PROMPT_ZH_HANT
    + "\n你正在使用標準 function tool calling 閱讀一份 PDF。文件全文尚未直接提供給你。"
    + "請自己決定何時呼叫 search_blocks、read_block、list_blocks。"
    + "建議先呼叫 search_blocks 找候選 block；snippet 不足時再 read_block。"
    + "回答必須根據工具回傳內容，並標明頁碼與 block id；不要捏造未在工具結果中的內容。"
)
_ZH_EN_QUERY_EXPANSIONS = [
    (re.compile(r"(論文|文章|文件|paper|document).*(主要|大意|摘要|講|說|內容|主題)|"
                r"(主要|大意|摘要|講|說|內容|主題).*(論文|文章|文件|paper|document)", re.IGNORECASE),
     ["paper", "topic", "overview", "summary", "abstract", "introduction", "conclusion"]),
    (re.compile(r"(方法|做法|技術|架構|流程|method|approach)", re.IGNORECASE),
     ["method", "approach", "architecture", "workflow"]),
    (re.compile(r"(結果|發現|成效|表現|result|finding|performance)", re.IGNORECASE),
     ["result", "finding", "performance", "evaluation"]),
    (re.compile(r"(實驗|評估|benchmark|experiment|evaluation)", re.IGNORECASE),
     ["experiment", "evaluation", "benchmark"]),
    (re.compile(r"(貢獻|創新|contribution|novelty)", re.IGNORECASE),
     ["contribution", "novelty"]),
    (re.compile(r"(模型|語言模型|model|language model)", re.IGNORECASE),
     ["model", "language model"]),
    (re.compile(r"(成本|token|延遲|效率|cost|latency|efficiency)", re.IGNORECASE),
     ["cost", "token", "latency", "efficiency"]),
    (re.compile(r"(風險|限制|問題|缺點|risk|limitation|problem)", re.IGNORECASE),
     ["risk", "limitation", "problem"]),
]


def write_json(path: str | Path, payload: Any) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


@dataclass(slots=True)
class TokenCounter:
    backend: str
    _encoding: Any = None

    @classmethod
    def create(cls) -> "TokenCounter":
        try:
            import tiktoken  # type: ignore

            return cls(backend="tiktoken:cl100k_base", _encoding=tiktoken.get_encoding("cl100k_base"))
        except Exception:
            return cls(backend="heuristic:chars_div_4_cjk_adjusted")

    def count(self, value: Any) -> int:
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if self._encoding is not None:
            return len(self._encoding.encode(value))
        cjk_count = sum(1 for char in value if "\u4e00" <= char <= "\u9fff")
        non_cjk = max(len(value) - cjk_count, 0)
        return max(1, math.ceil(cjk_count * 0.8 + non_cjk / 4))


@dataclass(slots=True)
class TraceRecorder:
    counter: TokenCounter
    events: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        event_type: str,
        name: str,
        payload: Any,
        *,
        started_at: float | None = None,
        response_usage: dict[str, Any] | None = None,
    ) -> None:
        estimated_count = self.counter.count(payload)
        token_usage = normalize_response_usage(response_usage) if response_usage else local_token_usage(estimated_count)
        event = {
            "event_index": len(self.events) + 1,
            "type": event_type,
            "name": name,
            "payload": payload,
            "token_count": token_usage["total_tokens"],
            "token_usage": token_usage,
        }
        if started_at is not None:
            event["elapsed_ms"] = round((time.perf_counter() - started_at) * 1000, 3)
        self.events.append(event)


def local_token_usage(token_count: int) -> dict[str, Any]:
    return {
        "source": "local_estimate",
        "estimated": True,
        "input_tokens": 0,
        "cached_tokens": 0,
        "cache_eligible": False,
        "cache_min_input_tokens": 1024,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": int(token_count),
    }


def normalize_response_usage(usage: dict[str, Any]) -> dict[str, Any]:
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int(input_details.get("cached_tokens") or 0)
    reasoning_tokens = int(output_details.get("reasoning_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "source": "response_api",
        "estimated": False,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_eligible": input_tokens >= 1024,
        "cache_min_input_tokens": 1024,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def aggregate_token_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    usage_items = [event.get("token_usage", {}) for event in events]
    response_items = [item for item in usage_items if item.get("source") == "response_api"]
    selected_items = response_items or usage_items
    return {
        "source": "response_api" if response_items else "local_estimate",
        "estimated": not bool(response_items),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in selected_items),
        "cached_tokens": sum(int(item.get("cached_tokens") or 0) for item in selected_items),
        "cache_eligible": any(bool(item.get("cache_eligible")) for item in selected_items),
        "cache_min_input_tokens": 1024,
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in selected_items),
        "reasoning_tokens": sum(int(item.get("reasoning_tokens") or 0) for item in selected_items),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in selected_items),
    }


def add_response_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
    if usage.get("source") != "response_api":
        return
    for key in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        total[key] = int(total.get(key) or 0) + int(usage.get(key) or 0)
    total["cache_eligible"] = bool(total.get("cache_eligible")) or bool(usage.get("cache_eligible"))


def request_debug_payload(input_text: str, counter: TokenCounter) -> dict[str, Any]:
    prefix = input_text[:4096]
    return {
        "input_tokens_estimate": counter.count(input_text),
        "prefix_chars": len(prefix),
        "prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(),
        "full_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
        "prefix_preview": prefix[:500],
    }


def plain_data(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [plain_data(item) for item in value]
    if isinstance(value, tuple):
        return [plain_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): plain_data(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return plain_data(value.model_dump())
    if hasattr(value, "to_dict"):
        return plain_data(value.to_dict())
    return value


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    data = plain_data(response)
    chunks = []
    for item in data.get("output", []) if isinstance(data, dict) else []:
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                chunks.append(str(content.get("text", "")))
    return "".join(chunks)


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            value = json.loads(stripped[start : end + 1])
            return value if isinstance(value, dict) else {}
        raise


class OpenAIResponsesClient:
    def __init__(self, *, model: str | None = None):
        from openai import OpenAI  # type: ignore

        self.model = model or os.environ.get("OPENAI_MODEL") or "gpt-5.4-mini"
        self.client = OpenAI()

    @classmethod
    def available(cls) -> bool:
        if not os.environ.get("OPENAI_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
        except Exception:
            return False
        return True

    def create(
        self,
        *,
        instructions: str,
        input_text: Any,
        max_output_tokens: int = 700,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": max_output_tokens,
        }
        if tools:
            request["tools"] = tools
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
            request["store"] = True
        prompt_cache_retention = os.environ.get("OPENAI_PROMPT_CACHE_RETENTION")
        cache_body: dict[str, Any] = {}
        if prompt_cache_key:
            cache_body["prompt_cache_key"] = prompt_cache_key
        if prompt_cache_retention:
            cache_body["prompt_cache_retention"] = prompt_cache_retention
        if cache_body:
            request["extra_body"] = cache_body
        response = self.client.responses.create(**request)
        usage = plain_data(getattr(response, "usage", None)) or {}
        output_items = plain_data(getattr(response, "output", [])) or []
        return {
            "response_id": getattr(response, "id", None),
            "model": getattr(response, "model", self.model),
            "text": extract_response_text(response),
            "output": output_items,
            "usage": usage,
        }


def query_terms(query: str) -> list[str]:
    terms = [
        match.group(0).casefold()
        for match in _WORD.finditer(query)
        if match.group(0).casefold() not in _LOW_VALUE_TERMS
    ]
    for match in _CJK.finditer(query):
        chars = match.group(0)
        compact = re.sub(r"[這那本篇份個的了嗎呢啊麼甚什在是有請幫我你他她它和與及或]", " ", chars)
        for term in re.split(r"\s+", compact):
            if len(term) >= 2:
                terms.append(term)
    output: list[str] = []
    for term in terms or [query.casefold()]:
        if term and term not in output:
            output.append(term)
    return output[:8]


def bilingual_query_terms(query: str) -> list[str]:
    terms = query_terms(query)
    for pattern, expanded_terms in _ZH_EN_QUERY_EXPANSIONS:
        if pattern.search(query):
            terms.extend(expanded_terms)
    output: list[str] = []
    for term in terms:
        folded = str(term).strip().casefold()
        if folded and folded not in output:
            output.append(folded)
    return output[:12]


def build_search_plan(query: str) -> dict[str, Any]:
    terms = bilingual_query_terms(query)
    is_overview = bool(_OVERVIEW_QUERY_RE.search(query))
    return {
        "intent": "overview" if is_overview else "lookup",
        "terms": terms,
        "structural_hints": _OVERVIEW_HINTS if is_overview else [],
        "terms_source": "deterministic_bilingual",
        "requires_llm_terms": False,
        "note": "Deterministic bilingual query expansion; search starts directly from search_blocks.",
    }


def local_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "search_blocks",
            "description": "Search Documa PDF blocks. Returns compact metadata and bounded snippets, not full block content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "terms": {"type": "array", "items": {"type": "string"}},
                    "search_body": {"type": "boolean", "default": True},
                    "max_snippets_per_block": {"type": "integer", "minimum": 0, "maximum": 5, "default": 3},
                    "verbosity": {"type": "string", "enum": ["compact", "standard", "debug"], "default": "compact"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "read_block",
            "description": "Read the full body text for one selected document block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "block_id": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 8000, "default": 2200},
                },
                "required": ["block_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_blocks",
            "description": "List document blocks without body text. Use when search results are insufficient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "depth": {"type": "integer", "minimum": 0, "maximum": 4, "default": 2},
                },
                "additionalProperties": False,
            },
        },
    ]


def meaningful_quote(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    folded = compact.casefold()
    if len(compact) < 12:
        return False
    if folded in _LOW_VALUE_HEADINGS:
        return False
    if re.fullmatch(r"[\d\s.,:/()\-–—]+", compact):
        return False
    cjk_or_letters = re.findall(r"[\u4e00-\u9fffA-Za-z]", compact)
    if len(cjk_or_letters) < 8:
        return False
    return True


def search_snippet(text: str, start: int, end: int, keyword: str, *, chars: int = 24, words: int = 8) -> str:
    if _CJK.search(keyword):
        left = max(0, start - chars)
        right = min(len(text), end + chars)
    else:
        prefix_matches = list(_SEARCH_WORD_WINDOW.finditer(text[:start]))
        suffix_matches = list(_SEARCH_WORD_WINDOW.finditer(text[end:]))
        left = prefix_matches[-words].start() if len(prefix_matches) > words else 0
        right = end + suffix_matches[words - 1].end() if len(suffix_matches) > words else len(text)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def normalized_search_verbosity(value: str | None) -> str:
    verbosity = (value or "compact").casefold()
    return verbosity if verbosity in _SEARCH_VERBOSITIES else "compact"


def evidence_line(item: dict[str, Any]) -> str:
    pages = ",".join(str(page) for page in item["page_refs"]) or "?"
    return f"- p.{pages} / {item['block_id']}: {item['quote']}"


def content_block(block: DocumentBlockIR) -> bool:
    return block.type in {DocumentBlockType.PARAGRAPH, DocumentBlockType.TABLE, DocumentBlockType.FOOTNOTE}


def block_path(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> list[str]:
    path = []
    current: DocumentBlockIR | None = block
    while current is not None:
        if current.title:
            path.append(safe_display_path_segment(current.title))
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def safe_display_path_segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if re.search(r"^[A-Za-z]:[\\/]|[\\/]", text):
        return PureWindowsPath(text).name or Path(text).name or "document.pdf"
    return text


def keyword_source_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"^[A-Za-z]:[\\/]|[\\/]", text):
        return safe_display_path_segment(text)
    return text


def inherited_heading_title(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> str | None:
    current = block
    while current is not None:
        if current.title and not re.fullmatch(r"Page\s+\d+", current.title, flags=re.IGNORECASE):
            return current.title
        current = by_id.get(current.parent_id) if current.parent_id else None
    return block.title


def normalize_keyword_text(text: str) -> str:
    cleaned = _EMAIL_RE.sub(" ", text)
    cleaned = _ARXIV_RE.sub(" ", cleaned)
    cleaned = _DATE_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("@", " ")
    cleaned = _CAMEL_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", cleaned)
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_+\-\s.]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def keyword_tokens(text: str) -> list[str]:
    normalized = normalize_keyword_text(text)
    tokens = []
    for match in _EN_KEYWORD.finditer(normalized):
        token = match.group(0).strip(".").casefold()
        if not valid_keyword_token(token):
            continue
        tokens.append(token)
    return tokens


def valid_keyword_token(token: str) -> bool:
    if len(token) < 3 or token in _LOW_VALUE_TERMS:
        return False
    if token in _PATH_LOW_VALUE_TERMS or token.endswith(".pdf") or "documa_pdf_chat" in token:
        return False
    if "_" in token and len(token) > 24:
        return False
    if token.isdigit() or re.fullmatch(r"v?\d+", token):
        return False
    if re.fullmatch(r"\d+k", token):
        return False
    if re.fullmatch(r"[a-z]{2}\.[a-z]{2}", token):
        return False
    if token in {"arxiv", "csail", "mit"}:
        return False
    return True


def metadata_keyword_label(text: str) -> str | None:
    if _EMAIL_RE.search(text):
        return "author metadata"
    if _ARXIV_RE.search(text) or _DATE_RE.search(text):
        return "paper metadata"
    return None


def valid_keyword_phrase(words: list[str]) -> bool:
    if not words or all(word in _LOW_VALUE_TERMS for word in words):
        return False
    if len(set(words)) < len(words):
        return False
    if len(words) > 1 and any(words[index] == words[index - 1] for index in range(1, len(words))):
        return False
    return True


def trim_repeated_token_loop(words: list[str]) -> list[str]:
    if len(words) > 12:
        return words
    seen: set[str] = set()
    output = []
    for word in words:
        if word in seen and len(output) >= 2:
            break
        seen.add(word)
        output.append(word)
    return output or words


def clean_keyword_candidate(value: Any) -> str | None:
    if metadata_keyword_label(str(value or "")):
        return None
    text = normalize_keyword_text(str(value or ""))
    if not text:
        return None
    words = keyword_tokens(text)
    cjk_terms = [
        match.group(0)
        for match in _CJK.finditer(text)
        if len(match.group(0)) >= 2 and not re.fullmatch(r"[\d\s]+", match.group(0))
    ]
    if words:
        if not valid_keyword_phrase(words):
            return None
        return " ".join(words[:3])
    if cjk_terms:
        return cjk_terms[0][:12]
    return None


def ngram_keywords(text: str, *, top_k: int = 8) -> list[str]:
    if metadata_keyword_label(text):
        return []
    terms: Counter[str] = Counter()
    words = trim_repeated_token_loop(keyword_tokens(text))
    for size in range(1, 4):
        for start in range(0, max(len(words) - size + 1, 0)):
            phrase_words = words[start : start + size]
            if not valid_keyword_phrase(phrase_words):
                continue
            term = " ".join(phrase_words)
            terms[term] += size * min(len(term), 24)
    for match in _CJK.finditer(text):
        chars = match.group(0)
        for size in range(2, min(8, len(chars)) + 1):
            for start in range(0, len(chars) - size + 1):
                term = chars[start : start + size]
                terms[term] += size
    ranked = sorted(terms.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
    return [term for term, _ in ranked[:top_k]]


def raw_block_keywords(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR], document: DocumentIR, *, top_k: int = 24) -> list[str]:
    output = []
    for value in [
        *(block.metadata.get("keyword_terms", []) or []),
        *(item.get("term") for item in block.metadata.get("new_word_terms", []) if isinstance(item, dict)),
        *(block.metadata.get("search_terms", []) or []),
    ]:
        text = clean_keyword_candidate(value)
        if text and text.casefold() not in {item.casefold() for item in output}:
            output.append(text)
        if len(output) >= top_k:
            return output

    fallback_text = "\n".join(
        part
        for part in [
            keyword_source_text(inherited_heading_title(block, by_id) or ""),
            keyword_source_text(block.text_preview or ""),
            keyword_source_text(document_block_text(document, block)),
        ]
        if part
    )
    output.extend(term for term in ngram_keywords(fallback_text, top_k=top_k) if term not in output)
    if not output:
        output.append(metadata_keyword_label(fallback_text) or block.type.value)
    return output[:top_k]


def tfidf_keyword_index(
    document: DocumentIR,
    by_id: dict[str, DocumentBlockIR],
    *,
    top_k: int = 8,
    max_df_ratio: float = 0.35,
) -> dict[str, list[str]]:
    blocks = ordered_blocks(document)
    raw_by_id = {
        block.id: raw_block_keywords(block, by_id, document, top_k=max(top_k * 3, 16))
        for block in blocks
    }
    doc_count = max(len(blocks), 1)
    doc_freq: Counter[str] = Counter()
    for terms in raw_by_id.values():
        doc_freq.update({term.casefold() for term in terms})

    output: dict[str, list[str]] = {}
    for block in blocks:
        raw_terms = raw_by_id.get(block.id, [])
        term_freq = Counter(term.casefold() for term in raw_terms)
        display = {term.casefold(): term for term in raw_terms}
        scored = []
        for folded, tf in term_freq.items():
            df = doc_freq.get(folded, 0)
            df_ratio = df / doc_count
            if doc_count >= 3 and df_ratio > max_df_ratio:
                continue
            idf = math.log((1 + doc_count) / (1 + df)) + 1
            phrase_len = min(len(folded.split()), 3)
            score = tf * idf * (1 + 0.15 * (phrase_len - 1))
            scored.append((score, idf, phrase_len, display[folded]))
        scored.sort(key=lambda item: (item[0], item[1], item[2], len(item[3])), reverse=True)
        selected = [term for _, _, _, term in scored[:top_k]]
        if not selected and raw_terms:
            rarest = sorted(
                raw_terms,
                key=lambda term: (doc_freq.get(term.casefold(), doc_count), -len(term)),
            )[0]
            selected = [rarest]
        output[block.id] = selected
    return output


def metadata_row(
    block: DocumentBlockIR,
    by_id: dict[str, DocumentBlockIR],
    document: DocumentIR,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    title = safe_display_path_segment(block.title or inherited_heading_title(block, by_id) or block.id)
    return {
        "id": block.id,
        "type": block.type.value,
        "title": title,
        "original_title": safe_display_path_segment(block.title) if block.title else None,
        "parent_id": block.parent_id,
        "depth": block.depth,
        "children_count": len(block.child_ids),
        "block_path": block_path(block, by_id),
        "page_refs": block.page_refs,
        "text_preview": block.text_preview,
        "keywords": keywords if keywords is not None else raw_block_keywords(block, by_id, document, top_k=8),
        "new_words": [item.get("term") for item in block.metadata.get("new_word_terms", [])[:8]],
    }


def ordered_blocks(document: DocumentIR) -> list[DocumentBlockIR]:
    return sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0))


def document_text_for_tokens(document: DocumentIR) -> str:
    parts = []
    for block in ordered_blocks(document):
        if not content_block(block):
            continue
        text = document_block_text(document, block).strip()
        if text:
            parts.append(text)
    if not parts:
        parts = [block.text_preview.strip() for block in ordered_blocks(document) if block.text_preview.strip()]
    return "\n\n".join(parts)


class PdfBlockChatExample:
    def __init__(self, document: DocumentIR):
        self.document = document
        self.by_id = {block.id: block for block in document.document_blocks}
        self.counter = TokenCounter.create()
        self._keyword_index: dict[str, list[str]] | None = None
        self.last_response_id: str | None = None
        self.cumulative_response_usage = {
            "source": "response_api",
            "estimated": False,
            "input_tokens": 0,
            "cached_tokens": 0,
            "cache_eligible": False,
            "cache_min_input_tokens": 1024,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

    @classmethod
    def load(cls, source: str | Path, *, lang: str = "auto", asset_dir: str | Path | None = None) -> "PdfBlockChatExample":
        languages = [part.strip() for part in lang.split(",") if part.strip()] or ["auto"]
        document = PyMuPDFAdapter().parse(source, ParseOptions(languages=languages, asset_dir=Path(asset_dir) if asset_dir else None))
        pipeline_run = run_default_pipeline(document, PipelineContext(settings={}), include_chunking=False)
        return cls(pipeline_run.document)

    def list_blocks(self, *, depth: int | None = 2) -> dict[str, Any]:
        rows = []
        keywords_by_id = self.keyword_index()
        for block in ordered_blocks(self.document):
            if depth is not None and block.depth > depth:
                continue
            rows.append(metadata_row(block, self.by_id, self.document, keywords=keywords_by_id.get(block.id, [])))
        return {"document_id": self.document.id, "block_count": len(rows), "blocks": rows}

    def keyword_index(self) -> dict[str, list[str]]:
        if self._keyword_index is None:
            self._keyword_index = tfidf_keyword_index(self.document, self.by_id)
        return self._keyword_index

    def document_token_usage(self) -> dict[str, Any]:
        return {
            "total_tokens": self.counter.count(document_text_for_tokens(self.document)),
            "counter": self.counter.backend,
        }

    def keyword_groups(self, *, limit: int = 24) -> list[dict[str, Any]]:
        scores: Counter[str] = Counter()
        block_ids: dict[str, list[str]] = {}
        pages: dict[str, set[int]] = {}
        source_kinds: dict[str, set[str]] = {}
        display: dict[str, str] = {}

        def add(term: Any, block: DocumentBlockIR, source: str, weight: float) -> None:
            text = str(term or "").strip()
            folded = text.casefold()
            if len(folded) < 2:
                return
            if folded in {"abstract", "contents", "introduction", "overview", "summary"}:
                return
            display.setdefault(folded, text)
            scores[folded] += weight * min(len(text), 12)
            block_ids.setdefault(folded, [])
            if block.id not in block_ids[folded]:
                block_ids[folded].append(block.id)
            pages.setdefault(folded, set()).update(block.page_refs)
            source_kinds.setdefault(folded, set()).add(source)

        keywords_by_id = self.keyword_index()
        for block in ordered_blocks(self.document):
            for term in keywords_by_id.get(block.id, []):
                add(term, block, "block_tfidf_ngram", 1.8)

        ranked = sorted(scores.items(), key=lambda item: (item[1], len(item[0])), reverse=True)
        return [
            {
                "term": display.get(term, term),
                "score": round(score, 4),
                "block_count": len(block_ids.get(term, [])),
                "page_refs": sorted(pages.get(term, set()))[:8],
                "source_kinds": sorted(source_kinds.get(term, set())),
                "sample_block_ids": block_ids.get(term, [])[:4],
            }
            for term, score in ranked[:limit]
        ]

    def search_blocks(
        self,
        query: str,
        *,
        limit: int = 5,
        terms: list[str] | None = None,
        terms_source: str | None = None,
        search_body: bool = True,
        include_snippets: bool = True,
        max_snippets_per_block: int = 5,
        snippet_fields: list[str] | None = None,
        verbosity: str = "compact",
    ) -> dict[str, Any]:
        query_plan = build_search_plan(query)
        has_terms_override = terms is not None
        terms = [term for term in (terms if has_terms_override else query_plan["terms"]) if str(term).strip()][:12]
        if terms:
            query_plan = {
                **query_plan,
                "terms": terms,
                "terms_source": terms_source or ("provided" if has_terms_override else query_plan["terms_source"]),
            }
        if normalized_search_verbosity(verbosity) != "debug":
            query_plan = {key: value for key, value in query_plan.items() if key != "note"}
        structural_hints = query_plan["structural_hints"]
        output_verbosity = normalized_search_verbosity(verbosity)
        snippet_field_set = set(snippet_fields) if snippet_fields is not None else set(_DEFAULT_SNIPPET_FIELDS)
        scored = []
        for block in self.document.document_blocks:
            block_keywords = self.keyword_index().get(block.id, [])
            fields = {
                "title": inherited_heading_title(block, self.by_id) or block.title or "",
                "preview": block.text_preview or "",
                "keywords": " ".join(str(item) for item in block_keywords),
                "new_words": " ".join(str(item.get("term")) for item in block.metadata.get("new_word_terms", [])),
                "search_terms": " ".join(str(item) for item in block.metadata.get("search_terms", [])),
            }
            if search_body:
                fields["body"] = document_block_text(self.document, block)
            matches: dict[str, list[str]] = {}
            matched: list[str] = []
            snippets: list[dict[str, str]] = []
            score = 0.0
            for field_name, text in fields.items():
                folded = text.casefold()
                for term in terms:
                    count = folded.count(term.casefold())
                    if count:
                        matches.setdefault(field_name, []).append(term)
                        if term not in matched:
                            matched.append(term)
                        score += _SEARCH_FIELD_WEIGHTS.get(field_name, 1.0) * count
                        if include_snippets and field_name in snippet_field_set and len(snippets) < max_snippets_per_block:
                            start = folded.find(term.casefold())
                            while start >= 0 and len(snippets) < max_snippets_per_block:
                                end = start + len(term)
                                snippets.append(
                                    {
                                        "field": field_name,
                                        "keyword": term,
                                        "snippet": search_snippet(text, start, end, term),
                                    }
                                )
                                start = folded.find(term.casefold(), start + max(1, len(term)))

            if structural_hints and block.type == DocumentBlockType.SECTION:
                folded_title = fields["title"].casefold()
                for hint in structural_hints:
                    if hint in folded_title:
                        matches.setdefault("structural_hints", []).append(hint)
                        score += 5.0

            if score > 0 and block.type in {DocumentBlockType.PARAGRAPH, DocumentBlockType.TABLE, DocumentBlockType.FOOTNOTE}:
                score += 0.25
            if score <= 0:
                continue
            base_row = metadata_row(block, self.by_id, self.document, keywords=block_keywords)
            row = {
                "id": base_row["id"],
                "type": base_row["type"],
                "title": base_row["title"],
                "score": round(score, 4),
                "page_refs": base_row["page_refs"],
                "children_count": base_row["children_count"],
                "matched": matched,
                "snippets": snippets,
            }
            if output_verbosity in {"standard", "debug"}:
                row.update(
                    {
                        "parent_id": base_row["parent_id"],
                        "depth": base_row["depth"],
                        "block_path": base_row["block_path"],
                        "text_preview": base_row["text_preview"],
                        "keywords": block_keywords[:5],
                    }
                )
            if output_verbosity == "debug":
                row.update(
                    {
                        "original_title": base_row["original_title"],
                        "new_words": base_row["new_words"],
                        "matches": matches,
                        "searched_fields": list(fields),
                    }
                )
            scored.append(row)
        scored.sort(key=lambda item: item["score"], reverse=True)
        return {
            "query": query,
            "query_plan": query_plan,
            "terms": terms,
            "searched_fields": ["title", "preview", "keywords", "new_words", "search_terms"] + (["body"] if search_body else []),
            "snippet_policy": {
                "include_snippets": include_snippets,
                "max_snippets_per_block": max_snippets_per_block,
                "search_body": search_body,
                "snippet_fields": sorted(snippet_field_set),
            },
            "verbosity": output_verbosity,
            "results": scored[:limit],
        }

    def expand_context_blocks(self, rows: list[dict[str, Any]], *, per_hit: int = 3, max_blocks: int = 12) -> list[dict[str, Any]]:
        ordered = ordered_blocks(self.document)
        by_position = {block.id: index for index, block in enumerate(ordered)}
        expanded: list[DocumentBlockIR] = []
        seen: set[str] = set()

        def append(block: DocumentBlockIR) -> None:
            if block.id in seen or len(expanded) >= max_blocks:
                return
            seen.add(block.id)
            expanded.append(block)

        def append_descendants(block: DocumentBlockIR) -> None:
            pending = list(block.child_ids)
            while pending and len(expanded) < max_blocks:
                child = self.by_id.get(pending.pop(0))
                if child is None:
                    continue
                if content_block(child):
                    append(child)
                pending.extend(child.child_ids)

        def append_following_content(block: DocumentBlockIR) -> None:
            position = by_position.get(block.id)
            if position is None:
                return
            added = 0
            for candidate in ordered[position + 1 :]:
                if content_block(candidate):
                    append(candidate)
                    added += 1
                if added >= per_hit or len(expanded) >= max_blocks:
                    break

        for row in rows:
            block = self.by_id.get(row["id"])
            if block is None:
                continue
            if content_block(block):
                append(block)
            append_descendants(block)
            content = document_block_text(self.document, block)
            if not meaningful_quote(content):
                append_following_content(block)
        keywords_by_id = self.keyword_index()
        return [metadata_row(block, self.by_id, self.document, keywords=keywords_by_id.get(block.id, [])) for block in expanded]

    def read_block(self, block_id: str, *, max_chars: int = 2000) -> dict[str, Any]:
        block = self.by_id[block_id]
        content = document_block_text(self.document, block)
        return {
            "id": block.id,
            "type": block.type.value,
            "block_path": block_path(block, self.by_id),
            "page_refs": block.page_refs,
            "source_block_ids": block.source_block_ids,
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }

    def prompt_cache_key(self, step: str) -> str:
        digest = hashlib.sha256(str(self.document.id).encode("utf-8")).hexdigest()[:24]
        return f"documa-pdf-chat:{step}:{digest}"

    def search_terms_with_model(self, question: str, *, limit: int = 6) -> dict[str, Any]:
        client = OpenAIResponsesClient()
        keyword_summary = [
            {
                "term": item.get("term"),
                "block_count": item.get("block_count"),
                "page_refs": item.get("page_refs", []),
            }
            for item in self.keyword_groups(limit=18)
        ]
        payload = {
            "document_keyword_summary": keyword_summary,
            "task_input": {"question": question},
        }
        response = client.create(
            instructions=LLM_SEARCH_TERMS_PROMPT,
            input_text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            max_output_tokens=250,
            prompt_cache_key=self.prompt_cache_key("search_terms"),
        )
        parsed = parse_json_object(response["text"])
        raw_terms = parsed.get("query_terms", [])
        terms = []
        if isinstance(raw_terms, list):
            for term in raw_terms:
                text = str(term).strip()
                if text and text.casefold() not in {item.casefold() for item in terms}:
                    terms.append(text)
                if len(terms) >= limit:
                    break
        return {
            "terms": terms,
            "response_id": response["response_id"],
            "model": response["model"],
            "usage": response["usage"],
        }

    def execute_local_tool(self, name: str, args: dict[str, Any], *, max_chars_per_block: int) -> dict[str, Any]:
        if name == "search_blocks":
            raw_terms = args.get("terms")
            terms = [str(term) for term in raw_terms] if isinstance(raw_terms, list) else None
            return self.search_blocks(
                str(args.get("query") or ""),
                limit=int(args.get("limit") or 5),
                terms=terms,
                terms_source="llm_tool_call",
                search_body=bool(args.get("search_body", True)),
                max_snippets_per_block=int(args.get("max_snippets_per_block") or 3),
                verbosity=str(args.get("verbosity") or "compact"),
            )
        if name == "read_block":
            block_id = str(args.get("block_id") or "")
            if block_id not in self.by_id:
                return {"status": "error", "message": f"Unknown block_id: {block_id}"}
            return self.read_block(block_id, max_chars=int(args.get("max_chars") or max_chars_per_block))
        if name == "list_blocks":
            depth_value = args.get("depth", 2)
            depth = int(depth_value) if depth_value is not None else None
            return self.list_blocks(depth=depth)
        return {"status": "error", "message": f"Unknown local tool: {name}"}

    def answer_with_tool_calling(
        self,
        question: str,
        recorder: TraceRecorder,
        *,
        max_chars_per_block: int,
        query_terms_override: list[str] | None = None,
        max_iterations: int = 8,
    ) -> dict[str, Any]:
        client = OpenAIResponsesClient()
        input_items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": question,
                        "document_context": {
                            "page_count": self.document.page_count,
                            "block_count": len(self.document.document_blocks),
                            "keyword_groups": [
                                {key: value for key, value in item.items() if key != "sample_block_ids"}
                                for item in self.keyword_groups(limit=12)
                            ],
                            "suggested_query_terms": query_terms_override or build_search_plan(question)["terms"],
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        ]
        tools = local_tool_definitions()
        evidence: list[dict[str, Any]] = []
        final_response: dict[str, Any] | None = None

        for iteration in range(1, max_iterations + 1):
            started = time.perf_counter()
            response = client.create(
                instructions=LLM_TOOL_CALLING_PROMPT,
                input_text=input_items,
                tools=tools,
                max_output_tokens=1200,
                prompt_cache_key=self.prompt_cache_key("tool_calling"),
            )
            recorder.add(
                "model",
                "responses_api_tool_loop",
                {
                    "iteration": iteration,
                    "response_id": response["response_id"],
                    "model": response["model"],
                    "output_item_count": len(response.get("output", [])),
                },
                started_at=started,
                response_usage=response["usage"],
            )
            add_response_usage(self.cumulative_response_usage, normalize_response_usage(response["usage"]))
            final_response = response
            tool_calls = [item for item in response.get("output", []) if item.get("type") == "function_call"]
            if not tool_calls:
                text = response["text"].strip()
                answer = {
                    "mode": "llm_responses_api_tool_calling",
                    "system_prompt": SYSTEM_PROMPT_ZH_HANT,
                    "language": "zh-Hant",
                    "question": question,
                    "text": text,
                    "evidence": evidence,
                    "response_id": response["response_id"],
                    "previous_response_id": self.last_response_id,
                    "model": response["model"],
                    "tool_calling": True,
                }
                recorder.add("answer", "llm_response", answer)
                self.last_response_id = response["response_id"]
                return answer

            input_items.extend(response.get("output", []))
            for tool_call in tool_calls:
                name = str(tool_call.get("name") or "")
                try:
                    args = json.loads(str(tool_call.get("arguments") or "{}"))
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                recorder.add(
                    "tool_call",
                    name,
                    {
                        "tool": name,
                        "args": args,
                        "call_id": tool_call.get("call_id"),
                        "source": "responses_api_function_call",
                    },
                )
                started = time.perf_counter()
                result = self.execute_local_tool(name, args, max_chars_per_block=max_chars_per_block)
                if name == "read_block" and isinstance(result, dict):
                    evidence.append(
                        {
                            "block_id": result.get("id"),
                            "block_path": result.get("block_path", []),
                            "page_refs": result.get("page_refs", []),
                            "quote": str(result.get("content", ""))[:500],
                        }
                    )
                recorder.add("tool_result", name, result, started_at=started)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_call.get("call_id"),
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

        text = "回答：工具調用迴圈達到上限，尚未取得模型最終回答。請降低問題範圍或重試。"
        answer = {
            "mode": "llm_responses_api_tool_calling_incomplete",
            "system_prompt": SYSTEM_PROMPT_ZH_HANT,
            "language": "zh-Hant",
            "question": question,
            "text": text,
            "evidence": evidence,
            "response_id": final_response.get("response_id") if final_response else None,
            "previous_response_id": self.last_response_id,
            "model": final_response.get("model") if final_response else None,
            "tool_calling": True,
        }
        recorder.add("answer", "llm_response", answer)
        if final_response:
            self.last_response_id = final_response["response_id"]
        return answer

    def answer_with_model(
        self,
        question: str,
        read_results: list[dict[str, Any]],
        recorder: TraceRecorder,
        *,
        query_plan: dict[str, Any],
    ) -> dict[str, Any]:
        stable_keywords = [
            {key: value for key, value in item.items() if key != "sample_block_ids"}
            for item in self.keyword_groups(limit=24)
        ]
        evidence_blocks = [
            {
                "page_refs": block.get("page_refs", []),
                "content": block.get("content", ""),
                "truncated": block.get("truncated", False),
            }
            for block in read_results
        ]
        evidence_payload = {
            "document_context": {
                "page_count": self.document.page_count,
                "block_count": len(self.document.document_blocks),
                "top_keywords": stable_keywords,
            },
            "evidence": {
                "blocks": evidence_blocks,
            },
            "task_input": {
                "question": question,
                "query_plan": query_plan,
            },
        }
        client = OpenAIResponsesClient()
        started = time.perf_counter()
        input_text = json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True)
        request_debug = request_debug_payload(input_text, self.counter)
        response = client.create(
            instructions=LLM_SYNTHESIS_PROMPT,
            input_text=input_text,
            max_output_tokens=1200,
            prompt_cache_key=self.prompt_cache_key("answer"),
            previous_response_id=self.last_response_id,
        )
        text = response["text"].strip()
        answer = {
            "mode": "llm_responses_api_answer",
            "system_prompt": SYSTEM_PROMPT_ZH_HANT,
            "language": "zh-Hant",
            "question": question,
            "text": text,
            "evidence": [
                {
                    "block_id": block["id"],
                    "block_path": block.get("block_path", []),
                    "page_refs": block.get("page_refs", []),
                    "quote": block.get("content", "")[:500],
                }
                for block in read_results
            ],
            "response_id": response["response_id"],
            "previous_response_id": self.last_response_id,
            "request_debug": request_debug,
            "model": response["model"],
        }
        recorder.add(
            "answer",
            "llm_response",
            answer,
            started_at=started,
            response_usage=response["usage"],
        )
        self.last_response_id = response["response_id"]
        add_response_usage(self.cumulative_response_usage, normalize_response_usage(response["usage"]))
        return answer

    def answer(
        self,
        question: str,
        *,
        limit: int = 5,
        max_chars_per_block: int = 2000,
        query_terms_override: list[str] | None = None,
        use_llm: bool = False,
    ) -> dict[str, Any]:
        recorder = TraceRecorder(self.counter)

        if use_llm:
            try:
                answer = self.answer_with_tool_calling(
                    question,
                    recorder,
                    max_chars_per_block=max_chars_per_block,
                    query_terms_override=query_terms_override,
                )
                return {
                    "question": question,
                    "system_prompt": SYSTEM_PROMPT_ZH_HANT,
                    "llm_enabled": answer.get("mode") == "llm_responses_api_tool_calling",
                    "llm_error": None,
                    "selected_block_ids": [item.get("block_id") for item in answer.get("evidence", []) if item.get("block_id")],
                    "answer": answer,
                    "events": recorder.events,
                    "token_usage": {**aggregate_token_usage(recorder.events), "counter": self.counter.backend},
                    "cumulative_token_usage": {**self.cumulative_response_usage, "counter": self.counter.backend},
                }
            except Exception as exc:  # noqa: BLE001
                llm_error = str(exc)
                recorder.add("model_error", "responses_api_tool_loop", {"error": llm_error})
                use_llm = False
        else:
            llm_error = None

        started = time.perf_counter()
        query_plan = build_search_plan(question)
        terms = [str(term).strip() for term in query_terms_override or query_plan["terms"] if str(term).strip()][:12]
        search_terms_usage: dict[str, Any] | None = None
        search_terms_error = None
        search_terms_model = None
        if use_llm and not query_terms_override:
            try:
                generated_terms = self.search_terms_with_model(question)
                if generated_terms.get("terms"):
                    terms = [str(term).strip() for term in generated_terms["terms"] if str(term).strip()][:6]
                    query_plan = {
                        **query_plan,
                        "terms": terms,
                        "terms_source": "llm_bilingual_search_terms",
                    }
                search_terms_usage = generated_terms.get("usage")
                search_terms_model = generated_terms.get("model")
            except Exception as exc:  # noqa: BLE001
                search_terms_error = str(exc)
        if terms:
            query_plan = {**query_plan, "terms": terms, "terms_source": "user_supplied" if query_terms_override else query_plan["terms_source"]}
        search_call = {
            "tool": "search_blocks",
            "query": question,
            "limit": limit,
            "query_plan": query_plan,
            "ranking_inputs": ["title", "keywords", "new_words", "search_terms", "preview", "body_snippets"],
            "snippet_policy": {
                "search_body": True,
                "include_snippets": True,
                "max_snippets_per_block": 3,
                "snippet_fields": sorted(_DEFAULT_SNIPPET_FIELDS),
                "verbosity": "compact",
            },
            "term_generation": {
                "mode": "responses_api" if search_terms_usage else "deterministic",
                "model": search_terms_model,
                "error": search_terms_error,
            },
        }
        recorder.add("tool_call", "search_blocks", search_call, response_usage=search_terms_usage)
        search_result = self.search_blocks(
            question,
            limit=limit,
            terms=terms or None,
            terms_source=query_plan["terms_source"],
            verbosity="compact",
            max_snippets_per_block=3,
        )
        recorder.add("tool_result", "search_blocks", search_result, started_at=started)

        selected = search_result["results"]
        if not selected:
            selected = [
                metadata_row(block, self.by_id, self.document, keywords=self.keyword_index().get(block.id, []))
                for block in self.document.document_blocks
                if not block.child_ids and block.text_preview
            ][:limit]
        selected = self.expand_context_blocks(selected, max_blocks=max(limit * 3, limit))

        read_results = []
        for item in selected:
            started = time.perf_counter()
            read_call = {"tool": "read_block", "block_id": item["id"], "max_chars": max_chars_per_block}
            recorder.add("tool_call", "read_block", read_call)
            read_result = self.read_block(item["id"], max_chars=max_chars_per_block)
            read_results.append(read_result)
            recorder.add("tool_result", "read_block", read_result, started_at=started)

        if use_llm and not llm_error:
            try:
                answer = self.answer_with_model(question, read_results, recorder, query_plan=query_plan)
            except Exception as exc:  # noqa: BLE001
                llm_error = str(exc)
                answer = fallback_answer(question, read_results, system_prompt=SYSTEM_PROMPT_ZH_HANT)
                answer["llm_error"] = llm_error
                recorder.add("answer", "fallback_answer", answer)
        else:
            answer = fallback_answer(question, read_results, system_prompt=SYSTEM_PROMPT_ZH_HANT)
            if llm_error:
                answer["llm_error"] = llm_error
            recorder.add("answer", "fallback_answer", answer)
        return {
            "question": question,
            "system_prompt": SYSTEM_PROMPT_ZH_HANT,
            "llm_enabled": bool(use_llm and not llm_error and answer.get("mode") == "llm_responses_api_answer"),
            "llm_error": llm_error,
            "selected_block_ids": [item["id"] for item in selected],
            "answer": answer,
            "events": recorder.events,
            "token_usage": {**aggregate_token_usage(recorder.events), "counter": self.counter.backend},
            "cumulative_token_usage": {**self.cumulative_response_usage, "counter": self.counter.backend},
        }

    def export_artifacts(self, out_dir: str | Path, trace: dict[str, Any]) -> dict[str, str]:
        output_dir = Path(out_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "ir": output_dir / "documa.ir.json",
            "blocks": output_dir / "documa.blocks.json",
            "trace": output_dir / "pdf_chat_trace.json",
        }
        write_json(paths["ir"], JsonExporter().export(self.document, ExportOptions()))
        write_json(paths["blocks"], BlockJsonExporter().export(self.document, ExportOptions()))
        write_json(paths["trace"], trace)
        return {key: str(path) for key, path in paths.items()}


def fallback_answer(
    question: str,
    blocks: list[dict[str, Any]],
    max_sentences: int = 6,
    *,
    system_prompt: str = SYSTEM_PROMPT_ZH_HANT,
) -> dict[str, Any]:
    terms = build_search_plan(question)["terms"]
    evidence = []
    for block in blocks:
        sentences = [part.strip() for part in _SENTENCE_SPLIT.split(block["content"]) if part.strip()]
        scored = []
        for sentence in sentences:
            folded = sentence.casefold()
            score = sum(folded.count(term.casefold()) for term in terms)
            if score:
                scored.append((score, sentence))
        if not scored:
            scored = [(0, sentence) for sentence in sentences if meaningful_quote(sentence)]
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, sentence in scored[:2]:
            if not meaningful_quote(sentence):
                continue
            evidence.append(
                {
                    "block_id": block["id"],
                    "block_path": block["block_path"],
                    "page_refs": block["page_refs"],
                    "quote": sentence,
                }
            )
    evidence = evidence[:max_sentences]
    if evidence:
        answer_points = []
        seen = set()
        for item in evidence:
            quote = item["quote"]
            folded = quote.casefold()
            if folded not in seen:
                answer_points.append(quote)
                seen.add(folded)
            if len(answer_points) >= 3:
                break
        text = (
            "回答：目前未接外部 LLM，因此此 deterministic example 不會翻譯或改寫 PDF 原文。"
            "以下列出最相關的原文證據，請以這些片段作為回答依據。\n\n"
            "原文證據：\n"
            + "\n".join(evidence_line(item) for item in evidence)
        )
    else:
        text = "回答：目前讀到的候選 PDF blocks 多為標題、頁碼或章節編號，無法形成可靠回答。\n\n依據：未找到足夠的正文片段。"
    return {
        "mode": "deterministic_extractive_synthesis",
        "system_prompt": system_prompt,
        "language": "zh-Hant-with-source-quotes",
        "question": question,
        "text": text,
        "evidence": evidence,
        "limitations": [
            "此 example 不呼叫外部 LLM；回答由已讀取 block 內容抽取生成，引用證據可能保留 PDF 原文語言。",
            "若要接成真正 chat UI，可直接渲染 events 裡的 tool_call/tool_result/answer。",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF chat-like progressive reading example for Documa.")
    parser.add_argument("pdf", help="Path to the source PDF.")
    parser.add_argument("--question", action="append", help="Question to ask. Can be repeated.")
    parser.add_argument("--interactive", action="store_true", help="Ask questions interactively after loading the PDF.")
    parser.add_argument("--out", help="Output directory for IR, blocks, and trace JSON.")
    parser.add_argument("--lang", default="auto", help="Comma-separated language hints.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum searched blocks per question.")
    parser.add_argument("--max-chars-per-block", type=int, default=2000, help="Maximum content loaded per block.")
    parser.add_argument("--llm", action="store_true", help="Use OpenAI Responses API for the final answer after local search.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.question and not args.interactive:
        parser.error("provide --question or --interactive")

    asset_dir = Path(args.out) / "assets" if args.out else None
    session = PdfBlockChatExample.load(args.pdf, lang=args.lang, asset_dir=asset_dir)
    turns = []
    for question in args.question or []:
        turns.append(session.answer(question, limit=args.limit, max_chars_per_block=args.max_chars_per_block, use_llm=args.llm))

    if args.interactive:
        print("PDF loaded. Type a question, or 'exit' to quit.", file=sys.stderr)
        while True:
            try:
                question = input("> ").strip()
            except EOFError:
                break
            if question.lower() in {"exit", "quit"}:
                break
            if question:
                turn = session.answer(question, limit=args.limit, max_chars_per_block=args.max_chars_per_block, use_llm=args.llm)
                turns.append(turn)
                print(json.dumps(turn["answer"], ensure_ascii=False, indent=2))

    trace = {
        "status": "ok",
        "example": "pdf_chat_like",
        "source": safe_display_path_segment(args.pdf),
        "document_id": session.document.id,
        "page_count": session.document.page_count,
        "document_block_count": len(session.document.document_blocks),
        "turns": turns,
    }
    if args.out:
        trace["output_paths"] = session.export_artifacts(args.out, trace)
    sys.stdout.write(json.dumps(trace, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
