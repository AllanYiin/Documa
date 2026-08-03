#!/usr/bin/env python3
"""Privacy-safe Stage 7.1 page-level quality localization for Documa adapters."""

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
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from stage12_baseline import canonical_bytes, digest_file, load_contract, verify_case
from stage12_documa_shadow import (
    DEFAULT_DOCUMA_ROOT,
    document_text,
    load_documa,
    normalized_characters,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-stage7a-page-quality"
DEFAULT_WHEEL_DIR = ROOT / "target" / "stage6c2e-final-python-exact"
DEFAULT_REFERENCE = ROOT / "target" / "stage12-stage6d-documa-shadow-final" / "report.json"
PROVIDERS = ("pymupdf", "rust")
FORBIDDEN_REPORT_KEYS = {
    "text",
    "raw_text",
    "normalized_text",
    "characters",
    "character_bigrams",
    "character_counter",
    "bigram_counter",
    "source_path",
    "url",
    "uri",
    "ir",
}
FURNITURE_ROLES = {"page_header", "page_footer"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--documa-root", type=Path, default=DEFAULT_DOCUMA_ROOT)
    parser.add_argument("--rust-wheel-dir", type=Path, default=DEFAULT_WHEEL_DIR)
    parser.add_argument("--reference-report", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--worst-pages", type=int, default=50)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--worker", choices=PROVIDERS)
    parser.add_argument("--worker-source", type=Path)
    parser.add_argument("--worker-output", type=Path)
    return parser.parse_args()


def ngram_counter(values: list[str], width: int = 2) -> collections.Counter[str]:
    return collections.Counter(
        "".join(values[index : index + width])
        for index in range(max(0, len(values) - width + 1))
    )


def sensitive_counters(text: str) -> dict[str, dict[str, int]]:
    values = normalized_characters(text)
    return {
        "characters": dict(collections.Counter(values)),
        "character_bigrams": dict(ngram_counter(values)),
    }


def counter_metrics(
    rust: collections.Counter[str],
    pymupdf: collections.Counter[str],
) -> dict[str, float]:
    rust_total = sum(rust.values())
    pymupdf_total = sum(pymupdf.values())
    overlap = sum((rust & pymupdf).values())
    if not rust_total and not pymupdf_total:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    precision = overlap / rust_total if rust_total else 0.0
    recall = overlap / pymupdf_total if pymupdf_total else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def unicode_script(character: str) -> str:
    code = ord(character)
    if (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x323AF
    ):
        return "Han"
    if 0x3040 <= code <= 0x309F:
        return "Hiragana"
    if 0x30A0 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return "Katakana"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
        return "Hangul"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
        return "Arabic"
    if 0x0400 <= code <= 0x052F:
        return "Cyrillic"
    if 0x0590 <= code <= 0x05FF:
        return "Hebrew"
    if 0x0900 <= code <= 0x097F:
        return "Devanagari"
    if 0x0E00 <= code <= 0x0E7F:
        return "Thai"
    name = unicodedata.name(character, "")
    if "LATIN" in name:
        return "Latin"
    category = unicodedata.category(character)
    if category[0] in {"P", "S", "N"}:
        return "Common"
    if category[0] == "M":
        return "Inherited"
    return "Other"


def unicode_summary(values: Iterable[str]) -> dict[str, dict[str, int]]:
    categories: collections.Counter[str] = collections.Counter()
    scripts: collections.Counter[str] = collections.Counter()
    for character in values:
        categories[unicodedata.category(character)] += 1
        scripts[unicode_script(character)] += 1
    return {
        "categories": dict(sorted(categories.items())),
        "scripts": dict(sorted(scripts.items())),
    }


def page_text(page: Any) -> str:
    return "\n".join(
        block.text.raw_text
        for block in page.blocks
        if block.text is not None and block.text.raw_text
    )


def warning_codes_by_page(document: Any) -> dict[int, collections.Counter[str]]:
    result: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for raw in document.metadata.get("rust_pdf_warnings", []):
        if not isinstance(raw, dict):
            continue
        code = raw.get("code")
        if not code:
            continue
        if isinstance(raw.get("page_number"), int):
            page_number = int(raw["page_number"])
        elif isinstance(raw.get("page_index"), int):
            page_number = int(raw["page_index"]) + 1
        else:
            page_number = 0
        result[page_number][str(code)] += 1
    return result


def page_worker_record(page: Any, warnings: collections.Counter[str]) -> dict[str, Any]:
    text = page_text(page)
    values = normalized_characters(text)
    roles = collections.Counter(str(block.type.value) for block in page.blocks)
    tagged = sum(
        1
        for block in page.blocks
        if block.metadata.get("tag") is not None
        or any(span.metadata.get("tag") is not None for span in block.spans)
    )
    artifacts = sum(
        1
        for block in page.blocks
        if bool(block.metadata.get("artifact"))
        or any(bool(span.metadata.get("artifact")) for span in block.spans)
    )
    furniture = sum(roles[role] for role in FURNITURE_ROLES)
    return {
        "page_number": int(page.page_number),
        "non_whitespace_length": len(values),
        "blocks": len(page.blocks),
        "spans": sum(len(block.spans) for block in page.blocks),
        "role_counts": dict(sorted(roles.items())),
        "tagged_blocks": tagged,
        "artifact_blocks": artifacts,
        "furniture_blocks": furniture,
        "warning_code_counts": dict(sorted(warnings.items())),
        "unicode": unicode_summary(values),
        "sensitive_comparison_counters": sensitive_counters(text),
    }


def worker(args: argparse.Namespace) -> int:
    if args.worker_source is None or args.worker_output is None:
        raise ValueError("worker requires --worker-source and --worker-output")
    ParseOptions, PyMuPDFAdapter, RustPdfAdapter, _ = load_documa(
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
    warnings = warning_codes_by_page(document)
    result = {
        "provider": args.worker,
        "page_count": len(document.pages),
        "document_sensitive_comparison_counters": sensitive_counters(document_text(document)),
        "pages": [
            page_worker_record(page, warnings.get(int(page.page_number), collections.Counter()))
            for page in document.pages
        ],
    }
    args.worker_output.parent.mkdir(parents=True, exist_ok=True)
    args.worker_output.write_bytes(canonical_bytes(result))
    return 0


def run_worker(
    provider: str,
    pdf_path: Path,
    documa_root: Path,
    wheel_dir: Path,
    temporary_root: Path,
) -> dict[str, Any]:
    output_path = temporary_root / f"{provider}.json"
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
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
    return json.loads(output_path.read_text(encoding="utf-8"))


def as_counter(value: dict[str, int]) -> collections.Counter[str]:
    return collections.Counter({str(key): int(count) for key, count in value.items()})


def signed_delta(
    rust: dict[str, int],
    pymupdf: dict[str, int],
) -> list[dict[str, int | str]]:
    keys = sorted(set(rust) | set(pymupdf))
    return [
        {"kind": key, "delta": int(rust.get(key, 0)) - int(pymupdf.get(key, 0))}
        for key in keys
        if int(rust.get(key, 0)) != int(pymupdf.get(key, 0))
    ]


def reason_candidates(
    rust: dict[str, Any],
    pymupdf: dict[str, Any],
    character: dict[str, float],
    bigram: dict[str, float],
    category_delta: list[dict[str, int | str]],
) -> list[str]:
    reasons: list[str] = []
    length_delta = int(rust["non_whitespace_length"]) - int(pymupdf["non_whitespace_length"])
    if length_delta < 0:
        reasons.append("rust_shorter")
    elif length_delta > 0:
        reasons.append("rust_longer")
    if character["f1"] < 0.995:
        reasons.append("character_mismatch")
    if bigram["f1"] + 0.002 < character["f1"]:
        reasons.append("order_or_adjacency_difference")
    if rust["warning_code_counts"]:
        reasons.append("rust_warning_present")
    if int(rust["tagged_blocks"]) or int(pymupdf["tagged_blocks"]):
        reasons.append("tagged_content_present")
    if (
        int(rust["artifact_blocks"])
        or int(pymupdf["artifact_blocks"])
        or int(rust["furniture_blocks"])
        or int(pymupdf["furniture_blocks"])
    ):
        reasons.append("artifact_or_furniture_policy")
    left_blocks = max(1, int(rust["blocks"]))
    right_blocks = max(1, int(pymupdf["blocks"]))
    left_spans = max(1, int(rust["spans"]))
    right_spans = max(1, int(pymupdf["spans"]))
    if max(left_blocks, right_blocks) / min(left_blocks, right_blocks) >= 1.5:
        reasons.append("block_segmentation_difference")
    if max(left_spans, right_spans) / min(left_spans, right_spans) >= 1.5:
        reasons.append("span_segmentation_difference")
    if category_delta:
        reasons.append("unicode_category_difference")
    return reasons


def public_provider_page(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "non_whitespace_length": int(raw["non_whitespace_length"]),
        "blocks": int(raw["blocks"]),
        "spans": int(raw["spans"]),
        "role_counts": [
            {"role_kind": key, "count": int(value)}
            for key, value in sorted(raw["role_counts"].items())
        ],
        "tagged_blocks": int(raw["tagged_blocks"]),
        "artifact_blocks": int(raw["artifact_blocks"]),
        "furniture_blocks": int(raw["furniture_blocks"]),
    }


def compare_page(
    document_id: str,
    rust: dict[str, Any],
    pymupdf: dict[str, Any],
) -> dict[str, Any]:
    rust_counters = rust["sensitive_comparison_counters"]
    pymupdf_counters = pymupdf["sensitive_comparison_counters"]
    character = counter_metrics(
        as_counter(rust_counters["characters"]),
        as_counter(pymupdf_counters["characters"]),
    )
    bigram = counter_metrics(
        as_counter(rust_counters["character_bigrams"]),
        as_counter(pymupdf_counters["character_bigrams"]),
    )
    category_delta = signed_delta(
        rust["unicode"]["categories"], pymupdf["unicode"]["categories"]
    )
    script_delta = signed_delta(rust["unicode"]["scripts"], pymupdf["unicode"]["scripts"])
    result = {
        "document_id": document_id,
        "page_number": int(rust["page_number"]),
        "rust": public_provider_page(rust),
        "pymupdf": public_provider_page(pymupdf),
        "non_whitespace_length_delta": (
            int(rust["non_whitespace_length"]) - int(pymupdf["non_whitespace_length"])
        ),
        "character_multiset": character,
        "character_bigram": bigram,
        "unicode_category_delta": category_delta,
        "unicode_script_delta": script_delta,
        "rust_warning_code_counts": [
            {"code": key, "count": int(value)}
            for key, value in sorted(rust["warning_code_counts"].items())
        ],
    }
    result["reason_candidates"] = reason_candidates(
        rust, pymupdf, character, bigram, category_delta
    )
    return result


def compare_case(
    case: dict[str, Any],
    rust: dict[str, Any],
    pymupdf: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, collections.Counter[str]]]:
    expected = int(case["pages"])
    if int(rust["page_count"]) != expected or int(pymupdf["page_count"]) != expected:
        raise ValueError(f"page count mismatch for {case['id']}")
    rust_pages = {int(page["page_number"]): page for page in rust["pages"]}
    pymupdf_pages = {int(page["page_number"]): page for page in pymupdf["pages"]}
    expected_numbers = set(range(1, expected + 1))
    if set(rust_pages) != expected_numbers or set(pymupdf_pages) != expected_numbers:
        raise ValueError(f"page alignment mismatch for {case['id']}")
    pages = [
        compare_page(case["id"], rust_pages[number], pymupdf_pages[number])
        for number in range(1, expected + 1)
    ]
    rust_document = rust["document_sensitive_comparison_counters"]
    pymupdf_document = pymupdf["document_sensitive_comparison_counters"]
    sensitive = {
        "rust_characters": as_counter(rust_document["characters"]),
        "rust_bigrams": as_counter(rust_document["character_bigrams"]),
        "pymupdf_characters": as_counter(pymupdf_document["characters"]),
        "pymupdf_bigrams": as_counter(pymupdf_document["character_bigrams"]),
    }
    reason_counts = collections.Counter(
        reason for page in pages for reason in page["reason_candidates"]
    )
    report = {
        "id": case["id"],
        "file_name": case["file_name"],
        "bytes": int(case["bytes"]),
        "sha256": case["sha256"],
        "expected_pages": expected,
        "quality_rust_vs_pymupdf": {
            "non_whitespace_character": counter_metrics(
                sensitive["rust_characters"], sensitive["pymupdf_characters"]
            ),
            "character_bigram": counter_metrics(
                sensitive["rust_bigrams"], sensitive["pymupdf_bigrams"]
            ),
        },
        "reason_candidate_counts": [
            {"reason": key, "pages": value} for key, value in sorted(reason_counts.items())
        ],
        "pages": pages,
    }
    return report, sensitive


def find_forbidden_key(value: Any, location: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_REPORT_KEYS:
                return f"{location}.{key}"
            found = find_forbidden_key(child, f"{location}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = find_forbidden_key(child, f"{location}[{index}]")
            if found:
                return found
    return None


def assert_privacy_safe(report: dict[str, Any], private_roots: Iterable[Path] = ()) -> None:
    forbidden = find_forbidden_key(report)
    if forbidden is not None:
        raise ValueError(f"privacy-forbidden report key: {forbidden}")
    if report.get("contains_extracted_content") is not False:
        raise ValueError("contains_extracted_content must be false")
    if report.get("contains_character_keys") is not False:
        raise ValueError("contains_character_keys must be false")
    if report.get("private_ir_written") is not False:
        raise ValueError("private_ir_written must be false")
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for root in private_roots:
        if str(root.resolve()).lower() in encoded.lower():
            raise ValueError("private source path leaked into report")
    if "://" in encoded:
        raise ValueError("URL-like value leaked into report")


def reference_metrics(path: Path) -> tuple[dict[str, tuple[float, float]], tuple[float, float]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    cases = {
        str(case["id"]): (
            float(case["quality_rust_vs_pymupdf"]["non_whitespace_character_f1"]),
            float(case["quality_rust_vs_pymupdf"]["character_bigram_f1"]),
        )
        for case in value["cases"]
    }
    quality = value["summary"]["quality_rust_vs_pymupdf"]
    return cases, (
        float(quality["non_whitespace_character_f1"]),
        float(quality["character_bigram_f1"]),
    )


def validate_reference(
    cases: list[dict[str, Any]],
    summary: dict[str, Any],
    path: Path,
    epsilon: float = 1e-12,
) -> dict[str, Any]:
    expected_cases, expected_global = reference_metrics(path)
    mismatches: list[str] = []
    for case in cases:
        actual = case["quality_rust_vs_pymupdf"]
        pair = (
            float(actual["non_whitespace_character"]["f1"]),
            float(actual["character_bigram"]["f1"]),
        )
        expected = expected_cases.get(str(case["id"]))
        if expected is None or any(abs(left - right) > epsilon for left, right in zip(pair, expected)):
            mismatches.append(str(case["id"]))
    quality = summary["quality_rust_vs_pymupdf"]
    actual_global = (
        float(quality["non_whitespace_character"]["f1"]),
        float(quality["character_bigram"]["f1"]),
    )
    global_match = all(
        abs(left - right) <= epsilon for left, right in zip(actual_global, expected_global)
    )
    return {
        "reference_report_sha256": digest_file(path),
        "epsilon": epsilon,
        "per_case_match": not mismatches,
        "mismatched_case_ids": mismatches,
        "global_match": global_match,
    }


def self_test() -> None:
    identical = counter_metrics(collections.Counter("abc"), collections.Counter("abc"))
    assert identical == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    different = counter_metrics(collections.Counter("ab"), collections.Counter("abc"))
    assert 0.0 < different["f1"] < 1.0
    assert unicode_script("中") == "Han"
    assert unicode_script("A") == "Latin"
    rust = {
        "page_number": 1,
        "non_whitespace_length": 2,
        "blocks": 1,
        "spans": 1,
        "role_counts": {"paragraph": 1},
        "tagged_blocks": 0,
        "artifact_blocks": 0,
        "furniture_blocks": 0,
        "warning_code_counts": {"missing_mapping": 1},
        "unicode": unicode_summary("ab"),
        "sensitive_comparison_counters": sensitive_counters("ab"),
    }
    pymupdf = {
        **rust,
        "non_whitespace_length": 3,
        "warning_code_counts": {},
        "unicode": unicode_summary("abc"),
        "sensitive_comparison_counters": sensitive_counters("abc"),
    }
    public = compare_page("synthetic", rust, pymupdf)
    report = {
        "contains_extracted_content": False,
        "contains_character_keys": False,
        "private_ir_written": False,
        "cases": [{"pages": [public]}],
    }
    assert public["non_whitespace_length_delta"] == -1
    assert "rust_shorter" in public["reason_candidates"]
    assert "rust_warning_present" in public["reason_candidates"]
    assert_privacy_safe(report)
    bad = {**report, "text": "forbidden"}
    try:
        assert_privacy_safe(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("privacy denylist did not reject extracted text")
    with tempfile.TemporaryDirectory(prefix="stage12-stage7a-self-test-") as temporary:
        marker = Path(temporary) / "sensitive.json"
        marker.write_text("temporary", encoding="utf-8")
    assert not marker.exists()
    print("stage12 Stage 7.1 page quality self-test: ok")


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
    if not args.reference_report.is_file():
        raise FileNotFoundError(f"Stage 6D reference report not found: {args.reference_report}")

    contract = load_contract(args.contract)
    cases = [(entry, verify_case(args.corpus_dir, entry)) for entry in contract["documents"]]
    report: dict[str, Any] = {
        "schema_version": 1,
        "stage": "stage-7.1",
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
        "cases": [],
    }
    aggregate = {
        "rust_characters": collections.Counter(),
        "rust_bigrams": collections.Counter(),
        "pymupdf_characters": collections.Counter(),
        "pymupdf_bigrams": collections.Counter(),
    }
    with tempfile.TemporaryDirectory(prefix="stage12-stage7a-private-") as temporary:
        temporary_root = Path(temporary)
        for index, (case, pdf_path) in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['file_name']}", flush=True)
            provider_results = {
                provider: run_worker(
                    provider,
                    pdf_path,
                    args.documa_root,
                    args.rust_wheel_dir,
                    temporary_root / str(case["id"]),
                )
                for provider in PROVIDERS
            }
            case_report, sensitive = compare_case(
                case, provider_results["rust"], provider_results["pymupdf"]
            )
            report["cases"].append(case_report)
            for key, value in sensitive.items():
                aggregate[key].update(value)
    report["temporary_counter_files_removed"] = not temporary_root.exists()

    all_pages = [page for case in report["cases"] for page in case["pages"]]
    reason_counts = collections.Counter(
        reason for page in all_pages for reason in page["reason_candidates"]
    )
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
    summary = {
        "documents": len(report["cases"]),
        "pages": len(all_pages),
        "quality_rust_vs_pymupdf": {
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
        "reason_candidate_counts": [
            {"reason": key, "pages": value} for key, value in sorted(reason_counts.items())
        ],
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
    summary["stage6d_reproduction"] = validate_reference(
        report["cases"], summary, args.reference_report
    )
    if not (
        summary["stage6d_reproduction"]["per_case_match"]
        and summary["stage6d_reproduction"]["global_match"]
    ):
        raise ValueError("Stage 7.1 metrics do not reproduce the Stage 6D reference")
    report["summary"] = summary
    assert_privacy_safe(report, [args.corpus_dir, args.documa_root, args.rust_wheel_dir])
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
