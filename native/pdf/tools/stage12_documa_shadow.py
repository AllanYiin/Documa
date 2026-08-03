#!/usr/bin/env python3
"""Privacy-safe Stage 6 shadow benchmark for Documa PDF adapters."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil

from stage12_baseline import (
    canonical_bytes,
    digest_file,
    load_contract,
    verify_case,
)
from stage12_layout_benchmark import assert_privacy_safe


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-documa-shadow"
DEFAULT_DOCUMA_ROOT = ROOT.parent / "Documa"
DEFAULT_WHEEL_DIR = ROOT / "target" / "stage6-python-exact"
PROVIDERS = ("pymupdf", "rust")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--documa-root", type=Path, default=DEFAULT_DOCUMA_ROOT)
    parser.add_argument("--rust-wheel-dir", type=Path, default=DEFAULT_WHEEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--worker", choices=PROVIDERS)
    parser.add_argument("--worker-source", type=Path)
    parser.add_argument("--worker-output", type=Path)
    return parser.parse_args()


def normalized_characters(text: str) -> list[str]:
    return [character for character in text if not character.isspace()]


def counter_f1(left: collections.Counter[str], right: collections.Counter[str]) -> float:
    left_total = sum(left.values())
    right_total = sum(right.values())
    overlap = sum((left & right).values())
    if left_total == 0 and right_total == 0:
        return 1.0
    precision = overlap / left_total if left_total else 0.0
    recall = overlap / right_total if right_total else 0.0
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def text_counters(text: str) -> dict[str, dict[str, int]]:
    characters = normalized_characters(text)
    return {
        "characters": dict(collections.Counter(characters)),
        "character_bigrams": dict(
            collections.Counter(
                characters[index] + characters[index + 1]
                for index in range(max(0, len(characters) - 1))
            )
        ),
    }


def merge_counter(target: collections.Counter[str], values: dict[str, int]) -> None:
    target.update({str(key): int(value) for key, value in values.items()})


def document_text(document: Any) -> str:
    return "\n".join(
        block.text.raw_text
        for page in document.pages
        for block in page.blocks
        if block.text is not None and block.text.raw_text
    )


def canonical_document(document: Any, to_plain_data: Any) -> dict[str, Any]:
    value = to_plain_data(document)
    value["id"] = "<document>"
    value["source_name"] = "<private-pdf>"
    return value


def bbox_is_out_of_bounds(
    bbox: tuple[float, float, float, float] | None,
    width: float,
    height: float,
    tolerance: float = 0.5,
) -> tuple[bool, bool]:
    if bbox is None:
        return False, False
    x0, y0, x1, y1 = (float(value) for value in bbox)
    invalid = x0 > x1 or y0 > y1
    out_of_bounds = (
        x0 < -tolerance
        or y0 < -tolerance
        or x1 > width + tolerance
        or y1 > height + tolerance
    )
    return invalid, out_of_bounds


def audit_document(document: Any) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "pages": len(document.pages),
        "blocks": 0,
        "spans": 0,
        "table_blocks": 0,
        "images": 0,
        "decorative_images": 0,
        "links": 0,
        "bboxes": 0,
        "invalid_bboxes": 0,
        "out_of_bounds_bboxes": 0,
        "role_counts": [],
    }
    roles: collections.Counter[str] = collections.Counter()
    for page in document.pages:
        counts["blocks"] += len(page.blocks)
        counts["images"] += len(page.images)
        counts["links"] += len(page.metadata.get("links", []))
        for block in page.blocks:
            roles[str(block.type.value)] += 1
            counts["spans"] += len(block.spans)
            counts["table_blocks"] += int(block.type.value == "table")
            for candidate in [block, *block.spans]:
                if candidate.bbox is None:
                    continue
                counts["bboxes"] += 1
                invalid, out_of_bounds = bbox_is_out_of_bounds(
                    candidate.bbox, page.width, page.height
                )
                counts["invalid_bboxes"] += int(invalid)
                counts["out_of_bounds_bboxes"] += int(out_of_bounds)
        for image in page.images:
            counts["decorative_images"] += int(image.image_type == "decorative")
            if image.bbox is None:
                continue
            counts["bboxes"] += 1
            invalid, out_of_bounds = bbox_is_out_of_bounds(
                image.bbox, page.width, page.height
            )
            counts["invalid_bboxes"] += int(invalid)
            counts["out_of_bounds_bboxes"] += int(out_of_bounds)
    counts["role_counts"] = [
        {"role_kind": role, "count": count} for role, count in sorted(roles.items())
    ]
    return counts


def load_documa(documa_root: Path, wheel_dir: Path) -> tuple[Any, Any, Any, Any]:
    if not (documa_root / "src").is_dir():
        raise FileNotFoundError(f"Documa source not found: {documa_root}")
    sys.path.insert(0, str(wheel_dir))
    sys.path.insert(0, str(documa_root / "src"))
    from documa.adapters.base import ParseOptions
    from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
    from documa.adapters.rust_pdf_adapter import RustPdfAdapter
    from documa.core.ir import to_plain_data

    return ParseOptions, PyMuPDFAdapter, RustPdfAdapter, to_plain_data


def worker(args: argparse.Namespace) -> int:
    if args.worker_source is None or args.worker_output is None:
        raise ValueError("worker requires --worker-source and --worker-output")
    ParseOptions, PyMuPDFAdapter, RustPdfAdapter, to_plain_data = load_documa(
        args.documa_root, args.rust_wheel_dir
    )
    adapter = PyMuPDFAdapter() if args.worker == "pymupdf" else RustPdfAdapter()
    document = adapter.parse(
        args.worker_source,
        ParseOptions(
            normalize_unicode=True,
            extract_images=True,
            resolve_relations=True,
            asset_dir=None,
        ),
    )
    value = canonical_document(document, to_plain_data)
    serialized = canonical_bytes(value)
    text = document_text(document)
    result = {
        "provider": args.worker,
        "canonical_sha256": hashlib.sha256(serialized).hexdigest(),
        "serialized_bytes": len(serialized),
        "text_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
        "text_characters": len(text),
        "counts": audit_document(document),
        "sensitive_comparison_counters": text_counters(text),
    }
    args.worker_output.parent.mkdir(parents=True, exist_ok=True)
    args.worker_output.write_bytes(canonical_bytes(result))
    return 0


def run_worker(
    provider: str,
    pdf_path: Path,
    documa_root: Path,
    wheel_dir: Path,
) -> tuple[dict[str, Any], float, int]:
    with tempfile.TemporaryDirectory(prefix="stage12-shadow-") as temporary:
        output_path = Path(temporary) / "worker.json"
        command = [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "--worker",
            provider,
            "--worker-source",
            str(pdf_path),
            "--worker-output",
            str(output_path),
            "--documa-root",
            str(documa_root),
            "--rust-wheel-dir",
            str(wheel_dir),
        ]
        started = time.perf_counter()
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        monitored = psutil.Process(process.pid)
        peak_rss = 0
        while process.poll() is None:
            try:
                peak_rss = max(peak_rss, monitored.memory_info().rss)
            except psutil.Error:
                pass
            time.sleep(0.005)
        try:
            peak_rss = max(peak_rss, monitored.memory_info().rss)
        except psutil.Error:
            pass
        stderr = process.stderr.read() if process.stderr is not None else b""
        elapsed = time.perf_counter() - started
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        return json.loads(output_path.read_text(encoding="utf-8")), elapsed, peak_rss


def measured_provider(
    provider: str,
    pdf_path: Path,
    documa_root: Path,
    wheel_dir: Path,
    warmups: int,
    runs: int,
    progress: str,
) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    for index in range(warmups):
        print(f"{progress} {provider} warmup {index + 1}/{warmups}", flush=True)
        run_worker(provider, pdf_path, documa_root, wheel_dir)
    samples: list[float] = []
    peaks: list[int] = []
    results: list[dict[str, Any]] = []
    for index in range(runs):
        print(f"{progress} {provider} measured {index + 1}/{runs}", flush=True)
        result, elapsed, peak = run_worker(provider, pdf_path, documa_root, wheel_dir)
        results.append(result)
        samples.append(elapsed)
        peaks.append(peak)
    hashes = [result["canonical_sha256"] for result in results]
    last = results[-1]
    sensitive = last.pop("sensitive_comparison_counters")
    provider_report = {
        **last,
        "timing": {
            "warmup_runs": warmups,
            "measured_runs": runs,
            "seconds": samples,
            "median_seconds": statistics.median(samples),
            "min_seconds": min(samples),
            "max_seconds": max(samples),
            "peak_rss_bytes_per_run": peaks,
            "max_peak_rss_bytes": max(peaks),
        },
        "deterministic": len(set(hashes)) == 1,
        "canonical_sha256_per_run": hashes,
    }
    return provider_report, sensitive


def self_test() -> None:
    assert counter_f1(collections.Counter("abc"), collections.Counter("abc")) == 1.0
    assert 0.0 < counter_f1(collections.Counter("abc"), collections.Counter("abd")) < 1.0
    counters = text_counters("a b")
    assert counters["characters"] == {"a": 1, "b": 1}
    assert counters["character_bigrams"] == {"ab": 1}
    sample = {
        "contains_extracted_content": False,
        "private_ir_written": False,
        "cases": [{"counts": {"pages": 1, "spans": 0}}],
    }
    assert_privacy_safe(sample)
    print("stage12 Documa shadow self-test: ok")


def main() -> int:
    args = parse_args()
    if args.worker:
        return worker(args)
    if args.self_test:
        self_test()
        return 0
    if args.corpus_dir is None:
        raise SystemExit("set --corpus-dir or RUST_PDF_STAGE12_CORPUS_DIR")
    if not args.rust_wheel_dir.is_dir():
        raise FileNotFoundError(f"exact Rust wheel directory not found: {args.rust_wheel_dir}")

    contract = load_contract(args.contract)
    measurement = contract["measurement"]
    warmups = int(measurement["warmup_runs"])
    runs = int(args.runs if args.runs is not None else measurement["measured_runs"])
    cases = [(entry, verify_case(args.corpus_dir, entry)) for entry in contract["documents"]]
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "stage12_documa_shadow",
        "contract_sha256": digest_file(args.contract),
        "private_corpus": True,
        "private_ir_written": False,
        "contains_extracted_content": False,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "psutil": psutil.__version__,
            "documa_root": str(args.documa_root),
            "rust_wheel_dir": str(args.rust_wheel_dir),
        },
        "measurement": {"warmup_runs": warmups, "measured_runs": runs},
        "cases": [],
    }
    aggregate: dict[str, dict[str, collections.Counter[str]]] = {
        provider: {
            "characters": collections.Counter(),
            "character_bigrams": collections.Counter(),
        }
        for provider in PROVIDERS
    }
    for case_index, (case, path) in enumerate(cases, start=1):
        case_report: dict[str, Any] = {
            "id": case["id"],
            "file_name": case["file_name"],
            "bytes": case["bytes"],
            "sha256": case["sha256"],
            "expected_pages": case["pages"],
        }
        sensitive_by_provider: dict[str, dict[str, dict[str, int]]] = {}
        for provider in PROVIDERS:
            provider_report, sensitive = measured_provider(
                provider,
                path,
                args.documa_root,
                args.rust_wheel_dir,
                warmups,
                runs,
                f"[{case_index}/{len(cases)}] {case['file_name']}",
            )
            if provider_report["counts"]["pages"] != case["pages"]:
                raise ValueError(f"{provider} page count mismatch for {path}")
            case_report[provider] = provider_report
            sensitive_by_provider[provider] = sensitive
            for counter_name in ("characters", "character_bigrams"):
                merge_counter(
                    aggregate[provider][counter_name],
                    sensitive[counter_name],
                )
        left = sensitive_by_provider["rust"]
        right = sensitive_by_provider["pymupdf"]
        case_report["quality_rust_vs_pymupdf"] = {
            "non_whitespace_character_f1": counter_f1(
                collections.Counter(left["characters"]),
                collections.Counter(right["characters"]),
            ),
            "character_bigram_f1": counter_f1(
                collections.Counter(left["character_bigrams"]),
                collections.Counter(right["character_bigrams"]),
            ),
        }
        assert_privacy_safe(
            {
                "contains_extracted_content": False,
                "cases": [case_report],
            }
        )
        report["cases"].append(case_report)

    pages = sum(case["expected_pages"] for case in report["cases"])
    summary: dict[str, Any] = {"documents": len(report["cases"]), "pages": pages}
    for provider in PROVIDERS:
        seconds = sum(case[provider]["timing"]["median_seconds"] for case in report["cases"])
        summary[provider] = {
            "sum_of_document_medians_seconds": seconds,
            "pages_per_second": pages / seconds if seconds else None,
            "max_peak_rss_bytes": max(
                case[provider]["timing"]["max_peak_rss_bytes"] for case in report["cases"]
            ),
            "total_serialized_bytes": sum(
                case[provider]["serialized_bytes"] for case in report["cases"]
            ),
            "all_deterministic": all(case[provider]["deterministic"] for case in report["cases"]),
        }
    summary["rust_speedup_vs_pymupdf_documa"] = (
        summary["pymupdf"]["sum_of_document_medians_seconds"]
        / summary["rust"]["sum_of_document_medians_seconds"]
    )
    summary["rust_peak_rss_ratio_vs_pymupdf"] = (
        summary["rust"]["max_peak_rss_bytes"] / summary["pymupdf"]["max_peak_rss_bytes"]
    )
    summary["rust_serialized_size_ratio_vs_pymupdf"] = (
        summary["rust"]["total_serialized_bytes"]
        / summary["pymupdf"]["total_serialized_bytes"]
    )
    summary["quality_rust_vs_pymupdf"] = {
        "non_whitespace_character_f1": counter_f1(
            aggregate["rust"]["characters"],
            aggregate["pymupdf"]["characters"],
        ),
        "character_bigram_f1": counter_f1(
            aggregate["rust"]["character_bigrams"],
            aggregate["pymupdf"]["character_bigrams"],
        ),
    }
    summary["default_provider_cutover_allowed"] = False
    report["summary"] = summary
    assert_privacy_safe(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    report_path.write_bytes(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
