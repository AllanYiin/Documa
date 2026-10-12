"""Compare collected OCR candidates with NDJSON using an original repeated scan.

Measures complete CLI RSS, including JSON serialization; this is not a parser-only
allocation benchmark or a replacement for private corpus/human quality gates.
Requires psutil. No extracted document content is retained in the report.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import psutil
from generate_priority_fixtures import ocr_pdf

ROOT = Path(__file__).resolve().parents[1]


def measure(command):
    start = time.perf_counter()
    with subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    ) as process:
        observed = psutil.Process(process.pid)
        peak = 0
        while process.poll() is None:
            try:
                peak = max(peak, observed.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            if time.perf_counter() - start > 120:
                process.kill()
                raise TimeoutError("synthetic OCR candidate benchmark timed out")
            time.sleep(0.005)
        stderr = process.communicate()[1]
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
    if peak == 0:
        raise RuntimeError("process ended before an RSS sample could be captured")
    return {"peak_rss_bytes": peak, "seconds": round(time.perf_counter() - start, 6)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cli",
        type=Path,
        default=ROOT
        / (
            "target/release/rust-pdf.exe"
            if os.name == "nt"
            else "target/release/rust-pdf"
        ),
    )
    parser.add_argument("--pages", type=int, default=48)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "target/priority-evidence/ocr-memory.json"
    )
    args = parser.parse_args()
    if not 2 <= args.pages <= 256:
        parser.error("pages must be in 2..=256")
    work = ROOT / "target/priority-evidence"
    work.mkdir(parents=True, exist_ok=True)
    pdf = work / "synthetic-repeated-scan.pdf"
    pdf.write_bytes(ocr_pdf(args.pages, bytes([255]) * 512 * 512, 512, 512))
    commands = {
        "collected_pretty_json": [str(args.cli.resolve()), "ocr-candidates", str(pdf)],
        "streamed_compact_ndjson": [
            str(args.cli.resolve()),
            "ocr-candidates",
            str(pdf),
            "--ndjson",
        ],
    }
    results = {}
    for mode, command in commands.items():
        measure(command)  # Warm-up is excluded from measured runs.
        runs = [measure(command) for _ in range(3)]
        results[mode] = {
            "runs": runs,
            "peak_rss_bytes": max(run["peak_rss_bytes"] for run in runs),
        }
    ratio = (
        results["streamed_compact_ndjson"]["peak_rss_bytes"]
        / results["collected_pretty_json"]["peak_rss_bytes"]
    )
    report = {
        "schema_version": 1,
        "synthetic": True,
        "contains_extracted_content": False,
        "pages": args.pages,
        "image_dimensions": [512, 512],
        "runs_per_mode": 3,
        "scope": "complete CLI, including pretty JSON versus compact NDJSON serialization",
        "results": results,
        "stream_to_collect_peak_rss_ratio": ratio,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
