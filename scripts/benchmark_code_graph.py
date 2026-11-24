"""Deterministic repository-graph scale gate.

The synthetic corpus keeps symbol density realistic enough for indexing while
making the requested line count deterministic. It measures cold sync, no-op
sync, one-file incremental sync, bounded lookup, and process peak RSS.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from documa.codegraph import code_context, query_code_graph, sync_code_graph
from documa.interfaces.token_counting import TiktokenCounter


def _peak_rss_bytes() -> int | None:
    if os.name == "nt":
        try:
            import ctypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
            psapi.GetProcessMemoryInfo.restype = ctypes.c_int
            handle = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return int(counters.PeakWorkingSetSize)
        except (AttributeError, OSError):
            return None
        return None
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value if sys.platform == "darwin" else value * 1024
    except (ImportError, ValueError):
        return None


def _write_corpus(root: Path, lines: int, files: int) -> tuple[int, Path]:
    package = root / "src" / "scale_fixture"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("\n", encoding="utf-8")
    lines_per_file = max(8, lines // files)
    actual = 1
    target = package / "module_00000.py"
    for index in range(files):
        path = package / f"module_{index:05d}.py"
        header = []
        if index:
            header.append(f"from . import module_{index - 1:05d}")
        header.extend(["", f"def function_{index:05d}():", "    total = 0"])
        body_count = max(1, lines_per_file - len(header) - 1)
        body = [f"    total += {offset % 7}" for offset in range(body_count)]
        payload = "\n".join([*header, *body, "    return total"]) + "\n"
        path.write_text(payload, encoding="utf-8")
        actual += payload.count("\n")
    return actual, target


def _timed(function):
    started = time.perf_counter()
    result = function()
    return result, time.perf_counter() - started


def _query_sample(root: Path, store: Path, suite: Path, intent: str) -> dict:
    cases = json.loads(suite.read_text(encoding="utf-8"))
    counter = TiktokenCounter("o200k_base")
    documents = [path for case in cases if case["mode"] == "cross_source" for path in case["expected"]]
    sync = sync_code_graph(root, source_roots=["src"], document_paths=documents, store_dir=store)
    results = []
    for case in cases:
        expected = set(case["expected"])
        missing = sorted(path for path in expected if not (root / path).is_file())
        if missing:
            raise ValueError(f"{case['id']}: expected locator missing: {missing}")
        result, elapsed = _timed(
            lambda: code_context(
                sync["workspace_id"],
                case["query"],
                intent="dependencies" if intent == "lookup" and case["mode"] == "dependency" else intent,
                symbols=[case["symbol"]] if "symbol" in case else None,
                store_dir=store,
                token_counter=counter,
            )
        )
        ranked_nodes = result["nodes"][:3]
        locators = list(dict.fromkeys(node["sourceLocator"] for node in ranked_nodes if node.get("sourceLocator")))
        evidence = result["evidence"]["blocks"]
        evidence_locators = [block["sourceLocator"] for block in evidence]
        serialized = json.dumps({"status": "ok", **result}, ensure_ascii=False, separators=(",", ":"))

        def rg_read():
            completed = subprocess.run(
                [
                    "rg", "--files-with-matches", "--sort", "path", "-i", "-g", "*.py", "-g", "*.md",
                    "-e", case["rg"], "src", "docs", "plugins",
                ],
                cwd=root,
                capture_output=True,
                encoding="utf-8",
                check=False,
            )
            if completed.returncode > 1:
                raise RuntimeError(completed.stderr.strip())
            paths = [Path(line).as_posix() for line in completed.stdout.splitlines()[:3]]
            bodies = [(root / path).read_text(encoding="utf-8") for path in paths]
            return paths, counter.count("\n".join([*paths, *bodies]))

        (rg_paths, rg_tokens), rg_seconds = _timed(rg_read)
        results.append({
            "id": case["id"],
            "mode": case["mode"],
            "expected": sorted(expected),
            "graph": {
                "top3": locators,
                "hit": bool(expected.intersection(locators)),
                "exactHit": bool(case.get("symbol") and ranked_nodes and ranked_nodes[0]["qualifiedName"] == case["symbol"]),
                "evidenceHit": bool(expected.intersection(evidence_locators)),
                "seconds": round(elapsed, 6),
                "calls": 1,
                "tokens": counter.count(serialized),
            },
            "rgRead": {
                "top3": rg_paths,
                "hit": bool(expected.intersection(rg_paths)),
                "seconds": round(rg_seconds, 6),
                "calls": 1 + len(rg_paths),
                "tokens": rg_tokens,
            },
        })
    stage2 = [case for case in results if case["mode"] in {"exact", "text"}]
    gates = {
        "top3": sum(case["graph"]["hit"] for case in stage2) / len(stage2) >= 0.8,
        "exactFirst": all(case["graph"]["exactHit"] for case in stage2 if case["mode"] == "exact"),
        "pdfProviderEvidence": all(
            case["graph"]["hit"] and case["graph"]["evidenceHit"] for case in stage2 if case["id"] == "text-pdf-provider"
        ),
        "responseBudget": all(case["graph"]["tokens"] <= 2000 for case in stage2),
    }
    return {"intent": intent, "tokenizer": counter.name, "coldMs": sync["duration_ms"], "cases": results, "stage2Gates": gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, default=1_000_000)
    parser.add_argument("--files", type=int, default=1000)
    parser.add_argument("--cold-max-seconds", type=float, default=600.0)
    parser.add_argument("--noop-max-seconds", type=float, default=2.0)
    parser.add_argument("--incremental-max-seconds", type=float, default=5.0)
    parser.add_argument("--query-max-seconds", type=float, default=0.3)
    parser.add_argument("--rss-max-bytes", type=int, default=2_000_000_000)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--scratch-dir", type=Path, help="Parent for the temporary benchmark directory.")
    parser.add_argument("--query-suite", type=Path, help="Measure repository queries instead of the synthetic scale gate.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--query-intent", choices=("lookup", "explore"), default="lookup")
    parser.add_argument("--check-query-quality", action="store_true", help="Require the Stage 2 explore gates.")
    args = parser.parse_args(argv)
    if args.lines < 1 or args.files < 1:
        parser.error("--lines and --files must be positive")

    with tempfile.TemporaryDirectory(prefix="documa-codegraph-benchmark-", dir=args.scratch_dir) as temp_dir:
        root = Path(temp_dir)
        if args.query_suite is not None:
            report = _query_sample(args.repository.resolve(), root, args.query_suite, args.query_intent)
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return int(args.check_query_quality and not all(report["stage2Gates"].values()))
        actual_lines, changed_file = _write_corpus(root, args.lines, args.files)
        store = root / ".documa"
        cold, cold_seconds = _timed(lambda: sync_code_graph(root, store_dir=store))
        noop, noop_seconds = _timed(lambda: sync_code_graph(root, store_dir=store))
        original = changed_file.read_text(encoding="utf-8")
        changed_file.write_text(original.replace("total += 0", "total += 9", 1), encoding="utf-8")
        incremental, incremental_seconds = _timed(lambda: sync_code_graph(root, store_dir=store))
        _, query_seconds = _timed(
            lambda: query_code_graph(
                cold["workspace_id"],
                intent="lookup",
                symbols=["scale_fixture.module_00000.function_00000"],
                store_dir=store,
            )
        )
        peak_rss = _peak_rss_bytes()
        gates = {
            "cold": cold_seconds <= args.cold_max_seconds,
            "noop": noop_seconds <= args.noop_max_seconds,
            "incremental": incremental_seconds <= args.incremental_max_seconds,
            "query": query_seconds <= args.query_max_seconds,
            "rss": peak_rss is None or peak_rss <= args.rss_max_bytes,
            "noopParsedZero": noop["parsed"] == 0,
            "incrementalParsedOne": incremental["parsed"] == 1,
        }
        payload = {
            "status": "ok" if all(gates.values()) else "failed",
            "requestedLines": args.lines,
            "actualLines": actual_lines,
            "files": args.files + 1,
            "coldSeconds": round(cold_seconds, 6),
            "noopSeconds": round(noop_seconds, 6),
            "incrementalSeconds": round(incremental_seconds, 6),
            "querySeconds": round(query_seconds, 6),
            "peakRssBytes": peak_rss,
            "nodeCount": incremental["node_count"],
            "edgeCount": incremental["edge_count"],
            "gates": gates,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return 0 if args.report_only or payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
