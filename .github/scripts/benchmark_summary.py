"""Render bench.json as a GitHub job-summary markdown table.

Usage in CI: python .github/scripts/benchmark_summary.py bench.json >> "$GITHUB_STEP_SUMMARY"
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    data = json.loads(open(sys.argv[1], encoding="utf-8").read())
    summary = data["summary"]

    print("## Quality benchmark")
    print()
    print(
        f"**{summary['passed']} passed / {summary['failed']} failed / "
        f"{summary['skipped']} skipped / {summary['errors']} errors** "
        f"(mode: {data.get('mode')}, max fallback block ratio: "
        f"{summary.get('fallback_block_ratio_max', 'n/a')})"
    )
    print()
    print("| case | status | scores | threshold |")
    print("|---|---|---|---|")
    for case in data["cases"]:
        scores = next(
            (c["details"] for c in case["checks"] if c.get("name") == "quality_scores"), None
        )
        if scores is None and case["status"] not in ("failed", "error", "skipped"):
            continue  # readiness-only cases stay out of the quality table
        threshold = next(
            (c["details"]["threshold"] for c in case["checks"] if c.get("name") == "quality_threshold"),
            "",
        )
        flat = ", ".join(
            f"{name}={value.get('score', value.get('teds_s'))}"
            for name, value in (scores or {}).items()
        )
        note = flat or (case.get("message") or "")[:80]
        print(f"| {case['case_id']} | {case['status']} | {note} | {threshold} |")
    print()
    print(
        "_Skipped OCR cases are expected: CI installs Documa without the "
        "`documa[ocr]` extra. Failed cases marked as known pipeline gaps are "
        "tracked in their gold notes._"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
