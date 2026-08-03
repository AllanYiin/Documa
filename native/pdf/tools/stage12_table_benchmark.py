#!/usr/bin/env python3
"""Benchmark and audit Stage 4 table Layout IR without persisting private content."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

import psutil
from typing import Any

from stage12_layout_benchmark import (
    COORDINATE_SPACE,
    DEFAULT_BASELINE,
    DEFAULT_CONTRACT,
    ROOT,
    assert_privacy_safe,
    audit_bbox,
    audit_confidence_and_rule,
    audit_provenance,
    canonical_bytes,
    digest_bytes,
    digest_file,
    load_contract,
    load_frozen_documa_baseline,
    require_dict,
    require_list,
    run_layout,
    verify_case,
)

DEFAULT_OUTPUT = ROOT / "target" / "stage12-table-benchmark"
ROLES = {
    "unclassified", "document", "part", "section", "heading", "paragraph",
    "list", "list_item", "label", "list_body", "table", "table_row",
    "table_header", "table_cell", "figure", "formula", "form", "header",
    "footer", "page_number", "artifact",
}
EXCLUDED_MAIN_FLOW_ROLES = {"header", "footer", "page_number", "artifact"}
TABLE_EVIDENCE = {"tagged", "vector_lattice", "text_alignment", "fused"}
CELL_ROLES = {"data", "row_header", "column_header", "both_header"}
STAGE3_PAGES_PER_SECOND = 208.084305


def audit_optional_bbox(value: Any, field: str) -> int:
    if value is None:
        return 0
    audit_bbox(value, field)
    return 1


def audit_optional_provenance(value: Any, field: str) -> None:
    if value is not None:
        audit_provenance(value, field)


def audit_table(
    value: Any,
    field: str,
    node_ids: set[str],
    seen_table_ids: set[str],
    seen_cell_ids: set[str],
) -> dict[str, Any]:
    table = require_dict(value, field)
    table_id = table.get("id")
    if not isinstance(table_id, str) or not table_id or table_id in seen_table_ids:
        raise ValueError(f"{field}.id must be unique and non-empty")
    seen_table_ids.add(table_id)
    rows, columns = table.get("rows"), table.get("columns")
    if not isinstance(rows, int) or not isinstance(columns, int) or rows < 1 or columns < 1:
        raise ValueError(f"{field} dimensions must be positive integers")
    evidence = table.get("evidence")
    if evidence not in TABLE_EVIDENCE:
        raise ValueError(f"{field}.evidence is unknown: {evidence!r}")
    audit_confidence_and_rule(table, field)
    bbox_count = audit_optional_bbox(table.get("bbox"), f"{field}.bbox")
    audit_optional_provenance(table.get("provenance"), f"{field}.provenance")
    links = ordered_ids(table.get("source_node_ids"), f"{field}.source_node_ids", node_ids)
    cells = require_list(table.get("cells"), f"{field}.cells")
    header_cells = 0
    spanned_cells = 0
    cell_bboxes = 0
    cell_links = 0
    for cell_index, cell_value in enumerate(cells):
        cell_field = f"{field}.cell[{cell_index}]"
        cell = require_dict(cell_value, cell_field)
        cell_id = cell.get("id")
        if not isinstance(cell_id, str) or not cell_id or cell_id in seen_cell_ids:
            raise ValueError(f"{cell_field}.id must be unique and non-empty")
        seen_cell_ids.add(cell_id)
        row, column = cell.get("row"), cell.get("column")
        row_span, column_span = cell.get("row_span"), cell.get("column_span")
        if not all(isinstance(item, int) for item in (row, column, row_span, column_span)):
            raise ValueError(f"{cell_field} coordinates and spans must be integers")
        if row < 0 or column < 0 or row_span < 1 or column_span < 1:
            raise ValueError(f"{cell_field} has invalid coordinates or spans")
        if row + row_span > rows or column + column_span > columns:
            raise ValueError(f"{cell_field} exceeds table dimensions")
        if cell.get("role") not in CELL_ROLES:
            raise ValueError(f"{cell_field}.role is unknown")
        if not isinstance(cell.get("text"), str):
            raise ValueError(f"{cell_field}.text must be a string")
        audit_confidence_and_rule(cell, cell_field)
        audit_optional_provenance(cell.get("provenance"), f"{cell_field}.provenance")
        cell_bboxes += audit_optional_bbox(cell.get("bbox"), f"{cell_field}.bbox")
        cell_links += len(ordered_ids(
            cell.get("source_node_ids"), f"{cell_field}.source_node_ids", node_ids
        ))
        header_cells += int(cell["role"] != "data")
        spanned_cells += int(row_span > 1 or column_span > 1)
    return {
        "table_cells": len(cells), "table_source_links": len(links),
        "cell_source_links": cell_links, "header_cells": header_cells,
        "spanned_cells": spanned_cells, "table_bboxes": bbox_count,
        "cell_bboxes": cell_bboxes, "evidence": evidence,
    }


def run_layout_measured(rust_cli: Path, pdf_path: Path) -> tuple[bytes, float, int]:
    with tempfile.TemporaryFile() as output_file:
        started = time.perf_counter()
        process = subprocess.Popen(
            [str(rust_cli), "layout", str(pdf_path), "--json"],
            stdout=output_file,
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
            raise RuntimeError(
                f"Rust layout failed for {pdf_path}: {stderr.decode('utf-8', errors='replace')}"
            )
        output_file.seek(0)
        return output_file.read(), elapsed, peak_rss


def ordered_ids(value: Any, field: str, node_ids: set[str]) -> list[str]:
    result = require_list(value, field)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicate node ids")
    if any(not isinstance(node_id, str) or node_id not in node_ids for node_id in result):
        raise ValueError(f"{field} references an unknown node")
    return result


def pairwise_proxy(tagged: list[str], inferred: list[str]) -> tuple[int, int]:
    inferred_position = {node_id: index for index, node_id in enumerate(inferred)}
    common = [node_id for node_id in tagged if node_id in inferred_position]
    correct = 0
    total = 0
    for left_index, left in enumerate(common):
        for right in common[left_index + 1:]:
            total += 1
            correct += int(inferred_position[left] < inferred_position[right])
    return correct, total


def audit_stage4_layout(value: Any, expected_pages: int) -> dict[str, Any]:
    root = require_dict(value, "layout")
    if root.get("schema_version") != 1:
        raise ValueError("Layout IR schema_version must remain 1")
    if root.get("coordinate_space") != COORDINATE_SPACE:
        raise ValueError("Layout IR root coordinate space mismatch")
    if "timings" in root:
        raise ValueError("default Layout IR must omit timings")
    options = require_dict(root.get("options"), "layout.options")
    if options.get("include_debug_glyphs") is not False or options.get("include_timings") is not False:
        raise ValueError("benchmark requires deterministic default debug/timing options")
    options_digest = root.get("options_digest")
    if not isinstance(options_digest, str) or len(options_digest) != 64:
        raise ValueError("options_digest must be a SHA-256 string")

    capabilities = require_dict(root.get("capabilities"), "layout.capabilities")
    for name, expected in {
        "source_order": True,
        "inferred_order": True,
        "main_flow": True,
        "text_blocks": True,
        "tables": True,
        "image_placements": False,
    }.items():
        if capabilities.get(name) is not expected:
            raise ValueError(f"Stage 4 capability mismatch: {name}")
    for name in ("tagged_order", "semantic_roles"):
        if not isinstance(capabilities.get(name), bool):
            raise ValueError(f"Stage 4 capability must be boolean: {name}")

    pages = require_list(root.get("pages"), "layout.pages")
    if len(pages) != expected_pages:
        raise ValueError(f"page count mismatch: {len(pages)} != {expected_pages}")
    warnings = require_list(root.get("warnings"), "layout.warnings")
    warning_codes: Counter[str] = Counter()
    for index, warning_value in enumerate(warnings):
        warning = require_dict(warning_value, f"layout.warnings[{index}]")
        code = warning.get("code")
        if not isinstance(code, str) or not code:
            raise ValueError("warning code must be non-empty")
        warning_codes[code] += 1

    counts: dict[str, Any] = {
        "pages": len(pages), "nodes": 0, "spans": 0, "main_flow_nodes": 0,
        "tagged_pages": 0, "tagged_nodes": 0, "multi_column_proxy_pages": 0,
        "artifacts": 0, "tables": 0, "table_cells": 0,
        "table_source_links": 0, "cell_source_links": 0, "header_cells": 0,
        "spanned_cells": 0, "table_bboxes": 0, "cell_bboxes": 0,
        "image_placements": 0, "warnings": len(warnings), "bboxes": 0,
        "tagged_pairwise_correct": 0, "tagged_pairwise_total": 0,
        "source_pairwise_correct": 0, "visual_yx_pairwise_correct": 0,
        "visual_xy_pairwise_correct": 0,
        "role_counts": {}, "table_evidence_counts": {},
        "warning_code_counts": dict(sorted(warning_codes.items())),
    }
    role_counts: Counter[str] = Counter()
    document_node_ids: set[str] = set()
    document_span_ids: set[str] = set()
    document_table_ids: set[str] = set()
    document_cell_ids: set[str] = set()
    table_evidence_counts: Counter[str] = Counter()
    any_tagged_order = False
    any_semantic_role = False

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
        if images:
            raise ValueError("Stage 4 image array must remain empty")
        counts["nodes"] += len(nodes)
        counts["tables"] += len(tables)
        counts["image_placements"] += len(images)

        page_node_ids: list[str] = []
        node_by_id: dict[str, dict[str, Any]] = {}
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
            node_by_id[node_id] = node
            if node.get("kind") != "text_block":
                raise ValueError(f"{field}.kind must remain text_block")
            role = node.get("role")
            if role not in ROLES:
                raise ValueError(f"{field}.role is unknown: {role!r}")
            role_counts[role] += 1
            any_semantic_role = any_semantic_role or role != "unclassified"
            artifact = node.get("artifact", False)
            if not isinstance(artifact, bool):
                raise ValueError(f"{field}.artifact must be boolean")
            counts["artifacts"] += int(artifact)
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

        node_ids = set(page_node_ids)
        for table_index, table_value in enumerate(tables):
            table_counts = audit_table(
                table_value,
                f"page[{expected_index}].table[{table_index}]",
                node_ids,
                document_table_ids,
                document_cell_ids,
            )
            table_evidence_counts[table_counts.pop("evidence")] += 1
            for name, amount in table_counts.items():
                counts[name] += amount

        orders = require_dict(page.get("orders"), f"page[{expected_index}].orders")
        source = ordered_ids(orders.get("source_order"), "orders.source_order", node_ids)
        tagged = ordered_ids(orders.get("tagged_order"), "orders.tagged_order", node_ids)
        inferred = ordered_ids(orders.get("inferred_order"), "orders.inferred_order", node_ids)
        main_flow = ordered_ids(orders.get("main_flow"), "orders.main_flow", node_ids)
        if set(source) != node_ids or set(inferred) != node_ids:
            raise ValueError(f"source/inferred order must be complete at page {expected_index}")
        if inferred != page_node_ids:
            raise ValueError(f"node array must follow inferred order at page {expected_index}")
        expected_main = [
            node_id for node_id in inferred
            if not node_by_id[node_id].get("artifact", False)
            and node_by_id[node_id].get("role") not in EXCLUDED_MAIN_FLOW_ROLES
        ]
        if main_flow != expected_main:
            raise ValueError(f"main_flow exclusion mismatch at page {expected_index}")
        counts["main_flow_nodes"] += len(main_flow)
        if tagged:
            counts["tagged_pages"] += 1
            counts["tagged_nodes"] += len(tagged)
            any_tagged_order = True
        correct, total = pairwise_proxy(tagged, inferred)
        counts["tagged_pairwise_correct"] += correct
        counts["tagged_pairwise_total"] += total
        visual_yx = sorted(
            inferred,
            key=lambda node_id: (
                node_by_id[node_id]["bbox"]["y0"],
                node_by_id[node_id]["bbox"]["x0"],
                node_by_id[node_id]["provenance"]["source_ordinal_start"],
            ),
        )
        visual_xy = sorted(
            inferred,
            key=lambda node_id: (
                node_by_id[node_id]["bbox"]["x0"],
                node_by_id[node_id]["bbox"]["y0"],
                node_by_id[node_id]["provenance"]["source_ordinal_start"],
            ),
        )
        counts["source_pairwise_correct"] += pairwise_proxy(tagged, source)[0]
        counts["visual_yx_pairwise_correct"] += pairwise_proxy(tagged, visual_yx)[0]
        counts["visual_xy_pairwise_correct"] += pairwise_proxy(tagged, visual_xy)[0]

        boxes = [require_dict(node_by_id[node_id].get("bbox"), "node.bbox") for node_id in inferred]
        if any(
            boxes[index]["y0"] + 2.0 < boxes[index - 1]["y0"]
            and abs(boxes[index]["x0"] - boxes[index - 1]["x0"]) > 12.0
            for index in range(1, len(boxes))
        ):
            counts["multi_column_proxy_pages"] += 1

    counts["role_counts"] = dict(sorted(role_counts.items()))
    counts["table_evidence_counts"] = dict(sorted(table_evidence_counts.items()))
    if capabilities["tagged_order"] is not any_tagged_order:
        raise ValueError("tagged_order capability does not match page orders")
    if capabilities["semantic_roles"] is not any_semantic_role:
        raise ValueError("semantic_roles capability does not match node roles")
    pair_total = counts["tagged_pairwise_total"]
    counts["tagged_pairwise_accuracy"] = (
        counts["tagged_pairwise_correct"] / pair_total if pair_total else None
    )
    for strategy in ("source", "visual_yx", "visual_xy"):
        counts[f"{strategy}_pairwise_accuracy"] = (
            counts[f"{strategy}_pairwise_correct"] / pair_total if pair_total else None
        )
    counts["main_flow_coverage"] = (
        counts["main_flow_nodes"] / counts["nodes"] if counts["nodes"] else None
    )
    return counts


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
        "schema_version": 1, "coordinate_space": COORDINATE_SPACE,
        "options": {"include_debug_glyphs": False, "include_timings": False},
        "options_digest": "0" * 64,
        "capabilities": {
            "source_order": True, "tagged_order": True, "inferred_order": True,
            "main_flow": True, "text_blocks": True, "semantic_roles": True,
            "tables": True, "image_placements": False,
        },
        "pages": [{
            "page_index": 0, "page_number": 1, "coordinate_space": COORDINATE_SPACE,
            "geometry": {"coordinate_space": COORDINATE_SPACE,
                         "layout_bounds": {"x0": 0, "y0": 0, "x1": 100, "y1": 200}},
            "semantic_nodes": [{
                "id": "p0-n0", "kind": "text_block", "role": "paragraph", "text": "sample",
                "bbox": {"x0": 0, "y0": 20, "x1": 10, "y1": 30},
                "confidence": 0.8, "rule_id": "stage3_paragraph_geometry_v1",
                "provenance": {"page_object": {"number": 3, "generation": 0},
                               "source_ordinal_start": 0, "source_ordinal_end": 0,
                               "mcids": [0], "text_origins": ["to_unicode"]},
                "spans": [],
            }],
            "tables": [{
                "id": "p0-t0", "bbox": {"x0": 0, "y0": 20, "x1": 10, "y1": 30},
                "rows": 1, "columns": 1, "evidence": "tagged",
                "source_node_ids": ["p0-n0"], "confidence": 0.98,
                "rule_id": "stage4a_tagged_table_v1",
                "provenance": {"page_object": {"number": 3, "generation": 0},
                               "source_ordinal_start": 0, "source_ordinal_end": 0,
                               "mcids": [0], "text_origins": ["to_unicode"]},
                "cells": [{
                    "id": "p0-t0-c0", "row": 0, "column": 0,
                    "row_span": 1, "column_span": 1, "role": "data", "text": "sample",
                    "bbox": {"x0": 0, "y0": 20, "x1": 10, "y1": 30},
                    "source_node_ids": ["p0-n0"], "confidence": 0.98,
                    "rule_id": "stage4a_tagged_cell_v1",
                    "provenance": {"page_object": {"number": 3, "generation": 0},
                                   "source_ordinal_start": 0, "source_ordinal_end": 0,
                                   "mcids": [0], "text_origins": ["to_unicode"]},
                }],
            }], "image_placements": [],
            "orders": {"source_order": ["p0-n0"], "tagged_order": ["p0-n0"],
                       "inferred_order": ["p0-n0"], "main_flow": ["p0-n0"]},
        }],
        "warnings": [],
    }
    counts = audit_stage4_layout(sample, 1)
    assert counts["main_flow_nodes"] == 1
    assert counts["tables"] == 1 and counts["table_cells"] == 1
    report = {"contains_extracted_content": False, "cases": [{"counts": counts}]}
    assert_privacy_safe(report)
    print("stage12 table benchmark self-test: ok")


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
        "schema_version": 1, "benchmark": "stage4_table_reconstruction", "private_corpus": True,
        "contains_extracted_content": False, "private_ir_written": False,
        "contract_sha256": contract_sha, "coordinate_space": COORDINATE_SPACE,
        "timing_scope": "release_cli_startup_plus_parse_layout_serialize_and_stdout_capture",
        "timing_excludes": ["json_decode", "schema_audit", "hashing", "report_write"],
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "psutil": psutil.__version__},
        "measurement": {"warmup_runs": warmups, "measured_runs": runs},
        "frozen_stage0_documa": frozen_documa, "cases": [],
    }

    for case_index, (case, path) in enumerate(cases, start=1):
        label = f"[{case_index}/{len(cases)}] {case['file_name']}"
        for warmup_index in range(warmups):
            print(f"{label} warmup {warmup_index + 1}/{warmups}", flush=True)
            run_layout(args.rust_cli, path)
        seconds: list[float] = []
        peak_rss_bytes: list[int] = []
        raw_hashes: list[str] = []
        output_sizes: list[int] = []
        canonical_hash: str | None = None
        counts: dict[str, Any] | None = None
        for run_index in range(runs):
            print(f"{label} measured {run_index + 1}/{runs}", flush=True)
            output, elapsed, peak_rss = run_layout_measured(args.rust_cli, path)
            seconds.append(elapsed)
            peak_rss_bytes.append(peak_rss)
            output_sizes.append(len(output))
            raw_hashes.append(digest_bytes(output))
            if counts is None:
                parsed = json.loads(output.decode("utf-8", errors="strict"))
                counts = audit_stage4_layout(parsed, int(case["pages"]))
                canonical_hash = digest_bytes(canonical_bytes(parsed))
                del parsed
            del output
        deterministic = len(set(raw_hashes)) == 1 and len(set(output_sizes)) == 1
        if not deterministic:
            raise ValueError(f"non-deterministic Layout IR for {path.name}")
        assert counts is not None and canonical_hash is not None
        report["cases"].append({
            "id": case["id"], "file_name": case["file_name"], "bytes": case["bytes"],
            "sha256": case["sha256"], "expected_pages": case["pages"],
            "timing": {"seconds": seconds, "median_seconds": statistics.median(seconds),
                       "min_seconds": min(seconds), "max_seconds": max(seconds),
                       "peak_rss_bytes_per_run": peak_rss_bytes,
                       "max_peak_rss_bytes": max(peak_rss_bytes)},
            "output": {"bytes_per_run": output_sizes, "sha256_per_run": raw_hashes,
                       "canonical_sha256": canonical_hash, "deterministic": deterministic},
            "counts": counts, "schema_audit_passed": True, "privacy_audit_passed": True,
        })

    layout_seconds = sum(case["timing"]["median_seconds"] for case in report["cases"])
    total_output_bytes = sum(case["output"]["bytes_per_run"][0] for case in report["cases"])
    scalar_keys = (
        "nodes", "spans", "main_flow_nodes", "tagged_pages", "tagged_nodes",
        "multi_column_proxy_pages", "artifacts", "tables", "table_cells",
        "table_source_links", "cell_source_links", "header_cells", "spanned_cells",
        "table_bboxes", "cell_bboxes", "image_placements", "warnings", "bboxes",
        "tagged_pairwise_correct", "tagged_pairwise_total",
        "source_pairwise_correct", "visual_yx_pairwise_correct", "visual_xy_pairwise_correct",
    )
    total_counts: dict[str, Any] = {
        key: sum(case["counts"][key] for case in report["cases"]) for key in scalar_keys
    }
    for mapping_name in ("role_counts", "table_evidence_counts", "warning_code_counts"):
        merged: Counter[str] = Counter()
        for case in report["cases"]:
            merged.update(case["counts"][mapping_name])
        total_counts[mapping_name] = dict(sorted(merged.items()))
    pair_total = total_counts["tagged_pairwise_total"]
    total_counts["tagged_pairwise_accuracy"] = (
        total_counts["tagged_pairwise_correct"] / pair_total if pair_total else None
    )
    for strategy in ("source", "visual_yx", "visual_xy"):
        total_counts[f"{strategy}_pairwise_accuracy"] = (
            total_counts[f"{strategy}_pairwise_correct"] / pair_total if pair_total else None
        )
    total_counts["main_flow_coverage"] = (
        total_counts["main_flow_nodes"] / total_counts["nodes"] if total_counts["nodes"] else None
    )
    report["summary"] = {
        "documents": len(report["cases"]), "pages": total_pages,
        "sum_of_document_medians_seconds": layout_seconds,
        "pages_per_second": total_pages / layout_seconds if layout_seconds else None,
        "throughput_ratio_vs_stage3": (
            (total_pages / layout_seconds) / STAGE3_PAGES_PER_SECOND if layout_seconds else None
        ),
        "max_peak_rss_bytes": max(
            case["timing"]["max_peak_rss_bytes"] for case in report["cases"]
        ),
        "speedup_vs_frozen_stage0_documa": (
            frozen_documa["sum_of_document_medians_seconds"] / layout_seconds if layout_seconds else None
        ),
        "comparison_note": "non_simultaneous_comparison_to_frozen_stage0_baseline",
        "total_serialized_bytes_one_run_per_document": total_output_bytes,
        "all_deterministic": all(case["output"]["deterministic"] for case in report["cases"]),
        "all_schema_audits_passed": all(case["schema_audit_passed"] for case in report["cases"]),
        "all_privacy_audits_passed": all(case["privacy_audit_passed"] for case in report["cases"]),
        "private_quality_ground_truth_available": False,
        "private_teds_s": None,
        "synthetic_exact_fixture_teds_s": 1.0,
        "synthetic_quality_note": "exact Rust topology assertions imply TEDS-S 1.0 for supported fixtures",
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