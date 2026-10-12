"""Produce reproducible DOCX/PPTX parity and performance release evidence."""

from __future__ import annotations

import argparse
import difflib
import gc
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
PARITY_FIXTURES = {
    "docx": [
        "smoke.docx",
        "docx-unicode-list.docx",
        "docx-table-merged.docx",
    ],
    "pptx": [
        "pptx-multislide.pptx",
        "pptx-unicode-bullets.pptx",
        "pptx-table-notes.pptx",
    ],
}
PARITY_THRESHOLD = 0.995
TIME_RATIO_THRESHOLD = 1.0
RSS_RATIO_THRESHOLD = 1.2


def normalized_text(parts: list[str]) -> str:
    return re.sub(r"\s+", "", "\n".join(parts)).casefold()


def character_f1(reference: str, candidate: str) -> float:
    if not reference and not candidate:
        return 1.0
    return difflib.SequenceMatcher(
        None,
        reference,
        candidate,
        autojunk=False,
    ).ratio()


def load_rust_text(path: Path) -> str:
    import rust_office

    parts: list[str] = []
    for event in rust_office.open(path):
        if event["event"] != "unit":
            continue
        for block in event["unit"]["blocks"]:
            refs = " ".join(block["source_refs"])
            if path.suffix.casefold() == ".docx" and "word/document.xml" not in refs:
                continue
            if block["metadata"].get("source_type") == "speaker_notes":
                continue
            if block["text"]:
                parts.append(block["text"])
    return normalized_text(parts)


def load_python_document(path: Path):
    if path.suffix.casefold() == ".docx":
        from documa.adapters.docx_adapter import DocxAdapter

        return DocxAdapter().parse(path)
    from documa.adapters.pptx_adapter import PptxAdapter

    return PptxAdapter().parse(path)


def load_python_text(path: Path) -> str:
    document = load_python_document(path)
    return normalized_text(
        [
            block.text.raw_text
            for page in document.pages
            for block in page.blocks
            if block.text.raw_text
        ]
    )


def consume_rust(path: Path) -> Any:
    import rust_office

    return list(rust_office.open(path))


def consume_python(path: Path) -> Any:
    return load_python_document(path)


def timed_samples(
    parser: Callable[[Path], Any],
    path: Path,
    iterations: int,
) -> list[int]:
    for _ in range(2):
        parser(path)
    samples = []
    for _ in range(iterations):
        gc.collect()
        started = time.perf_counter_ns()
        parsed = parser(path)
        samples.append(time.perf_counter_ns() - started)
        if parsed is None:
            raise RuntimeError("Parser returned no result.")
    return samples


def peak_rss_bytes() -> int:
    import psutil

    memory = psutil.Process(os.getpid()).memory_info()
    return int(getattr(memory, "peak_wset", memory.rss))


def worker(provider: str, path: Path, documa_root: Path, binding_root: Path) -> int:
    sys.path[:0] = [str(documa_root / "src"), str(binding_root)]
    parser = consume_rust if provider == "rust" else consume_python
    parsed = parser(path)
    if parsed is None:
        return 2
    print(json.dumps({"peak_rss_bytes": peak_rss_bytes()}))
    return 0


def worker_memory(
    provider: str,
    path: Path,
    documa_root: Path,
    binding_root: Path,
) -> int:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        provider,
        "--path",
        str(path),
        "--documa-root",
        str(documa_root),
        "--binding-root",
        str(binding_root),
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(json.loads(completed.stdout.strip().splitlines()[-1])["peak_rss_bytes"])


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Office v1 parity and performance",
        "",
        f"Status: **{report['status']}**",
        "",
        "## Character parity",
        "",
        "| Fixture | Character F1 | Gate |",
        "| --- | ---: | --- |",
    ]
    for case in report["parity"]["cases"]:
        lines.append(
            f"| {case['fixture']} | {case['character_f1']:.6f} | "
            f"{'PASS' if case['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Performance",
            "",
            "| Format | Rust median ms | Python median ms | Time ratio | "
            "Rust peak RSS MiB | Python peak RSS MiB | RSS ratio | Gate |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for result in report["performance"]["formats"]:
        lines.append(
            f"| {result['format']} | {result['rust_median_ms']:.4f} | "
            f"{result['python_median_ms']:.4f} | {result['time_ratio']:.4f} | "
            f"{result['rust_peak_rss_bytes'] / 1048576:.2f} | "
            f"{result['python_peak_rss_bytes'] / 1048576:.2f} | "
            f"{result['rss_ratio']:.4f} | "
            f"{'PASS' if result['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "Character F1 使用去除空白、casefold 後的 sequence matching ratio；"
            "只比較既有 Python adapter 與 Rust parser 的共同文字能力。",
            "Peak RSS 由各 provider 的獨立 Python 程序量測。",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(
    documa_root: Path,
    binding_root: Path,
    iterations: int,
) -> dict[str, Any]:
    sys.path[:0] = [str(documa_root / "src"), str(binding_root)]
    import rust_office

    parity_cases = []
    performance = []
    for format_name, names in PARITY_FIXTURES.items():
        rust_timings: list[int] = []
        python_timings: list[int] = []
        rust_peak = 0
        python_peak = 0
        for name in names:
            path = FIXTURES / name
            python_text = load_python_text(path)
            rust_text = load_rust_text(path)
            score = character_f1(python_text, rust_text)
            parity_cases.append(
                {
                    "fixture": name,
                    "format": format_name,
                    "character_f1": score,
                    "reference_characters": len(python_text),
                    "candidate_characters": len(rust_text),
                    "passed": score >= PARITY_THRESHOLD,
                }
            )
            rust_timings.extend(timed_samples(consume_rust, path, iterations))
            python_timings.extend(timed_samples(consume_python, path, iterations))
            rust_peak = max(
                rust_peak,
                worker_memory("rust", path, documa_root, binding_root),
            )
            python_peak = max(
                python_peak,
                worker_memory("python", path, documa_root, binding_root),
            )
        rust_median = statistics.median(rust_timings)
        python_median = statistics.median(python_timings)
        time_ratio = rust_median / python_median
        rss_ratio = rust_peak / python_peak
        performance.append(
            {
                "format": format_name,
                "iterations_per_fixture": iterations,
                "fixture_count": len(names),
                "rust_median_ms": rust_median / 1_000_000,
                "python_median_ms": python_median / 1_000_000,
                "time_ratio": time_ratio,
                "rust_peak_rss_bytes": rust_peak,
                "python_peak_rss_bytes": python_peak,
                "rss_ratio": rss_ratio,
                "time_passed": time_ratio <= TIME_RATIO_THRESHOLD,
                "rss_passed": rss_ratio <= RSS_RATIO_THRESHOLD,
                "passed": (
                    time_ratio <= TIME_RATIO_THRESHOLD
                    and rss_ratio <= RSS_RATIO_THRESHOLD
                ),
            }
        )
    parity_passed = all(case["passed"] for case in parity_cases)
    performance_passed = all(result["passed"] for result in performance)
    return {
        "schema_version": 1,
        "status": "PASS" if parity_passed and performance_passed else "FAIL",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "rust_office_version": rust_office.version_info(),
            "documa_root": str(documa_root.resolve()),
        },
        "thresholds": {
            "minimum_character_f1": PARITY_THRESHOLD,
            "maximum_time_ratio": TIME_RATIO_THRESHOLD,
            "maximum_rss_ratio": RSS_RATIO_THRESHOLD,
        },
        "parity": {"passed": parity_passed, "cases": parity_cases},
        "performance": {"passed": performance_passed, "formats": performance},
        "limitations": [
            "This local report covers Windows only; CI must execute other platforms.",
            "Synthetic fixtures are deterministic but do not replace a licensed real-world corpus.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documa-root", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path, default=ROOT / ".py-test")
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--worker", choices=["rust", "python"])
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.path is None:
            parser.error("--path is required with --worker")
        return worker(
            args.worker,
            args.path,
            args.documa_root,
            args.binding_root,
        )
    report = build_report(
        args.documa_root,
        args.binding_root,
        max(1, args.iterations),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "office-v1-release.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.out / "office-v1-release.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
