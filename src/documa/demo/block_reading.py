"""Block-based reading demo with trace and token accounting."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.errors import DocumaError
from documa.core.ir import DocumentBlockIR, DocumentBlockType, DocumentIR, to_plain_data
from documa.exporters import BlockJsonExporter, ExportOptions, JsonExporter
from documa.interfaces.tools import write_payload
from documa.pipeline import PipelineContext, run_default_pipeline
from documa.pipeline.block_tree import document_block_text


_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_+\-]{2,}")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*|\n+")
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
class DemoTracer:
    counter: TokenCounter
    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        *,
        name: str,
        call: dict[str, Any],
        returned: Any,
        started_at: float,
        note: str | None = None,
    ) -> Any:
        ended_at = time.perf_counter()
        input_tokens = self.counter.count(call)
        output_tokens = self.counter.count(returned)
        step = {
            "step_index": len(self.steps) + 1,
            "name": name,
            "note": note,
            "elapsed_ms": round((ended_at - started_at) * 1000, 3),
            "call": call,
            "returned": returned,
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }
        self.steps.append(step)
        return returned


def _query_terms(query: str) -> list[str]:
    terms = [match.group(0).casefold() for match in _WORD.finditer(query)]
    for match in _CJK.finditer(query):
        chars = match.group(0)
        if len(chars) <= 8:
            terms.append(chars)
        for size in range(2, min(8, len(chars)) + 1):
            for start in range(0, len(chars) - size + 1):
                terms.append(chars[start : start + size])
    output = []
    for term in terms:
        if term and term not in output:
            output.append(term)
    return output or [query.casefold()]


def _meaningful_quote(text: str) -> bool:
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


def _evidence_line(item: dict[str, Any]) -> str:
    pages = ",".join(str(page) for page in item["page_refs"]) or "?"
    return f"- p.{pages} / {item['block_id']}: {item['quote']}"


def _content_block(block: DocumentBlockIR) -> bool:
    return block.type in {DocumentBlockType.PARAGRAPH, DocumentBlockType.TABLE, DocumentBlockType.FOOTNOTE}


def _block_path(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> list[str]:
    path = []
    current: DocumentBlockIR | None = block
    while current is not None:
        if current.title:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _metadata_summary(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> dict[str, Any]:
    return {
        "id": block.id,
        "type": block.type.value,
        "title": block.title,
        "parent_id": block.parent_id,
        "depth": block.depth,
        "children_count": len(block.child_ids),
        "block_path": _block_path(block, by_id),
        "page_refs": block.page_refs,
        "text_preview": block.text_preview,
        "keywords": block.metadata.get("keyword_terms", [])[:8],
        "new_words": [item.get("term") for item in block.metadata.get("new_word_terms", [])[:8]],
        "thresholds": block.metadata.get("keyword_thresholds", {}),
        "confidence": block.confidence.value,
    }


def _score_block(block: DocumentBlockIR, terms: list[str]) -> tuple[float, dict[str, Any]]:
    fields = {
        "title": block.title or "",
        "preview": block.text_preview or "",
        "keywords": " ".join(str(item) for item in block.metadata.get("keyword_terms", [])),
        "new_words": " ".join(str(item.get("term")) for item in block.metadata.get("new_word_terms", [])),
        "search_terms": " ".join(str(item) for item in block.metadata.get("search_terms", [])),
    }
    weights = {"title": 4.0, "keywords": 3.0, "new_words": 3.0, "search_terms": 2.0, "preview": 1.0}
    matches: dict[str, list[str]] = {}
    score = 0.0
    for field, text in fields.items():
        folded = text.casefold()
        for term in terms:
            count = folded.count(term.casefold())
            if count:
                matches.setdefault(field, []).append(term)
                score += weights[field] * count
    if block.type in {DocumentBlockType.PARAGRAPH, DocumentBlockType.TABLE, DocumentBlockType.FOOTNOTE}:
        score += 0.25
    return score, {"matches": matches, "field_weights": weights}


def _list_blocks(document: DocumentIR) -> list[dict[str, Any]]:
    by_id = {block.id: block for block in document.document_blocks}
    return [
        _metadata_summary(block, by_id)
        for block in sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
    ]


def _select_blocks(document: DocumentIR, question: str, top_k: int) -> list[dict[str, Any]]:
    by_id = {block.id: block for block in document.document_blocks}
    terms = _query_terms(question)
    scored = []
    for block in document.document_blocks:
        score, detail = _score_block(block, terms)
        if score <= 0:
            continue
        item = _metadata_summary(block, by_id)
        item["score"] = round(score, 4)
        item["score_detail"] = detail
        scored.append(item)
    scored.sort(key=lambda item: item["score"], reverse=True)
    if scored:
        return scored[:top_k]
    fallback = [
        _metadata_summary(block, by_id)
        for block in document.document_blocks
        if not block.child_ids and block.text_preview
    ]
    return fallback[:top_k]


def _expand_selected_blocks(document: DocumentIR, selected: list[dict[str, Any]], *, per_hit: int = 3, max_blocks: int = 12) -> list[dict[str, Any]]:
    by_id = {block.id: block for block in document.document_blocks}
    ordered = sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
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
            child = by_id.get(pending.pop(0))
            if child is None:
                continue
            if _content_block(child):
                append(child)
            pending.extend(child.child_ids)

    def append_following_content(block: DocumentBlockIR) -> None:
        position = by_position.get(block.id)
        if position is None:
            return
        added = 0
        for candidate in ordered[position + 1 :]:
            if _content_block(candidate):
                append(candidate)
                added += 1
            if added >= per_hit or len(expanded) >= max_blocks:
                break

    for row in selected:
        block = by_id.get(row["id"])
        if block is None:
            continue
        if _content_block(block):
            append(block)
        append_descendants(block)
        content = document_block_text(document, block)
        if not _meaningful_quote(content):
            append_following_content(block)
    return [_metadata_summary(block, by_id) for block in expanded]


def _read_selected_blocks(document: DocumentIR, selected: list[dict[str, Any]], max_chars_per_block: int) -> list[dict[str, Any]]:
    by_id = {block.id: block for block in document.document_blocks}
    bodies = []
    for item in selected:
        block = by_id[item["id"]]
        text = document_block_text(document, block)
        truncated = len(text) > max_chars_per_block
        bodies.append(
            {
                "id": block.id,
                "type": block.type.value,
                "block_path": _block_path(block, by_id),
                "page_refs": block.page_refs,
                "source_block_ids": block.source_block_ids,
                "content": text[:max_chars_per_block],
                "truncated": truncated,
            }
        )
    return bodies


def _synthesize_answer(question: str, block_bodies: list[dict[str, Any]], max_sentences: int = 5) -> dict[str, Any]:
    terms = _query_terms(question)
    evidence = []
    for body in block_bodies:
        sentences = [part.strip() for part in _SENTENCE_SPLIT.split(body["content"]) if part.strip()]
        scored = []
        for sentence in sentences:
            folded = sentence.casefold()
            score = sum(folded.count(term.casefold()) for term in terms)
            if score:
                scored.append((score, sentence))
        if not scored:
            scored = [(0, sentence) for sentence in sentences if _meaningful_quote(sentence)]
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, sentence in scored[:2]:
            if not _meaningful_quote(sentence):
                continue
            evidence.append(
                {
                    "block_id": body["id"],
                    "block_path": body["block_path"],
                    "page_refs": body["page_refs"],
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
        answer = "回答：" + " ".join(answer_points) + "\n\n依據：\n" + "\n".join(_evidence_line(item) for item in evidence)
    else:
        answer = "回答：目前讀到的候選 blocks 多為標題、頁碼或章節編號，無法形成可靠回答。\n\n依據：未找到足夠的正文片段。"
    return {
        "mode": "deterministic_extractive_synthesis",
        "question": question,
        "answer": answer,
        "evidence": evidence,
        "limitations": [
            "此 demo 不呼叫外部 LLM；答案由已載入 block 內容抽取生成。",
            "token_usage 為 demo 估算或 tiktoken 本地計算，不代表外部 API billing。",
        ],
    }


def _pipeline_summary(document: DocumentIR, pipeline_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": document.id,
        "source_name": document.source_name,
        "page_count": document.page_count,
        "page_blocks": sum(len(page.blocks) for page in document.pages),
        "document_blocks": len(document.document_blocks),
        "relations": len(document.relations),
        "pipeline": pipeline_report,
    }


def run_block_reading_demo(
    source: str,
    question: str,
    *,
    out: str | None = None,
    lang: str = "auto",
    top_k: int = 3,
    max_chars_per_block: int = 2000,
) -> dict[str, Any]:
    """Run an end-to-end block-based reading demo and return a trace payload."""

    output_dir = Path(out) if out else None
    asset_dir = output_dir / "assets" if output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
    counter = TokenCounter.create()
    tracer = DemoTracer(counter)
    started_total = time.perf_counter()
    languages = [part.strip() for part in lang.split(",") if part.strip()] or ["auto"]

    try:
        started = time.perf_counter()
        document = PyMuPDFAdapter().parse(source, ParseOptions(languages=languages, asset_dir=asset_dir))
        tracer.record(
            name="load_pdf",
            call={"tool": "PyMuPDFAdapter.parse", "source": source, "lang": languages},
            returned={"document_id": document.id, "page_count": document.page_count, "parser": document.parser},
            started_at=started,
            note="載入 PDF 並轉成 parser-neutral Documa IR。",
        )
    except DocumaError as exc:
        payload = exc.to_dict()
        payload["status"] = "error"
        return payload

    started = time.perf_counter()
    pipeline_run = run_default_pipeline(document, PipelineContext(settings={}), include_chunking=False)
    document = pipeline_run.document
    tracer.record(
        name="build_blocks_and_metadata",
        call={"tool": "run_default_pipeline", "include_chunking": False},
        returned=_pipeline_summary(document, pipeline_run.report()),
        started_at=started,
        note="建立 reading order、layout、paragraph、relations、block tree 與 bottom-up keywords。",
    )

    started = time.perf_counter()
    block_list = _list_blocks(document)
    tracer.record(
        name="list_all_blocks",
        call={"tool": "documa_list_blocks", "include_metadata_summary": True},
        returned={"block_count": len(block_list), "blocks": block_list},
        started_at=started,
        note="先讀全體 block list 與 metadata summary，不載入完整內文。",
    )

    started = time.perf_counter()
    selected = _select_blocks(document, question, top_k)
    tracer.record(
        name="rank_blocks_by_metadata",
        call={"query": question, "top_k": top_k, "ranking_inputs": ["title", "preview", "keywords", "new_words", "search_terms"]},
        returned={"selected_blocks": selected},
        started_at=started,
        note="只用 block metadata 和 preview 定向候選分塊。",
    )
    context_selected = _expand_selected_blocks(document, selected, max_blocks=max(top_k * 3, top_k))

    started = time.perf_counter()
    block_bodies = _read_selected_blocks(document, context_selected, max_chars_per_block)
    tracer.record(
        name="read_selected_block_bodies",
        call={"tool": "documa_read_block", "block_ids": [item["id"] for item in context_selected], "max_chars_per_block": max_chars_per_block},
        returned={"blocks": block_bodies},
        started_at=started,
        note="只載入已選定 blocks 的全文；若命中標題，補讀附近正文 block。",
    )

    started = time.perf_counter()
    answer = _synthesize_answer(question, block_bodies)
    tracer.record(
        name="synthesize_answer",
        call={"mode": "deterministic_extractive_synthesis", "question": question, "context_block_ids": [item["id"] for item in block_bodies]},
        returned=answer,
        started_at=started,
        note="將已載入 block 內容轉成可讀答案，並保留 evidence。",
    )

    total_usage = {
        "input_tokens": sum(step["token_usage"]["input_tokens"] for step in tracer.steps),
        "output_tokens": sum(step["token_usage"]["output_tokens"] for step in tracer.steps),
    }
    total_usage["total_tokens"] = total_usage["input_tokens"] + total_usage["output_tokens"]

    paths: dict[str, str] = {}
    if output_dir:
        ir_path = output_dir / "documa.ir.json"
        blocks_path = output_dir / "documa.blocks.json"
        trace_path = output_dir / "documa.block_demo.trace.json"
        write_payload(ir_path, JsonExporter().export(document, ExportOptions()))
        write_payload(blocks_path, BlockJsonExporter().export(document, ExportOptions()))
        paths = {"ir": str(ir_path), "blocks": str(blocks_path), "trace": str(trace_path)}
    trace = {
        "status": "ok",
        "demo": "block_based_reading",
        "source": source,
        "question": question,
        "token_counter": counter.backend,
        "elapsed_ms": round((time.perf_counter() - started_total) * 1000, 3),
        "summary": {
            "document_id": document.id,
            "page_count": document.page_count,
            "document_block_count": len(document.document_blocks),
            "selected_block_ids": [item["id"] for item in context_selected],
            "token_usage": total_usage,
        },
        "answer": answer,
        "steps": tracer.steps,
        "output_paths": paths,
    }
    if output_dir:
        write_payload(output_dir / "documa.block_demo.trace.json", trace)
    return trace
