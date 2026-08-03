#!/usr/bin/env python3
"""Reproducible Stage 12 baseline for the private PDF corpus.

The default report stores only timings, counts, hashes, and quality proxies.
Full parser IR is private and is written only with --write-private-ir.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-baseline"


def canonical_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return {
            "$bytes_length": len(value),
            "$bytes_sha256": hashlib.sha256(value).hexdigest(),
        }
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        default=canonical_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="replace")


def digest_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timed(call: Callable[[], T], warmups: int, runs: int) -> tuple[T, dict[str, Any]]:
    if runs < 1:
        raise ValueError("measured runs must be at least one")
    for _ in range(warmups):
        call()
    samples: list[float] = []
    result_hashes: list[str] = []
    result: T | None = None
    for _ in range(runs):
        started = time.perf_counter()
        result = call()
        samples.append(time.perf_counter() - started)
        result_hashes.append(digest_value(result))
    assert result is not None
    return result, {
        "warmup_runs": warmups,
        "measured_runs": runs,
        "seconds": samples,
        "median_seconds": statistics.median(samples),
        "min_seconds": min(samples),
        "max_seconds": max(samples),
        "canonical_sha256_per_run": result_hashes,
        "deterministic": len(set(result_hashes)) == 1,
    }


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("unsupported baseline contract schema")
    if value.get("measurement", {}).get("save_private_ir_by_default") is not False:
        raise ValueError("save_private_ir_by_default must remain false")
    return value


def verify_case(corpus_dir: Path, case: dict[str, Any]) -> Path:
    path = corpus_dir / case["file_name"]
    if not path.is_file():
        raise FileNotFoundError(path)
    actual_bytes = path.stat().st_size
    if actual_bytes != case["bytes"]:
        raise ValueError(f"byte length mismatch for {path}: {actual_bytes}")
    actual_sha = digest_file(path)
    if actual_sha != case["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path}: {actual_sha}")
    return path


def load_documa(documa_root: Path) -> tuple[Any, Any, Any, Any]:
    source = documa_root / "src"
    if not source.is_dir():
        raise FileNotFoundError(f"Documa source not found: {source}")
    sys.path.insert(0, str(source))
    import fitz
    from documa.adapters.base import ParseOptions
    from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
    from documa.core.ir import to_plain_data

    return fitz, ParseOptions, PyMuPDFAdapter, to_plain_data


def canonicalize_documa(value: dict[str, Any], file_name: str) -> dict[str, Any]:
    result = json.loads(json.dumps(value, ensure_ascii=False))
    result["id"] = "<document>"
    result["source_name"] = file_name
    return result


def documa_text(value: dict[str, Any]) -> str:
    parts: list[str] = []
    for page in value.get("pages", []):
        for block in page.get("blocks", []):
            text = block.get("text") or {}
            raw = text.get("raw_text") if isinstance(text, dict) else None
            if raw:
                parts.append(str(raw))
    return "\n".join(parts)


def rust_text(value: dict[str, Any]) -> str:
    text = value.get("text")
    if isinstance(text, str):
        return text
    pages = value.get("pages")
    if isinstance(pages, list):
        parts = []
        for page in pages:
            if isinstance(page, dict) and isinstance(page.get("text"), str):
                parts.append(page["text"])
        return "\n".join(parts)
    return ""


def bbox_values(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    keys = ("x0", "y0", "x1", "y1")
    if not all(isinstance(value.get(key), (int, float)) for key in keys):
        return None
    return tuple(float(value[key]) for key in keys)  # type: ignore[return-value]


def documa_counts_and_coordinate_audit(value: dict[str, Any]) -> dict[str, Any]:
    pages = value.get("pages", [])
    blocks = 0
    spans = 0
    images = 0
    bbox_count = 0
    invalid_bbox_count = 0
    out_of_bounds_bbox_count = 0
    rotated_pages = 0
    tolerance = 0.5
    for page in pages:
        width = float(page.get("width", 0.0))
        height = float(page.get("height", 0.0))
        if int(page.get("rotation", 0) or 0) % 360:
            rotated_pages += 1
        page_blocks = page.get("blocks", [])
        page_images = page.get("images", [])
        blocks += len(page_blocks)
        images += len(page_images)
        for item in [*page_blocks, *page_images]:
            spans += len(item.get("spans", [])) if isinstance(item, dict) else 0
            candidates = [item]
            if isinstance(item, dict):
                candidates.extend(item.get("spans", []))
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                box = bbox_values(candidate.get("bbox"))
                if box is None:
                    continue
                bbox_count += 1
                x0, y0, x1, y1 = box
                if x0 > x1 or y0 > y1:
                    invalid_bbox_count += 1
                if x0 < -tolerance or y0 < -tolerance or x1 > width + tolerance or y1 > height + tolerance:
                    out_of_bounds_bbox_count += 1
    return {
        "pages": len(pages),
        "blocks": blocks,
        "spans": spans,
        "images": images,
        "tables": len(value.get("tables", [])),
        "relations": len(value.get("relations", [])),
        "document_blocks": len(value.get("document_blocks", [])),
        "chunks": len(value.get("chunks", [])),
        "coordinate_audit": {
            "coordinate_space_claim": "pymupdf_adapter_current_behavior",
            "bbox_count": bbox_count,
            "invalid_bbox_count": invalid_bbox_count,
            "out_of_bounds_bbox_count": out_of_bounds_bbox_count,
            "rotated_pages": rotated_pages,
            "tolerance_pt": tolerance,
        },
    }


def multiset_f1(left: list[Any], right: list[Any]) -> float:
    left_counts = collections.Counter(left)
    right_counts = collections.Counter(right)
    overlap = sum((left_counts & right_counts).values())
    if not left and not right:
        return 1.0
    precision = overlap / len(left) if left else 0.0
    recall = overlap / len(right) if right else 0.0
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def normalized_chars(text: str) -> list[str]:
    return [character for character in text if not character.isspace()]


def ngrams(items: list[Any], size: int) -> list[tuple[Any, ...]]:
    return [tuple(items[index : index + size]) for index in range(max(0, len(items) - size + 1))]


def quality_proxy_rust_vs_documa(rust_output: dict[str, Any], documa_output: dict[str, Any]) -> dict[str, float]:
    left_text = rust_text(rust_output)
    right_text = documa_text(documa_output)
    left_chars = normalized_chars(left_text)
    right_chars = normalized_chars(right_text)
    left_words = left_text.split()
    right_words = right_text.split()
    return {
        "non_whitespace_character_f1": multiset_f1(left_chars, right_chars),
        "character_bigram_f1": multiset_f1(ngrams(left_chars, 2), ngrams(right_chars, 2)),
        "word_bigram_f1": multiset_f1(ngrams(left_words, 2), ngrams(right_words, 2)),
    }


def write_private_ir(output_dir: Path, case_id: str, name: str, value: Any) -> None:
    private_dir = output_dir / "private-ir"
    private_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(private_dir / f"{case_id}.{name}.json.gz", "wb") as stream:
        stream.write(canonical_bytes(value))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--documa-root", type=Path, default=ROOT.parent / "Documa")
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target" / "release" / "rust-pdf.exe")
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--skip-documa", action="store_true")
    parser.add_argument("--skip-rust", action="store_true")
    parser.add_argument("--write-private-ir", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def self_test() -> None:
    assert digest_value({"b": 2, "a": 1}) == digest_value({"a": 1, "b": 2})
    assert digest_value(b"private") != digest_value(b"content")
    assert multiset_f1(list("abc"), list("abc")) == 1.0
    assert 0.0 < multiset_f1(list("abc"), list("abd")) < 1.0
    assert ngrams([1, 2, 3], 2) == [(1, 2), (2, 3)]
    _, timing = timed(lambda: {"stable": True}, 0, 2)
    assert timing["deterministic"] is True
    print("stage12 baseline self-test: ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.corpus_dir is None:
        raise SystemExit("set --corpus-dir or RUST_PDF_STAGE12_CORPUS_DIR")

    contract = load_contract(args.contract)
    measurement = contract["measurement"]
    warmups = int(measurement["warmup_runs"])
    runs = int(args.runs if args.runs is not None else measurement["measured_runs"])
    cases = [(entry, verify_case(args.corpus_dir, entry)) for entry in contract["documents"]]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    fitz = ParseOptions = PyMuPDFAdapter = to_plain_data = None
    if not args.skip_documa:
        fitz, ParseOptions, PyMuPDFAdapter, to_plain_data = load_documa(args.documa_root)
    if not args.skip_rust and not args.rust_cli.is_file():
        raise FileNotFoundError(f"Rust release CLI not found: {args.rust_cli}")

    report: dict[str, Any] = {
        "schema_version": 1,
        "contract_sha256": digest_file(args.contract),
        "private_corpus": True,
        "save_private_ir_by_default": False,
        "private_ir_written": bool(args.write_private_ir),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "documa": None if args.skip_documa else importlib.metadata.version("documa"),
            "pymupdf": None if args.skip_documa else getattr(fitz, "__doc__", "").splitlines()[0].strip(),
        },
        "measurement": {"warmup_runs": warmups, "measured_runs": runs},
        "cases": [],
    }

    for index, (case, path) in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['file_name']}", flush=True)
        case_report: dict[str, Any] = {
            "id": case["id"],
            "file_name": case["file_name"],
            "bytes": case["bytes"],
            "sha256": case["sha256"],
            "expected_pages": case["pages"],
        }
        documa_output: dict[str, Any] | None = None

        if not args.skip_documa:
            def raw_parse() -> dict[str, Any]:
                pdf = fitz.open(path)
                page_values = [page.get_text("dict") for page in pdf]
                page_count = pdf.page_count
                pdf.close()
                return {"page_count": page_count, "pages": page_values}

            raw_output, raw_timing = timed(raw_parse, warmups, runs)
            if raw_output["page_count"] != case["pages"]:
                raise ValueError(f"PyMuPDF page count mismatch for {path}")
            case_report["pymupdf_raw"] = {
                "timing": raw_timing,
                "canonical_sha256": digest_value(raw_output),
                "pages": raw_output["page_count"],
            }

            def documa_parse() -> dict[str, Any]:
                adapter = PyMuPDFAdapter()
                options = ParseOptions(
                    normalize_unicode=True,
                    extract_images=True,
                    resolve_relations=True,
                    asset_dir=None,
                )
                document = adapter.parse(path, options)
                return canonicalize_documa(to_plain_data(document), case["file_name"])

            documa_output, documa_timing = timed(documa_parse, warmups, runs)
            documa_counts = documa_counts_and_coordinate_audit(documa_output)
            if documa_counts["pages"] != case["pages"]:
                raise ValueError(f"Documa page count mismatch for {path}")
            case_report["documa"] = {
                "timing": documa_timing,
                "canonical_sha256": digest_value(documa_output),
                "text_sha256": hashlib.sha256(documa_text(documa_output).encode("utf-8", errors="replace")).hexdigest(),
                "text_characters": len(documa_text(documa_output)),
                "counts": documa_counts,
            }
            if args.write_private_ir:
                write_private_ir(args.output_dir, case["id"], "documa", documa_output)

        if not args.skip_rust:
            def rust_parse() -> dict[str, Any]:
                completed = subprocess.run(
                    [str(args.rust_cli), "extract", "--json", "--mode", "auto", str(path)],
                    check=True,
                    capture_output=True,
                )
                return json.loads(completed.stdout.decode("utf-8", errors="replace"))

            rust_output, rust_timing = timed(rust_parse, warmups, runs)
            rust_page_count = len(rust_output.get("pages", [])) if isinstance(rust_output.get("pages"), list) else rust_output.get("page_count")
            if rust_page_count != case["pages"]:
                raise ValueError(f"Rust page count mismatch for {path}: {rust_page_count}")
            rust_summary: dict[str, Any] = {
                "timing": rust_timing,
                "canonical_sha256": digest_value(rust_output),
                "text_sha256": hashlib.sha256(rust_text(rust_output).encode("utf-8", errors="replace")).hexdigest(),
                "text_characters": len(rust_text(rust_output)),
                "pages": rust_page_count,
            }
            if documa_output is not None:
                rust_summary["quality_proxy_rust_vs_documa"] = quality_proxy_rust_vs_documa(rust_output, documa_output)
            case_report["rust"] = rust_summary
            if args.write_private_ir:
                write_private_ir(args.output_dir, case["id"], "rust", rust_output)

        report["cases"].append(case_report)

    report["summary"] = summarize(report["cases"])
    report_path = args.output_dir / "report.json"
    report_path.write_bytes(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    print(f"report: {report_path}")
    return 0


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"documents": len(cases), "pages": sum(case["expected_pages"] for case in cases)}
    for parser_name in ("pymupdf_raw", "documa", "rust"):
        seconds = [case[parser_name]["timing"]["median_seconds"] for case in cases if parser_name in case]
        if seconds:
            total = sum(seconds)
            summary[parser_name] = {
                "sum_of_document_medians_seconds": total,
                "pages_per_second": summary["pages"] / total if total else None,
            }
    if "documa" in summary and "rust" in summary:
        documa_seconds = summary["documa"]["sum_of_document_medians_seconds"]
        rust_seconds = summary["rust"]["sum_of_document_medians_seconds"]
        summary["rust_speedup_vs_documa"] = documa_seconds / rust_seconds if rust_seconds else None
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
