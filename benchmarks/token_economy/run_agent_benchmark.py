"""Deterministic end-to-end retrieval/token benchmark for Documa."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
from pathlib import Path

from documa.core.ir import BlockIR, BlockType, DocumentBlockIR, DocumentBlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool, token_counting
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.pipeline import BlockKeywordExtractionStage
from documa.search.sidecar import build_search_sidecar


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUDGETS = (300, 600, 1200)


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _document(gold):
    source_blocks = []
    document_blocks = [
        DocumentBlockIR(
            id="root",
            type=DocumentBlockType.DOCUMENT,
            title=gold["source_name"],
            child_ids=[f"section-{index}" for index in range(len(gold["blocks"]))],
            order_index=0,
        )
    ]
    for index, item in enumerate(gold["blocks"]):
        source_id = f"source-{index}"
        section_id = f"section-{index}"
        source_blocks.append(
            BlockIR(
                id=source_id,
                type=BlockType.PARAGRAPH,
                page_number=index + 1,
                text=TextContent(item["text"]),
                order_index=index,
            )
        )
        document_blocks.extend(
            [
                DocumentBlockIR(
                    id=section_id,
                    type=DocumentBlockType.SECTION,
                    title=item["section"],
                    parent_id="root",
                    child_ids=[item["id"]],
                    page_refs=[index + 1],
                    order_index=index * 2 + 1,
                ),
                DocumentBlockIR(
                    id=item["id"],
                    type=DocumentBlockType.PARAGRAPH,
                    parent_id=section_id,
                    source_block_ids=[source_id],
                    page_refs=[index + 1],
                    text_preview=item["text"][:160],
                    content_hash=hashlib.sha256(item["text"].encode("utf-8")).hexdigest(),
                    order_index=index * 2 + 2,
                ),
            ]
        )
    return DocumentIR(
        id=gold["document_id"],
        source_name=gold["source_name"],
        pages=[
            PageIR(id=f"page-{index + 1}", page_number=index + 1, width=400, height=600, blocks=[block])
            for index, block in enumerate(source_blocks)
        ],
        document_blocks=document_blocks,
    )


def _rank(ids, gold_ids):
    for index, block_id in enumerate(ids, start=1):
        if block_id in gold_ids:
            return index
    return None


def run_benchmark():
    counter = token_counting.TiktokenCounter()
    token_counting.set_token_counter(counter)
    queries = _jsonl(HERE / "queries.jsonl")
    paraphrases = {item["query_id"]: item for item in _jsonl(HERE / "paraphrases.jsonl")}
    gold = json.loads((HERE / "gold_evidence.json").read_text(encoding="utf-8"))
    document = _document(gold)
    BlockKeywordExtractionStage().run(document)
    skill_text = (ROOT / "plugins" / "codex-documa" / "skills" / "documa-evidence" / "SKILL.md").read_text(encoding="utf-8")
    skill_tokens = counter.count(skill_text)
    schema_tokens = counter.count(json.dumps(documa_tool_schemas(profile="agent"), ensure_ascii=False, separators=(",", ":")))
    traces = []
    paraphrase_agreements = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(document), ensure_ascii=False), encoding="utf-8")
            build_search_sidecar(document, Path(tmp) / "documa.search.idx")
            for query in queries:
                paraphrase = paraphrases[query["id"]]
                canonical_ids = []
                for budget in BUDGETS:
                    search = call_documa_tool(
                        "documa_search_blocks",
                        {
                            "ir_path": str(ir_path),
                            "query": query["query"],
                            "any_of": query["any_of"],
                            "granularity": "leaf",
                            "response_profile": "evidence",
                            "limit": 5,
                            "max_evidence_tokens": budget,
                        },
                    )["structuredContent"]
                    ids = [row["block_id"] for row in search.get("results", [])]
                    if budget == BUDGETS[-1]:
                        canonical_ids = ids
                    read = call_documa_tool(
                        "documa_read_blocks",
                        {
                            "ir_path": str(ir_path),
                            "block_ids": ids or query["gold_block_ids"],
                            "total_max_tokens": budget,
                            "per_block_max_tokens": min(300, budget),
                        },
                    )["structuredContent"]
                    tool_tokens = counter.count(json.dumps(search, ensure_ascii=False, separators=(",", ":"))) + counter.count(
                        json.dumps(read, ensure_ascii=False, separators=(",", ":"))
                    )
                    selected = set(ids)
                    gold_ids = set(query["gold_block_ids"])
                    relevant = selected & gold_ids
                    evidence_tokens = int(search.get("retrieval", {}).get("selected_evidence_tokens") or 0)
                    contents = [item.get("content", "") for item in read.get("results", [])]
                    redundancy = 1.0 - len(set(contents)) / max(1, len(contents))
                    traces.append(
                        {
                            "query_id": query["id"],
                            "intent": query["intent"],
                            "budget": budget,
                            "skill_tokens": skill_tokens,
                            "tool_schema_tokens": schema_tokens,
                            "tool_output_tokens": tool_tokens,
                            "assistant_input_tokens": skill_tokens + schema_tokens + tool_tokens,
                            "assistant_output_tokens": 0,
                            "total_tokens": skill_tokens + schema_tokens + tool_tokens,
                            "calls": 2,
                            "failed_calls": int(search.get("status") != "ok") + int(read.get("status") != "ok"),
                            "first_relevant_rank": _rank(ids, gold_ids),
                            "evidence_tokens": evidence_tokens,
                            "gold_recall": len(relevant) / len(gold_ids),
                            "citation_precision": len(relevant) / max(1, len(selected)),
                            "citation_recall": len(relevant) / len(gold_ids),
                            "supported": bool(relevant),
                            "budget_violation": evidence_tokens > budget or int(read.get("budget", {}).get("spent_tokens") or 0) > budget,
                            "result_redundancy": redundancy,
                        }
                    )
                paraphrase_result = call_documa_tool(
                    "documa_search_blocks",
                    {
                        "ir_path": str(ir_path),
                        "query": paraphrase["query"],
                        "any_of": paraphrase["any_of"],
                        "granularity": "leaf",
                        "limit": 5,
                    },
                )["structuredContent"]
                paraphrase_ids = [row["block_id"] for row in paraphrase_result.get("results", [])]
                union = set(canonical_ids) | set(paraphrase_ids)
                paraphrase_agreements.append(len(set(canonical_ids) & set(paraphrase_ids)) / max(1, len(union)))
    finally:
        token_counting.reset_token_counter()

    supported = [trace for trace in traces if trace["supported"]]
    summary = {
        "query_count": len(queries),
        "trace_count": len(traces),
        "tokens_to_supported_answer": sum(trace["total_tokens"] for trace in traces) / max(1, len(supported)),
        "evidence_recall": {
            str(budget): statistics.mean(trace["gold_recall"] for trace in traces if trace["budget"] == budget)
            for budget in BUDGETS
        },
        "minimal_evidence_regret": statistics.mean(max(0, trace["evidence_tokens"] - 80) for trace in traces),
        "search_path_length": statistics.mean(trace["calls"] for trace in traces),
        "result_redundancy": statistics.mean(trace["result_redundancy"] for trace in traces),
        "citation_precision": statistics.mean(trace["citation_precision"] for trace in traces),
        "citation_recall": statistics.mean(trace["citation_recall"] for trace in traces),
        "budget_correctness": 1.0 - statistics.mean(int(trace["budget_violation"]) for trace in traces),
        "failed_calls": sum(trace["failed_calls"] for trace in traces),
        "paraphrase_top_k_jaccard": statistics.mean(paraphrase_agreements),
        "token_counter": counter.name,
    }
    status = (
        "ok"
        if summary["budget_correctness"] == 1.0
        and summary["evidence_recall"]["1200"] >= 0.9
        and summary["citation_precision"] >= 0.5
        else "failed"
    )
    return {"status": status, "summary": summary, "traces": traces}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="token-economy.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    report = run_benchmark()
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if args.check and report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
