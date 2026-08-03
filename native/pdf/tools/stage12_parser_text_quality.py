#!/usr/bin/env python3
"""Stage 7.2 privacy-safe raw parser text comparison without Documa table rewriting."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from stage12_baseline import canonical_bytes, digest_file, load_contract, verify_case
from stage12_page_quality_diff import (
    as_counter,
    assert_privacy_safe,
    counter_metrics,
    sensitive_counters,
    signed_delta,
    unicode_summary,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-stage7b-parser-text"
DEFAULT_WHEEL_DIR = ROOT / "target" / "stage6c2e-final-python-exact"
DEFAULT_STAGE7A = ROOT / "target" / "stage12-stage7a-page-quality" / "report.json"
PROVIDERS = ("pymupdf_raw", "rust_layout_source")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--rust-wheel-dir", type=Path, default=DEFAULT_WHEEL_DIR)
    parser.add_argument("--stage7a-report", type=Path, default=DEFAULT_STAGE7A)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--worst-pages", type=int, default=50)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--worker", choices=PROVIDERS)
    parser.add_argument("--worker-source", type=Path)
    parser.add_argument("--worker-output", type=Path)
    return parser.parse_args()


def import_pymupdf() -> Any:
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz

        return fitz


def pymupdf_raw_pages(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pymupdf = import_pymupdf()
    document = pymupdf.open(path)
    pages: list[dict[str, Any]] = []
    document_parts: list[str] = []
    try:
        for page_number, page in enumerate(document, start=1):
            raw = page.get_text("dict", sort=False)
            block_parts: list[str] = []
            spans = 0
            for block in raw.get("blocks", []):
                if int(block.get("type", 0)) != 0:
                    continue
                parts: list[str] = []
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        spans += 1
                        parts.append(str(span.get("text") or ""))
                block_parts.append("".join(parts))
            text = "\n".join(part for part in block_parts if part)
            document_parts.append(text)
            characters = [character for character in text if not character.isspace()]
            pages.append(
                {
                    "page_number": page_number,
                    "non_whitespace_length": len(characters),
                    "blocks": len(block_parts),
                    "spans": spans,
                    "unicode": unicode_summary(characters),
                    "sensitive_comparison_counters": sensitive_counters(text),
                }
            )
    finally:
        document.close()
    return pages, sensitive_counters("\n".join(document_parts))


def source_order_text(page: dict[str, Any]) -> tuple[str, int, int]:
    nodes = {str(node.get("id")): node for node in page.get("semantic_nodes", [])}
    order = [str(value) for value in page.get("orders", {}).get("source_order", [])]
    order_set = set(order)
    ordered = [nodes[node_id] for node_id in order if node_id in nodes]
    ordered.extend(node for node_id, node in nodes.items() if node_id not in order_set)
    return (
        "\n".join(str(node.get("text") or "") for node in ordered if node.get("text")),
        len(ordered),
        sum(len(node.get("spans", [])) for node in ordered),
    )


def rust_layout_pages(path: Path, wheel_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sys.path.insert(0, str(wheel_dir))
    import rust_pdf

    stream = rust_pdf.extract_layout_stream(
        path.read_bytes(),
        normalize_unicode=True,
        quality=True,
        debug_glyphs=False,
        timings=False,
    )
    pages: list[dict[str, Any]] = []
    document_parts: list[str] = []
    for page in stream:
        text, blocks, spans = source_order_text(page)
        root_text = str(page.get("text") or "")
        characters = [character for character in text if not character.isspace()]
        root_counter = sensitive_counters(root_text)["characters"]
        source_counter = sensitive_counters(text)["characters"]
        pages.append(
            {
                "page_number": int(page["page_number"]),
                "non_whitespace_length": len(characters),
                "blocks": blocks,
                "spans": spans,
                "page_root_character_multiset_matches_source_nodes": root_counter == source_counter,
                "unicode": unicode_summary(characters),
                "sensitive_comparison_counters": sensitive_counters(text),
                "warning_code_counts": {},
            }
        )
        document_parts.append(text)
    warnings: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for warning in stream.metadata.get("warnings", []):
        if not isinstance(warning, dict) or not warning.get("code"):
            continue
        page_index = warning.get("page_index")
        if isinstance(page_index, int):
            warnings[page_index + 1][str(warning["code"])] += 1
    for page in pages:
        page["warning_code_counts"] = dict(
            sorted(warnings.get(int(page["page_number"]), collections.Counter()).items())
        )
    return pages, sensitive_counters("\n".join(document_parts))


def worker(args: argparse.Namespace) -> int:
    if args.worker_source is None or args.worker_output is None:
        raise ValueError("worker requires --worker-source and --worker-output")
    if args.worker == "pymupdf_raw":
        pages, document_counters = pymupdf_raw_pages(args.worker_source)
    else:
        pages, document_counters = rust_layout_pages(args.worker_source, args.rust_wheel_dir)
    result = {
        "provider": args.worker,
        "page_count": len(pages),
        "pages": pages,
        "document_sensitive_comparison_counters": document_counters,
    }
    args.worker_output.parent.mkdir(parents=True, exist_ok=True)
    args.worker_output.write_bytes(canonical_bytes(result))
    return 0


def run_worker(
    provider: str,
    path: Path,
    wheel_dir: Path,
    temporary_root: Path,
) -> tuple[dict[str, Any], float]:
    output = temporary_root / f"{provider}.json"
    command = [
        sys.executable,
        "-B",
        str(Path(__file__).resolve()),
        "--worker",
        provider,
        "--worker-source",
        str(path),
        "--worker-output",
        str(output),
        "--rust-wheel-dir",
        str(wheel_dir),
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    return json.loads(output.read_text(encoding="utf-8")), elapsed


def compare_page(
    document_id: str,
    rust: dict[str, Any],
    pymupdf: dict[str, Any],
) -> dict[str, Any]:
    rust_sensitive = rust["sensitive_comparison_counters"]
    pymupdf_sensitive = pymupdf["sensitive_comparison_counters"]
    character = counter_metrics(
        as_counter(rust_sensitive["characters"]),
        as_counter(pymupdf_sensitive["characters"]),
    )
    bigram = counter_metrics(
        as_counter(rust_sensitive["character_bigrams"]),
        as_counter(pymupdf_sensitive["character_bigrams"]),
    )
    reasons: list[str] = []
    delta = int(rust["non_whitespace_length"]) - int(pymupdf["non_whitespace_length"])
    if delta < 0:
        reasons.append("rust_shorter")
    elif delta > 0:
        reasons.append("rust_longer")
    if character["f1"] < 0.995:
        reasons.append("raw_character_mismatch")
    if bigram["f1"] + 0.002 < character["f1"]:
        reasons.append("source_order_or_adjacency_difference")
    if rust.get("warning_code_counts"):
        reasons.append("rust_warning_present")
    return {
        "document_id": document_id,
        "page_number": int(rust["page_number"]),
        "rust": {
            "non_whitespace_length": int(rust["non_whitespace_length"]),
            "blocks": int(rust["blocks"]),
            "spans": int(rust["spans"]),
            "page_root_character_multiset_matches_source_nodes": bool(
                rust["page_root_character_multiset_matches_source_nodes"]
            ),
        },
        "pymupdf_raw": {
            "non_whitespace_length": int(pymupdf["non_whitespace_length"]),
            "blocks": int(pymupdf["blocks"]),
            "spans": int(pymupdf["spans"]),
        },
        "non_whitespace_length_delta": delta,
        "character_multiset": character,
        "character_bigram": bigram,
        "unicode_category_delta": signed_delta(
            rust["unicode"]["categories"], pymupdf["unicode"]["categories"]
        ),
        "unicode_script_delta": signed_delta(
            rust["unicode"]["scripts"], pymupdf["unicode"]["scripts"]
        ),
        "rust_warning_code_counts": [
            {"code": code, "count": int(count)}
            for code, count in sorted(rust.get("warning_code_counts", {}).items())
        ],
        "reason_candidates": reasons,
    }


def stage7a_reference(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    quality = value["summary"]["quality_rust_vs_pymupdf"]
    return {
        "report_sha256": digest_file(path),
        "comparison_level": "complete_documa_adapters_after_table_reconstruction",
        "non_whitespace_character_f1": float(quality["non_whitespace_character"]["f1"]),
        "character_bigram_f1": float(quality["character_bigram"]["f1"]),
    }


def self_test() -> None:
    page = {
        "semantic_nodes": [{"id": "a", "text": "A", "spans": []}, {"id": "b", "text": "B", "spans": []}],
        "orders": {"source_order": ["b", "a"]},
    }
    text, blocks, spans = source_order_text(page)
    assert text == "B\nA"
    assert blocks == 2 and spans == 0
    report = {
        "contains_extracted_content": False,
        "contains_character_keys": False,
        "private_ir_written": False,
        "cases": [],
    }
    assert_privacy_safe(report)
    print("stage12 Stage 7.2 raw parser quality self-test: ok")


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
    if not args.stage7a_report.is_file():
        raise FileNotFoundError(f"Stage 7.1 report not found: {args.stage7a_report}")

    contract = load_contract(args.contract)
    cases = [(entry, verify_case(args.corpus_dir, entry)) for entry in contract["documents"]]
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "stage-7.2-oracle-calibration",
        "contract_sha256": digest_file(args.contract),
        "private_corpus": True,
        "private_ir_written": False,
        "contains_extracted_content": False,
        "contains_character_keys": False,
        "contains_source_path": False,
        "temporary_counter_files_removed": False,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "provider_process_isolation": True,
        },
        "stage7a_reference": stage7a_reference(args.stage7a_report),
        "cases": [],
    }
    aggregate = {
        "rust_characters": collections.Counter(),
        "rust_bigrams": collections.Counter(),
        "pymupdf_characters": collections.Counter(),
        "pymupdf_bigrams": collections.Counter(),
    }
    timings = {provider: [] for provider in PROVIDERS}
    with tempfile.TemporaryDirectory(prefix="stage12-stage7b-private-") as temporary:
        temporary_root = Path(temporary)
        for index, (case, path) in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['file_name']}", flush=True)
            results: dict[str, dict[str, Any]] = {}
            for provider in PROVIDERS:
                results[provider], elapsed = run_worker(
                    provider, path, args.rust_wheel_dir, temporary_root / str(case["id"])
                )
                timings[provider].append(elapsed)
            rust = results["rust_layout_source"]
            pymupdf = results["pymupdf_raw"]
            expected = int(case["pages"])
            if int(rust["page_count"]) != expected or int(pymupdf["page_count"]) != expected:
                raise ValueError(f"page count mismatch for {case['id']}")
            rust_pages = {int(page["page_number"]): page for page in rust["pages"]}
            pymupdf_pages = {int(page["page_number"]): page for page in pymupdf["pages"]}
            if set(rust_pages) != set(pymupdf_pages) or len(rust_pages) != expected:
                raise ValueError(f"page alignment mismatch for {case['id']}")
            pages = [
                compare_page(case["id"], rust_pages[number], pymupdf_pages[number])
                for number in range(1, expected + 1)
            ]
            rust_sensitive = rust["document_sensitive_comparison_counters"]
            pymupdf_sensitive = pymupdf["document_sensitive_comparison_counters"]
            case_counters = {
                "rust_characters": as_counter(rust_sensitive["characters"]),
                "rust_bigrams": as_counter(rust_sensitive["character_bigrams"]),
                "pymupdf_characters": as_counter(pymupdf_sensitive["characters"]),
                "pymupdf_bigrams": as_counter(pymupdf_sensitive["character_bigrams"]),
            }
            for name, counter in case_counters.items():
                aggregate[name].update(counter)
            report["cases"].append(
                {
                    "id": case["id"],
                    "file_name": case["file_name"],
                    "bytes": int(case["bytes"]),
                    "sha256": case["sha256"],
                    "expected_pages": expected,
                    "quality_rust_vs_pymupdf_raw": {
                        "non_whitespace_character": counter_metrics(
                            case_counters["rust_characters"], case_counters["pymupdf_characters"]
                        ),
                        "character_bigram": counter_metrics(
                            case_counters["rust_bigrams"], case_counters["pymupdf_bigrams"]
                        ),
                    },
                    "pages": pages,
                }
            )
    report["temporary_counter_files_removed"] = not temporary_root.exists()

    all_pages = [page for case in report["cases"] for page in case["pages"]]
    ranked = sorted(
        all_pages,
        key=lambda page: (
            float(page["character_multiset"]["f1"]),
            float(page["character_bigram"]["f1"]),
            -abs(int(page["non_whitespace_length_delta"])),
            str(page["document_id"]),
            int(page["page_number"]),
        ),
    )
    total_pages = len(all_pages)
    report["summary"] = {
        "documents": len(report["cases"]),
        "pages": total_pages,
        "quality_rust_vs_pymupdf_raw": {
            "non_whitespace_character": counter_metrics(
                aggregate["rust_characters"], aggregate["pymupdf_characters"]
            ),
            "character_bigram": counter_metrics(
                aggregate["rust_bigrams"], aggregate["pymupdf_bigrams"]
            ),
        },
        "pages_below_character_f1_0_995": sum(
            float(page["character_multiset"]["f1"]) < 0.995 for page in all_pages
        ),
        "pages_below_bigram_f1_0_99": sum(
            float(page["character_bigram"]["f1"]) < 0.99 for page in all_pages
        ),
        "rust_page_root_multiset_matches_source_nodes": all(
            page["rust"]["page_root_character_multiset_matches_source_nodes"] for page in all_pages
        ),
        "timing": {
            provider: {
                "seconds": sum(timings[provider]),
                "pages_per_second": total_pages / sum(timings[provider]),
            }
            for provider in PROVIDERS
        },
        "worst_pages": [
            {
                "document_id": page["document_id"],
                "page_number": page["page_number"],
                "character_f1": page["character_multiset"]["f1"],
                "bigram_f1": page["character_bigram"]["f1"],
                "non_whitespace_length_delta": page["non_whitespace_length_delta"],
                "reason_candidates": page["reason_candidates"],
            }
            for page in ranked[: max(0, int(args.worst_pages))]
        ],
        "default_provider_cutover_allowed": False,
    }
    assert_privacy_safe(report, [args.corpus_dir, args.rust_wheel_dir])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    report_path.write_bytes(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    )
    print(f"report: {report_path}")
    print(f"sha256: {hashlib.sha256(report_path.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
