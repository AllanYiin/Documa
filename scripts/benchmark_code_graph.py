"""Deterministic repository-graph scale gate.

The synthetic corpus keeps symbol density realistic enough for indexing while
making the requested line count deterministic. It measures cold sync, no-op
sync, one-file incremental sync, bounded lookup, and process peak RSS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from documa.codegraph import query_code_graph, sync_code_graph


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
    args = parser.parse_args(argv)
    if args.lines < 1 or args.files < 1:
        parser.error("--lines and --files must be positive")

    with tempfile.TemporaryDirectory(prefix="documa-codegraph-benchmark-") as temp_dir:
        root = Path(temp_dir)
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
