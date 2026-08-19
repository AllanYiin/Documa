"""Measure warm dynamic-skill load latency against an existing 1,000-skill store."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from documa.skills import load_skill_bundle, skill_store_status
from documa.skills.store import active_skill_entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-dir", default=".documa")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--minimum-skills", type=int, default=1000)
    parser.add_argument("--p95-ms", type=float, default=250.0)
    args = parser.parse_args()

    store = Path(args.store_dir)
    status = skill_store_status(store)
    entries = active_skill_entries(store)
    if status.get("status") != "ok" or len(entries) < args.minimum_skills:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "code": "SKILL_BENCHMARK_CORPUS_TOO_SMALL",
                    "active_skills": len(entries),
                    "minimum_skills": args.minimum_skills,
                },
                ensure_ascii=False,
            )
        )
        return 2

    entries.sort(key=lambda item: (item["qualified_name"], item["skill_id"]))
    iterations = max(1, args.iterations)
    warm = entries[0]
    warm_result = load_skill_bundle(warm["name"], [warm["qualified_name"]], store_dir=store)
    if warm_result.status != "ok":
        print(json.dumps({"status": "error", "code": warm_result.code}, ensure_ascii=False))
        return 1

    samples: list[float] = []
    for index in range(iterations):
        entry = entries[index % len(entries)]
        started = time.perf_counter()
        result = load_skill_bundle(entry["name"], [entry["qualified_name"]], store_dir=store)
        samples.append((time.perf_counter() - started) * 1000.0)
        if result.status != "ok":
            print(json.dumps({"status": "error", "code": result.code, "skill_id": entry["skill_id"]}))
            return 1

    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))]
    payload = {
        "status": "ok" if p95 <= args.p95_ms else "failed",
        "active_skills": len(entries),
        "iterations": iterations,
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(p95, 3),
        "threshold_ms": args.p95_ms,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
