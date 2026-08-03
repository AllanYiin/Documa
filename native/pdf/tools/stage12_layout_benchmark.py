#!/usr/bin/env python3
"""Benchmark and audit Stage 1B Layout IR without persisting private content."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_BASELINE = ROOT / "target" / "stage12-baseline" / "report.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-layout-benchmark"
COORDINATE_SPACE = "layout_unrotated_top_left"


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("unsupported corpus contract schema")
    measurement = value.get("measurement", {})
    if measurement.get("save_private_ir_by_default") is not False:
        raise ValueError("private IR must remain disabled by default")
    return value


def verify_case(corpus_dir: Path, case: dict[str, Any]) -> Path:
    path = corpus_dir / case["file_name"]
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size != case["bytes"]:
        raise ValueError(f"byte length mismatch for {path.name}")
    actual_sha = digest_file(path)
    if actual_sha != case["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path.name}: {actual_sha}")
    return path


def require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def audit_bbox(value: Any, field: str) -> None:
    box = require_dict(value, field)
    x0 = finite_number(box.get("x0"), f"{field}.x0")
    y0 = finite_number(box.get("y0"), f"{field}.y0")
    x1 = finite_number(box.get("x1"), f"{field}.x1")
    y1 = finite_number(box.get("y1"), f"{field}.y1")
    if x0 > x1 or y0 > y1:
        raise ValueError(f"{field} is inverted")


def audit_confidence_and_rule(value: dict[str, Any], field: str) -> None:
    confidence = finite_number(value.get("confidence"), f"{field}.confidence")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"{field}.confidence is outside [0, 1]")
    if not isinstance(value.get("rule_id"), str) or not value["rule_id"]:
        raise ValueError(f"{field}.rule_id must be non-empty")


def audit_provenance(value: Any, field: str) -> None:
    provenance = require_dict(value, field)
    if not isinstance(provenance.get("page_object"), dict):
        raise ValueError(f"{field}.page_object must be an object id")
    start = provenance.get("source_ordinal_start")
    end = provenance.get("source_ordinal_end")
    if not isinstance(start, int) or not isinstance(end, int) or start > end:
        raise ValueError(f"{field} has an invalid source ordinal range")
    require_list(provenance.get("mcids"), f"{field}.mcids")
    require_list(provenance.get("text_origins"), f"{field}.text_origins")


def audit_layout(value: Any, expected_pages: int) -> dict[str, Any]:
    root = require_dict(value, "layout")
    if root.get("schema_version") != 1:
        raise ValueError("Layout IR schema_version must be 1")
    if root.get("coordinate_space") != COORDINATE_SPACE:
        raise ValueError("Layout IR root coordinate space mismatch")
    if "timings" in root:
        raise ValueError("default Layout IR must omit timings")
    options = require_dict(root.get("options"), "layout.options")
    if options.get("include_debug_glyphs") is not False or options.get("include_timings") is not False:
        raise ValueError("benchmark requires deterministic default debug/timing options")
    digest = root.get("options_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("options_digest must be a SHA-256 string")

    capabilities = require_dict(root.get("capabilities"), "layout.capabilities")
    required_capabilities = {
        "source_order": True,
        "tagged_order": False,
        "inferred_order": False,
        "main_flow": False,
        "text_blocks": True,
        "semantic_roles": False,
        "tables": False,
        "image_placements": False,
    }
    if capabilities != required_capabilities:
        raise ValueError("Stage 1B capability set mismatch")

    pages = require_list(root.get("pages"), "layout.pages")
    if len(pages) != expected_pages:
        raise ValueError(f"page count mismatch: {len(pages)} != {expected_pages}")

    counts = {
        "pages": len(pages),
        "nodes": 0,
        "spans": 0,
        "tables": 0,
        "image_placements": 0,
        "warnings": len(require_list(root.get("warnings"), "layout.warnings")),
        "bboxes": 0,
    }
    document_node_ids: set[str] = set()
    document_span_ids: set[str] = set()
    for expected_index, page_value in enumerate(pages):
        page = require_dict(page_value, f"layout.pages[{expected_index}]")
        if page.get("page_index") != expected_index or page.get("page_number") != expected_index + 1:
            raise ValueError(f"page numbering mismatch at page {expected_index}")
        if page.get("coordinate_space") != COORDINATE_SPACE:
            raise ValueError(f"page coordinate space mismatch at page {expected_index}")
        geometry = require_dict(page.get("geometry"), f"page[{expected_index}].geometry")
        if geometry.get("coordinate_space") != COORDINATE_SPACE:
            raise ValueError(f"geometry coordinate space mismatch at page {expected_index}")
        audit_bbox(geometry.get("layout_bounds"), f"page[{expected_index}].layout_bounds")
        counts["bboxes"] += 1
        if "debug_glyphs" in page:
            raise ValueError("default Layout IR must omit debug glyphs")

        nodes = require_list(page.get("semantic_nodes"), f"page[{expected_index}].semantic_nodes")
        tables = require_list(page.get("tables"), f"page[{expected_index}].tables")
        images = require_list(page.get("image_placements"), f"page[{expected_index}].image_placements")
        if tables or images:
            raise ValueError("Stage 1B table and image arrays must remain empty")
        counts["nodes"] += len(nodes)
        counts["tables"] += len(tables)
        counts["image_placements"] += len(images)

        page_node_ids: list[str] = []
        for node_index, node_value in enumerate(nodes):
            field = f"page[{expected_index}].node[{node_index}]"
            node = require_dict(node_value, field)
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError(f"{field}.id must be non-empty")
            if node_id in document_node_ids:
                raise ValueError(f"duplicate node id: {node_id}")
            document_node_ids.add(node_id)
            page_node_ids.append(node_id)
            if node.get("kind") != "text_block" or node.get("role") != "unclassified":
                raise ValueError(f"{field} has unexpected Stage 1B semantics")
            audit_bbox(node.get("bbox"), f"{field}.bbox")
            audit_confidence_and_rule(node, field)
            audit_provenance(node.get("provenance"), f"{field}.provenance")
            counts["bboxes"] += 1

            spans = require_list(node.get("spans"), f"{field}.spans")
            counts["spans"] += len(spans)
            for span_index, span_value in enumerate(spans):
                span_field = f"{field}.span[{span_index}]"
                span = require_dict(span_value, span_field)
                span_id = span.get("id")
                if not isinstance(span_id, str) or not span_id:
                    raise ValueError(f"{span_field}.id must be non-empty")
                if span_id in document_span_ids:
                    raise ValueError(f"duplicate span id: {span_id}")
                document_span_ids.add(span_id)
                audit_bbox(span.get("bbox"), f"{span_field}.bbox")
                audit_confidence_and_rule(span, span_field)
                audit_provenance(span.get("provenance"), f"{span_field}.provenance")
                counts["bboxes"] += 1

        orders = require_dict(page.get("orders"), f"page[{expected_index}].orders")
        source_order = require_list(orders.get("source_order"), "orders.source_order")
        if source_order != page_node_ids:
            raise ValueError(f"source_order mismatch at page {expected_index}")
        for name in ("tagged_order", "inferred_order", "main_flow"):
            if require_list(orders.get(name), f"orders.{name}"):
                raise ValueError(f"{name} must be empty in Stage 1B")

    return counts


def run_layout(rust_cli: Path, pdf_path: Path) -> tuple[bytes, float]:
    started = time.perf_counter()
    completed = subprocess.run(
        [str(rust_cli), "layout", str(pdf_path), "--json"],
        check=True,
        capture_output=True,
    )
    elapsed = time.perf_counter() - started
    if completed.stderr:
        raise ValueError(f"unexpected CLI stderr for {pdf_path.name}")
    return completed.stdout, elapsed


def load_frozen_documa_baseline(path: Path, contract_sha: str, pages: int) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Stage 0 baseline report not found: {path}")
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if baseline.get("contract_sha256") != contract_sha:
        raise ValueError("Stage 0 baseline contract SHA mismatch")
    summary = require_dict(baseline.get("summary"), "baseline.summary")
    if summary.get("pages") != pages:
        raise ValueError("Stage 0 baseline page count mismatch")
    documa = require_dict(summary.get("documa"), "baseline.summary.documa")
    seconds = finite_number(
        documa.get("sum_of_document_medians_seconds"),
        "baseline.summary.documa.sum_of_document_medians_seconds",
    )
    return {
        "report_sha256": digest_file(path),
        "sum_of_document_medians_seconds": seconds,
        "pages_per_second": finite_number(
            documa.get("pages_per_second"),
            "baseline.summary.documa.pages_per_second",
        ),
    }


def assert_privacy_safe(report: dict[str, Any]) -> None:
    if report.get("contains_extracted_content") is not False:
        raise ValueError("privacy marker must be false")
    forbidden_keys = {"text", "raw_text", "semantic_nodes", "debug_glyphs"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            overlap = forbidden_keys.intersection(value)
            if overlap:
                raise ValueError(f"private-content field leaked into report: {sorted(overlap)}")
            if "spans" in value and not isinstance(value["spans"], int):
                raise ValueError("private span structures leaked into report")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(report)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target" / "release" / "rust-pdf.exe")
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def self_test() -> None:
    sample = {
        "schema_version": 1,
        "coordinate_space": COORDINATE_SPACE,
        "options": {"include_debug_glyphs": False, "include_timings": False},
        "options_digest": "0" * 64,
        "capabilities": {
            "source_order": True,
            "tagged_order": False,
            "inferred_order": False,
            "main_flow": False,
            "text_blocks": True,
            "semantic_roles": False,
            "tables": False,
            "image_placements": False,
        },
        "pages": [{
            "page_index": 0,
            "page_number": 1,
            "coordinate_space": COORDINATE_SPACE,
            "geometry": {
                "coordinate_space": COORDINATE_SPACE,
                "layout_bounds": {"x0": 0, "y0": 0, "x1": 100, "y1": 200},
            },
            "semantic_nodes": [],
            "tables": [],
            "image_placements": [],
            "orders": {"source_order": [], "tagged_order": [], "inferred_order": [], "main_flow": []},
        }],
        "warnings": [],
    }
    assert audit_layout(sample, 1)["pages"] == 1
    report = {"contains_extracted_content": False, "cases": [{"counts": {"pages": 1, "spans": 0}}]}
    assert_privacy_safe(report)
    assert digest_bytes(canonical_bytes({"b": 2, "a": 1})) == digest_bytes(
        canonical_bytes({"a": 1, "b": 2})
    )
    print("stage12 layout benchmark self-test: ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.corpus_dir is None:
        raise SystemExit("set --corpus-dir or RUST_PDF_STAGE12_CORPUS_DIR")
    if not args.rust_cli.is_file():
        raise FileNotFoundError(f"Rust release CLI not found: {args.rust_cli}")

    contract = load_contract(args.contract)
    measurement = contract["measurement"]
    warmups = int(measurement["warmup_runs"])
    runs = int(args.runs if args.runs is not None else measurement["measured_runs"])
    if warmups < 0 or runs < 1:
        raise ValueError("warmups must be non-negative and runs must be positive")
    contract_sha = digest_file(args.contract)
    cases = [(case, verify_case(args.corpus_dir, case)) for case in contract["documents"]]
    total_pages = sum(int(case["pages"]) for case, _ in cases)
    frozen_documa = load_frozen_documa_baseline(args.baseline_report, contract_sha, total_pages)

    report: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "stage1b_layout_ir",
        "private_corpus": True,
        "contains_extracted_content": False,
        "private_ir_written": False,
        "contract_sha256": contract_sha,
        "coordinate_space": COORDINATE_SPACE,
        "timing_scope": "release_cli_startup_plus_parse_layout_serialize_and_stdout_capture",
        "timing_excludes": ["json_decode", "schema_audit", "hashing", "report_write"],
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "measurement": {"warmup_runs": warmups, "measured_runs": runs},
        "frozen_stage0_documa": frozen_documa,
        "cases": [],
    }

    for case_index, (case, path) in enumerate(cases, start=1):
        label = f"[{case_index}/{len(cases)}] {case['file_name']}"
        for warmup_index in range(warmups):
            print(f"{label} warmup {warmup_index + 1}/{warmups}", flush=True)
            run_layout(args.rust_cli, path)

        seconds: list[float] = []
        raw_hashes: list[str] = []
        canonical_hash: str | None = None
        output_sizes: list[int] = []
        counts: dict[str, Any] | None = None
        for run_index in range(runs):
            print(f"{label} measured {run_index + 1}/{runs}", flush=True)
            output, elapsed = run_layout(args.rust_cli, path)
            seconds.append(elapsed)
            output_sizes.append(len(output))
            raw_hashes.append(digest_bytes(output))
            if counts is None:
                parsed = json.loads(output.decode("utf-8", errors="strict"))
                counts = audit_layout(parsed, int(case["pages"]))
                canonical_hash = digest_bytes(canonical_bytes(parsed))
                del parsed
            del output

        deterministic = len(set(raw_hashes)) == 1 and len(set(output_sizes)) == 1
        if not deterministic:
            raise ValueError(f"non-deterministic Layout IR for {path.name}")
        assert counts is not None
        assert canonical_hash is not None
        report["cases"].append({
            "id": case["id"],
            "file_name": case["file_name"],
            "bytes": case["bytes"],
            "sha256": case["sha256"],
            "expected_pages": case["pages"],
            "timing": {
                "seconds": seconds,
                "median_seconds": statistics.median(seconds),
                "min_seconds": min(seconds),
                "max_seconds": max(seconds),
            },
            "output": {
                "bytes_per_run": output_sizes,
                "sha256_per_run": raw_hashes,
                "canonical_sha256": canonical_hash,
                "deterministic": deterministic,
            },
            "counts": counts,
            "schema_audit_passed": True,
            "privacy_audit_passed": True,
        })

    layout_seconds = sum(case["timing"]["median_seconds"] for case in report["cases"])
    total_output_bytes = sum(case["output"]["bytes_per_run"][0] for case in report["cases"])
    total_counts = {
        key: sum(case["counts"][key] for case in report["cases"])
        for key in ("nodes", "spans", "tables", "image_placements", "warnings", "bboxes")
    }
    report["summary"] = {
        "documents": len(report["cases"]),
        "pages": total_pages,
        "sum_of_document_medians_seconds": layout_seconds,
        "pages_per_second": total_pages / layout_seconds if layout_seconds else None,
        "speedup_vs_frozen_stage0_documa": (
            frozen_documa["sum_of_document_medians_seconds"] / layout_seconds
            if layout_seconds else None
        ),
        "comparison_note": "non_simultaneous_comparison_to_frozen_stage0_baseline",
        "total_serialized_bytes_one_run_per_document": total_output_bytes,
        "all_deterministic": all(case["output"]["deterministic"] for case in report["cases"]),
        "all_schema_audits_passed": all(case["schema_audit_passed"] for case in report["cases"]),
        "all_privacy_audits_passed": all(case["privacy_audit_passed"] for case in report["cases"]),
        "counts": total_counts,
    }
    assert_privacy_safe(report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"report: {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())