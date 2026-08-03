#!/usr/bin/env python3
"""Benchmark and audit Stage 5 image/navigation Layout IR without private content."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psutil
import stage12_table_benchmark as stage4
from stage12_layout_benchmark import (
    COORDINATE_SPACE,
    DEFAULT_BASELINE,
    DEFAULT_CONTRACT,
    ROOT,
    assert_privacy_safe,
    audit_bbox,
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

DEFAULT_OUTPUT = ROOT / "target" / "stage12-image-navigation-benchmark"
STAGE4_PAGES_PER_SECOND = 205.731635
STAGE4_MAX_PEAK_RSS_BYTES = 674_615_296
STAGE4_SERIALIZED_BYTES = 331_010_437
TARGET_KINDS = {"uri", "go_to", "unsupported"}
stage4.ROLES.add("caption")


def audit_point(value: Any, field: str) -> None:
    point = require_dict(value, field)
    if set(point) != {"x", "y"}:
        raise ValueError(f"{field} must contain x/y")
    if not all(isinstance(point[name], (int, float)) and math.isfinite(point[name]) for name in ("x", "y")):
        raise ValueError(f"{field} coordinates must be finite")


def audit_quad(value: Any, field: str) -> None:
    quad = require_dict(value, field)
    for name in ("top_left", "top_right", "bottom_right", "bottom_left"):
        audit_point(quad.get(name), f"{field}.{name}")


def audit_object_id(value: Any, field: str) -> tuple[int, int] | None:
    if value is None:
        return None
    object_id = require_dict(value, field)
    number, generation = object_id.get("number"), object_id.get("generation")
    if not isinstance(number, int) or number < 1 or not isinstance(generation, int) or generation < 0:
        raise ValueError(f"{field} must be a positive object id")
    return number, generation


def audit_target(value: Any, field: str, page_count: int) -> str:
    target = require_dict(value, field)
    kind = target.get("kind")
    if kind not in TARGET_KINDS:
        raise ValueError(f"{field}.kind is unknown: {kind!r}")
    if kind == "uri" and not isinstance(target.get("uri"), str):
        raise ValueError(f"{field}.uri must be a string")
    if kind == "go_to":
        page_index = target.get("page_index")
        if page_index is not None and (
            not isinstance(page_index, int) or not 0 <= page_index < page_count
        ):
            raise ValueError(f"{field}.page_index is invalid")
        if target.get("destination_name") is None and page_index is None:
            raise ValueError(f"{field} GoTo has neither name nor resolved page")
    if kind == "unsupported" and not isinstance(target.get("unsupported_action"), str):
        raise ValueError(f"{field}.unsupported_action must be a string")
    audit_object_id(target.get("page_object"), f"{field}.page_object")
    for name in ("destination_name", "fit", "unsupported_action"):
        if target.get(name) is not None and not isinstance(target[name], str):
            raise ValueError(f"{field}.{name} must be a string when present")
    return kind


def audit_image(
    value: Any,
    field: str,
    node_ids: set[str],
    seen_ids: set[str],
    object_ids: set[tuple[int, int]],
) -> dict[str, int | str]:
    image = require_dict(value, field)
    image_id = image.get("id")
    if not isinstance(image_id, str) or not image_id or image_id in seen_ids:
        raise ValueError(f"{field}.id must be unique and non-empty")
    seen_ids.add(image_id)
    ordinal = image.get("paint_ordinal")
    if not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError(f"{field}.paint_ordinal must be non-negative")
    if not isinstance(image.get("resource_name"), str) or not image["resource_name"]:
        raise ValueError(f"{field}.resource_name must be non-empty")
    object_id = audit_object_id(image.get("object"), f"{field}.object")
    if object_id is not None:
        object_ids.add(object_id)
    audit_bbox(image.get("bbox"), f"{field}.bbox")
    audit_quad(image.get("quad"), f"{field}.quad")
    audit_provenance(image.get("provenance"), f"{field}.provenance")
    source_ids = require_list(image.get("source_node_ids"), f"{field}.source_node_ids")
    if len(source_ids) != len(set(source_ids)) or any(node_id not in node_ids for node_id in source_ids):
        raise ValueError(f"{field}.source_node_ids are invalid")
    if not isinstance(image.get("confidence"), (int, float)) or not 0 <= image["confidence"] <= 1:
        raise ValueError(f"{field}.confidence is invalid")
    rule = image.get("rule_id")
    if not isinstance(rule, str) or not rule:
        raise ValueError(f"{field}.rule_id must be non-empty")
    if image.get("tag") is not None and not isinstance(image["tag"], str):
        raise ValueError(f"{field}.tag must be a string")
    if image.get("alt_text") is not None and not isinstance(image["alt_text"], str):
        raise ValueError(f"{field}.alt_text must be a string")
    if not isinstance(image.get("artifact", False), bool):
        raise ValueError(f"{field}.artifact must be boolean")
    return {
        "caption_links": len(source_ids),
        "tagged_figure": int(image.get("structure_object") is not None or rule.startswith("stage5b_tagged")),
        "artifact_image": int(image.get("artifact", False)),
        "rule": rule,
    }


def audit_stage5_layout(value: Any, expected_pages: int) -> dict[str, Any]:
    root = require_dict(value, "layout")
    capabilities = require_dict(root.get("capabilities"), "layout.capabilities")
    if capabilities.get("image_placements") is not True or capabilities.get("navigation") is not True:
        raise ValueError("Stage 5 image/navigation capabilities must be true")
    pages = require_list(root.get("pages"), "layout.pages")
    if len(pages) != expected_pages:
        raise ValueError("Stage 5 page count mismatch")
    additions: dict[str, Any] = {
        "image_placements": 0,
        "unique_image_objects": 0,
        "tagged_figures": 0,
        "caption_links": 0,
        "artifact_images": 0,
        "links": 0,
        "uri_links": 0,
        "goto_links": 0,
        "unsupported_navigation_targets": 0,
        "named_destinations": 0,
        "outline_items": 0,
        "image_rule_counts": {},
        "navigation_target_kind_counts": {},
    }
    seen_image_ids: set[str] = set()
    image_object_ids: set[tuple[int, int]] = set()
    image_rules: Counter[str] = Counter()
    target_kinds: Counter[str] = Counter()
    for page_index, page_value in enumerate(pages):
        page = require_dict(page_value, f"page[{page_index}]")
        node_ids = {
            require_dict(node, "node").get("id")
            for node in require_list(page.get("semantic_nodes"), "page.semantic_nodes")
        }
        images = require_list(page.get("image_placements"), "page.image_placements")
        additions["image_placements"] += len(images)
        for image_index, image in enumerate(images):
            counts = audit_image(
                image,
                f"page[{page_index}].image[{image_index}]",
                node_ids,
                seen_image_ids,
                image_object_ids,
            )
            additions["tagged_figures"] += counts["tagged_figure"]
            additions["caption_links"] += counts["caption_links"]
            additions["artifact_images"] += counts["artifact_image"]
            image_rules.update([counts["rule"]])
        links = require_list(page.get("links"), "page.links")
        additions["links"] += len(links)
        seen_link_ids: set[str] = set()
        for link_index, link_value in enumerate(links):
            field = f"page[{page_index}].link[{link_index}]"
            link = require_dict(link_value, field)
            link_id = link.get("id")
            if not isinstance(link_id, str) or not link_id or link_id in seen_link_ids:
                raise ValueError(f"{field}.id must be unique and non-empty")
            seen_link_ids.add(link_id)
            audit_bbox(link.get("bbox"), f"{field}.bbox")
            for quad_index, quad in enumerate(require_list(link.get("quads"), f"{field}.quads")):
                audit_quad(quad, f"{field}.quad[{quad_index}]")
            kind = audit_target(link.get("target"), f"{field}.target", len(pages))
            target_kinds.update([kind])
            if kind == "uri":
                additions["uri_links"] += 1
            elif kind == "go_to":
                additions["goto_links"] += 1
        page["image_placements"] = []
    named = require_list(root.get("named_destinations"), "layout.named_destinations")
    additions["named_destinations"] = len(named)
    seen_names: set[str] = set()
    for index, value in enumerate(named):
        destination = require_dict(value, f"named_destination[{index}]")
        name = destination.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            raise ValueError("named destination names must be unique and non-empty")
        seen_names.add(name)
        target_kinds.update([audit_target(destination.get("target"), "named.target", len(pages))])
    outlines = require_list(root.get("outlines"), "layout.outlines")
    additions["outline_items"] = len(outlines)
    seen_outline_ids: set[str] = set()
    for index, value in enumerate(outlines):
        outline = require_dict(value, f"outline[{index}]")
        outline_id = outline.get("id")
        if not isinstance(outline_id, str) or not outline_id or outline_id in seen_outline_ids:
            raise ValueError("outline ids must be unique and non-empty")
        seen_outline_ids.add(outline_id)
        if not isinstance(outline.get("title"), str) or not isinstance(outline.get("depth"), int):
            raise ValueError("outline title/depth are invalid")
        if outline.get("target") is not None:
            target_kinds.update([audit_target(outline["target"], "outline.target", len(pages))])
    additions["unique_image_objects"] = len(image_object_ids)
    additions["unsupported_navigation_targets"] = target_kinds["unsupported"]
    additions["image_rule_counts"] = dict(sorted(image_rules.items()))
    additions["navigation_target_kind_counts"] = dict(sorted(target_kinds.items()))

    capabilities["image_placements"] = False
    baseline = stage4.audit_stage4_layout(root, expected_pages)
    baseline.update(additions)
    return baseline


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
            raise RuntimeError(stderr.decode("utf-8", errors="replace"))
        output_file.seek(0)
        return output_file.read(), elapsed, peak_rss


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
            "inferred_order": True,
            "main_flow": True,
            "text_blocks": True,
            "semantic_roles": False,
            "tables": True,
            "image_placements": True,
            "navigation": True,
        },
        "pages": [{
            "page_index": 0,
            "page_number": 1,
            "coordinate_space": COORDINATE_SPACE,
            "geometry": {"coordinate_space": COORDINATE_SPACE,
                         "layout_bounds": {"x0": 0, "y0": 0, "x1": 100, "y1": 200}},
            "semantic_nodes": [],
            "tables": [],
            "image_placements": [],
            "links": [],
            "orders": {"source_order": [], "tagged_order": [],
                       "inferred_order": [], "main_flow": []},
        }],
        "named_destinations": [],
        "outlines": [],
        "warnings": [],
    }
    counts = audit_stage5_layout(sample, 1)
    assert counts["pages"] == 1 and counts["image_placements"] == 0
    report = {"contains_extracted_content": False, "cases": [{"counts": counts}]}
    assert_privacy_safe(report)
    print("stage12 image/navigation benchmark self-test: ok")


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
    contract_sha = digest_file(args.contract)
    cases = [(case, verify_case(args.corpus_dir, case)) for case in contract["documents"]]
    total_pages = sum(int(case["pages"]) for case, _ in cases)
    frozen_documa = load_frozen_documa_baseline(args.baseline_report, contract_sha, total_pages)
    report: dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "stage5_image_navigation",
        "private_corpus": True,
        "contains_extracted_content": False,
        "private_ir_written": False,
        "contract_sha256": contract_sha,
        "coordinate_space": COORDINATE_SPACE,
        "timing_scope": "release_cli_startup_plus_parse_layout_serialize_and_stdout_capture",
        "timing_excludes": ["json_decode", "schema_audit", "hashing", "report_write"],
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "psutil": psutil.__version__},
        "measurement": {"warmup_runs": warmups, "measured_runs": runs},
        "frozen_stage0_documa": frozen_documa,
        "cases": [],
    }
    for case_index, (case, pdf_path) in enumerate(cases, start=1):
        label = f"[{case_index}/{len(cases)}] {case['file_name']}"
        for warmup_index in range(warmups):
            print(f"{label} warmup {warmup_index + 1}/{warmups}", flush=True)
            run_layout(args.rust_cli, pdf_path)
        seconds: list[float] = []
        peaks: list[int] = []
        hashes: list[str] = []
        sizes: list[int] = []
        canonical_hash: str | None = None
        counts: dict[str, Any] | None = None
        for run_index in range(runs):
            print(f"{label} measured {run_index + 1}/{runs}", flush=True)
            output, elapsed, peak = run_layout_measured(args.rust_cli, pdf_path)
            seconds.append(elapsed)
            peaks.append(peak)
            sizes.append(len(output))
            hashes.append(digest_bytes(output))
            if counts is None:
                parsed = json.loads(output.decode("utf-8", errors="strict"))
                canonical_hash = digest_bytes(canonical_bytes(parsed))
                counts = audit_stage5_layout(parsed, int(case["pages"]))
                del parsed
            del output
        deterministic = len(set(hashes)) == 1 and len(set(sizes)) == 1
        if not deterministic:
            raise ValueError(f"non-deterministic Layout IR for {pdf_path.name}")
        assert counts is not None and canonical_hash is not None
        report["cases"].append({
            "id": case["id"], "file_name": case["file_name"], "bytes": case["bytes"],
            "sha256": case["sha256"], "expected_pages": case["pages"],
            "timing": {"seconds": seconds, "median_seconds": statistics.median(seconds),
                       "min_seconds": min(seconds), "max_seconds": max(seconds),
                       "peak_rss_bytes_per_run": peaks, "max_peak_rss_bytes": max(peaks)},
            "output": {"bytes_per_run": sizes, "sha256_per_run": hashes,
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
        "tagged_pairwise_correct", "tagged_pairwise_total", "source_pairwise_correct",
        "visual_yx_pairwise_correct", "visual_xy_pairwise_correct", "unique_image_objects",
        "tagged_figures", "caption_links", "artifact_images", "links", "uri_links",
        "goto_links", "unsupported_navigation_targets", "named_destinations", "outline_items",
    )
    total_counts: dict[str, Any] = {
        key: sum(case["counts"][key] for case in report["cases"]) for key in scalar_keys
    }
    for mapping_name in (
        "role_counts", "table_evidence_counts", "warning_code_counts",
        "image_rule_counts", "navigation_target_kind_counts",
    ):
        merged: Counter[str] = Counter()
        for case in report["cases"]:
            merged.update(case["counts"][mapping_name])
        total_counts[mapping_name] = dict(sorted(merged.items()))
    pair_total = total_counts["tagged_pairwise_total"]
    total_counts["tagged_pairwise_accuracy"] = (
        total_counts["tagged_pairwise_correct"] / pair_total if pair_total else None
    )
    total_counts["main_flow_coverage"] = (
        total_counts["main_flow_nodes"] / total_counts["nodes"] if total_counts["nodes"] else None
    )
    pages_per_second = total_pages / layout_seconds if layout_seconds else None
    max_peak_rss = max(case["timing"]["max_peak_rss_bytes"] for case in report["cases"])
    report["summary"] = {
        "documents": len(report["cases"]),
        "pages": total_pages,
        "sum_of_document_medians_seconds": layout_seconds,
        "pages_per_second": pages_per_second,
        "throughput_ratio_vs_stage4": (
            pages_per_second / STAGE4_PAGES_PER_SECOND if pages_per_second else None
        ),
        "feature_cost_vs_stage4": (
            1.0 - pages_per_second / STAGE4_PAGES_PER_SECOND if pages_per_second else None
        ),
        "max_peak_rss_bytes": max_peak_rss,
        "peak_rss_ratio_vs_stage4": max_peak_rss / STAGE4_MAX_PEAK_RSS_BYTES,
        "speedup_vs_frozen_stage0_documa": (
            frozen_documa["sum_of_document_medians_seconds"] / layout_seconds if layout_seconds else None
        ),
        "comparison_note": "non_simultaneous_comparison_to_frozen_stage0_baseline",
        "total_serialized_bytes_one_run_per_document": total_output_bytes,
        "serialized_size_ratio_vs_stage4": total_output_bytes / STAGE4_SERIALIZED_BYTES,
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