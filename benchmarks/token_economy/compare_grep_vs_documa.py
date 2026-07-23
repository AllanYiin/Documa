"""Measure grep-style vs Documa progressive-reading token cost on one document.

Reproduces the README comparison table: for each query, sum the tokens an
agent would receive on (a) a grep path over the exported Markdown — all
matching lines with file:line prefixes plus a 60-line read window around the
first three hits, backing off through the query terms until one matches — and
(b) the Documa path — search_blocks, then executing the response's
recommended_next actions verbatim, then one bounded read of the top hit plus
cite_block.

Usage:
    python benchmarks/token_economy/compare_grep_vs_documa.py \
        --ir out/basel3-documa/documa.ir.json \
        --markdown out/basel3-documa/documa.md
"""

from __future__ import annotations

import argparse
import json
import statistics

from documa.interfaces import call_documa_tool, token_counting

DEFAULT_QUERIES = [
    "流動性覆蓋比率",
    "淨穩定資金比率",
    "高品質流動性資產 定義",
    "存款 流失率",
    "監控工具",
    "LCR",
    "壓力情境 假設",
    "抵押品 交換",
    "資金集中度",
    "市場流動性 風險",
]

GREP_EXPAND_HITS = 3
GREP_WINDOW_BEFORE = 10
GREP_WINDOW_AFTER = 50


def grep_path_tokens(count, lines: list[str], query: str) -> tuple[int, int]:
    """(tokens, hit_count) for the simulated grep-and-expand path."""
    hits: list[tuple[int, str]] = []
    for term in query.split():
        hits = [(index + 1, line) for index, line in enumerate(lines) if term in line]
        if hits:
            break
    if not hits:
        return 0, 0
    total = count("\n".join(f"documa.md:{number}:{line}" for number, line in hits))
    for number, _ in hits[:GREP_EXPAND_HITS]:
        window = lines[max(0, number - GREP_WINDOW_BEFORE) : number + GREP_WINDOW_AFTER]
        total += count("\n".join(window))
    return total, len(hits)


def documa_path_tokens(count, ir_path: str, query: str) -> tuple[int, int]:
    """(tokens, hit_count) for search -> recommended_next -> bounded read -> cite."""
    total = 0

    def call(name: str, arguments: dict) -> dict:
        nonlocal total
        result = call_documa_tool(name, arguments)
        payload = result.get("structuredContent") or result
        total += count(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return payload

    search = call("documa_search_blocks", {"ir_path": ir_path, "query": query})
    results = search.get("results") or []
    for action in (search.get("recommended_next") or {}).get("actions", []):
        call(action["tool"], action["arguments"])
    if results:
        top = results[0]
        call(
            "documa_read_block",
            {"ir_path": ir_path, "block_id": top["block_id"], "max_chars": top.get("read_chars", 1500)},
        )
        call("documa_cite_block", {"ir_path": ir_path, "block_id": top["block_id"]})
    return total, len(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ir", required=True, help="Path to documa.ir.json.")
    parser.add_argument("--markdown", required=True, help="Exported Markdown of the same document (grep target).")
    parser.add_argument("--query", action="append", dest="queries", help="Query to measure; repeatable. Defaults to the README query set.")
    args = parser.parse_args()

    counter = token_counting.get_token_counter()
    if counter is None:
        raise SystemExit("A real token counter is required (install documa[tokens] or set DOCUMA_TOKEN_COUNTER).")
    count = counter.count

    with open(args.markdown, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    queries = args.queries or DEFAULT_QUERIES

    grep_totals, documa_totals = [], []
    print(f"document: {args.markdown} ({len(lines)} lines, {count(chr(10).join(lines))} tokens full text)")
    print(f"{'query':<20} {'grep(hits)':>16} {'documa(hits)':>16}")
    for query in queries:
        grep_total, grep_hits = grep_path_tokens(count, lines, query)
        documa_total, documa_hits = documa_path_tokens(count, args.ir, query)
        grep_totals.append(grep_total)
        documa_totals.append(documa_total)
        print(f"{query:<20} {grep_total:>10} ({grep_hits:>3}) {documa_total:>10} ({documa_hits:>3})")

    def describe(values: list[int]) -> str:
        return f"median {statistics.median(values):,.0f} | mean {statistics.mean(values):,.0f} | range {min(values):,}-{max(values):,}"

    print("\ngrep  :", describe(grep_totals))
    print("documa:", describe(documa_totals))
    if statistics.median(documa_totals):
        print("median ratio grep/documa:", round(statistics.median(grep_totals) / statistics.median(documa_totals), 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
