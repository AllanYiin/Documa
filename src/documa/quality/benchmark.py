"""Benchmark harness for Documa fixture manifests.

Two modes: "readiness" (manifest/file existence contract, the historical
behavior) and "quality" (score pipeline output against gold annotations in
``fixtures/pdf/gold/<case_id>/expected.partial.json`` using table TEDS/TEDS-S
and reading-order NED). Cases without gold stay in readiness mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from documa.quality.fixture_manifest import FixtureCase, load_fixture_manifest
from documa.quality.metrics_layout_roles import header_footer_role_score, ocr_text_recall
from documa.quality.metrics_reading_order import reading_order_score
from documa.quality.metrics_relations import relation_link_score
from documa.quality.metrics_table_teds import score_table


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    manifest_path: Path = Path("fixtures/pdf/manifest.json")
    fixtures_dir: Path = Path("fixtures/pdf")
    require_files: bool = False
    mode: str = "readiness"
    gold_dir: Path = Path("fixtures/pdf/gold")
    quality_threshold: float = 0.85


@dataclass(slots=True)
class BenchmarkCaseResult:
    case_id: str
    issue_type: str
    title: str
    status: str
    expected_capabilities: list[str]
    file: str | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "issue_type": self.issue_type,
            "title": self.title,
            "status": self.status,
            "expected_capabilities": self.expected_capabilities,
            "file": self.file,
            "checks": self.checks,
            "message": self.message,
        }


def _fixture_path(case: FixtureCase, options: BenchmarkOptions) -> Path | None:
    if not case.file:
        return None
    path = Path(case.file)
    if path.is_absolute():
        return path
    return options.fixtures_dir / path


def _case_result(case: FixtureCase, options: BenchmarkOptions) -> BenchmarkCaseResult:
    checks = [
        {
            "name": "expected_capability_contract",
            "status": "passed" if len(case.expected_capabilities) >= 2 else "failed",
            "details": {"expected_capability_count": len(case.expected_capabilities)},
        }
    ]
    path = _fixture_path(case, options)
    if path is None:
        status = "failed" if options.require_files else "skipped"
        checks.append(
            {
                "name": "fixture_file_declared",
                "status": "failed" if options.require_files else "skipped",
                "details": {"file": None},
            }
        )
        return BenchmarkCaseResult(
            case_id=case.id,
            issue_type=case.issue_type.value,
            title=case.title,
            status=status,
            expected_capabilities=case.expected_capabilities,
            file=None,
            checks=checks,
            message="Fixture file is not declared.",
        )

    file_exists = path.exists()
    checks.append(
        {
            "name": "fixture_file_exists",
            "status": "passed" if file_exists else "failed",
            "details": {"path": str(path)},
        }
    )
    return BenchmarkCaseResult(
        case_id=case.id,
        issue_type=case.issue_type.value,
        title=case.title,
        status="passed" if file_exists else "failed",
        expected_capabilities=case.expected_capabilities,
        file=str(path),
        checks=checks,
        message=None if file_exists else "Fixture file is missing.",
    )


def _ordered_block_texts(document: dict[str, Any]) -> list[str]:
    blocks = []
    for page in document.get("pages", []) or []:
        for block in page.get("blocks", []) or []:
            order = block.get("order_index")
            text = (block.get("text") or {}).get("raw_text", "")
            blocks.append((page.get("page_number", 0), order if order is not None else 10**9, str(text)))
    blocks.sort(key=lambda item: (item[0], item[1]))
    return [text for _page, _order, text in blocks]


def _ocr_extra_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _reading_order_stats(document: dict[str, Any]) -> dict[str, Any]:
    """Trace-derived health stats: fallback share and zone composition."""
    total_blocks = 0
    fallback_blocks = 0
    zone_kinds: dict[str, int] = {}
    for page in document.get("pages", []) or []:
        for block in page.get("blocks", []) or []:
            total_blocks += 1
            rule = ((block.get("metadata") or {}).get("reading_order") or {}).get("rule")
            if rule == "fallback_row_major":
                fallback_blocks += 1
        trace = (page.get("metadata") or {}).get("reading_order_trace") or {}
        for zone in trace.get("zones", []) or []:
            kind = str(zone.get("kind"))
            zone_kinds[kind] = zone_kinds.get(kind, 0) + 1
    return {
        "fallback_block_ratio": round(fallback_blocks / total_blocks, 4) if total_blocks else 0.0,
        "zone_kinds": zone_kinds,
    }


def _quality_case_result(case: FixtureCase, options: BenchmarkOptions, gold_path: Path) -> BenchmarkCaseResult:
    fixture_path = _fixture_path(case, options)
    base = BenchmarkCaseResult(
        case_id=case.id,
        issue_type=case.issue_type.value,
        title=case.title,
        status="error",
        expected_capabilities=case.expected_capabilities,
        file=str(fixture_path) if fixture_path else None,
    )
    if fixture_path is None or not fixture_path.exists():
        base.message = "Quality mode requires an existing fixture file."
        return base
    try:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base.message = f"Cannot read gold annotation: {exc}"
        return base

    threshold = gold.get("threshold", options.quality_threshold)
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        base.message = f"threshold must be in [0,1], got {threshold!r} ({gold_path})"
        return base
    threshold = float(threshold)

    needs_ocr = bool(gold.get("ocr_expected_texts"))
    if needs_ocr and not _ocr_extra_available():
        base.status = "skipped"
        base.message = "Gold expects OCR output but the documa[ocr] extra is not installed."
        return base

    # Lazy import through the tools layer: the metrics stay pipeline-free, the
    # orchestrator needs a processed document to score.
    from documa.interfaces.tools import process_document_tool

    try:
        payload = process_document_tool(source=str(fixture_path), ocr=needs_ocr)
    except Exception as exc:  # pipeline failure is a case error, not a crash
        base.message = f"Pipeline failed: {exc}"
        return base
    if payload.get("status") != "ok":
        base.message = f"Pipeline failed: {payload.get('message')}"
        return base
    document = payload["document"]

    scores: dict[str, Any] = {}
    for gold_table in gold.get("tables", []) or []:
        index = int(gold_table.get("table_index", 0))
        actual_tables = document.get("tables", []) or []
        if index >= len(actual_tables):
            scores[f"table_{index}"] = {"teds": 0.0, "teds_s": 0.0, "missing": True}
            continue
        scores[f"table_{index}"] = score_table(actual_tables[index].get("rows") or [], gold_table.get("html", ""))
    if gold.get("reading_order"):
        scores["reading_order"] = reading_order_score(gold["reading_order"], _ordered_block_texts(document))
    if gold.get("relations"):
        scores["relations"] = relation_link_score(document, gold["relations"])
    if gold.get("excluded_texts"):
        scores["header_footer"] = header_footer_role_score(document, gold["excluded_texts"])
    if gold.get("ocr_expected_texts"):
        scores["ocr_recall"] = ocr_text_recall(document, gold["ocr_expected_texts"])

    flat_scores = []
    for entry in scores.values():
        if "score" in entry:
            flat_scores.append(float(entry["score"]))
        elif "teds_s" in entry:
            flat_scores.append(float(entry["teds_s"]))
    passed = bool(flat_scores) and all(value >= threshold for value in flat_scores)

    base.status = "passed" if passed else "failed"
    base.checks = [
        {"name": "quality_scores", "status": base.status, "details": scores},
        {"name": "quality_threshold", "status": "info", "details": {"threshold": threshold}},
        {"name": "reading_order_stats", "status": "info", "details": _reading_order_stats(document)},
    ]
    base.message = None if flat_scores else "Gold annotation contains nothing to score."
    if not flat_scores:
        base.status = "error"
    return base


def run_fixture_benchmark(options: BenchmarkOptions | None = None) -> dict[str, Any]:
    options = options or BenchmarkOptions()
    manifest = load_fixture_manifest(options.manifest_path)
    case_results = []
    for case in manifest.cases:
        gold_path = options.gold_dir / case.id / "expected.partial.json"
        if options.mode == "quality" and gold_path.exists():
            result = _quality_case_result(case, options, gold_path)
            result.checks.insert(0, {"name": "mode", "status": "quality", "details": {"gold": str(gold_path)}})
        else:
            result = _case_result(case, options)
            if options.mode == "quality":
                result.checks.insert(0, {"name": "mode", "status": "readiness", "details": {"gold": None}})
        case_results.append(result)

    # Orphan gold directories are annotation errors, never silently skipped.
    if options.mode == "quality" and options.gold_dir.exists():
        known_ids = {case.id for case in manifest.cases}
        for gold_case_dir in sorted(options.gold_dir.iterdir()):
            if gold_case_dir.is_dir() and gold_case_dir.name not in known_ids:
                case_results.append(
                    BenchmarkCaseResult(
                        case_id=gold_case_dir.name,
                        issue_type="unknown",
                        title="Orphan gold annotation",
                        status="error",
                        expected_capabilities=[],
                        message=f"Gold directory has no matching manifest case: {gold_case_dir.name}",
                    )
                )
    summary = {
        "case_count": len(case_results),
        "passed": sum(1 for item in case_results if item.status == "passed"),
        "failed": sum(1 for item in case_results if item.status == "failed"),
        "skipped": sum(1 for item in case_results if item.status == "skipped"),
        "errors": sum(1 for item in case_results if item.status == "error"),
        "issue_types": sorted(manifest.issue_type_values()),
    }
    if options.mode == "quality":
        fallback_ratios = [
            check["details"]["fallback_block_ratio"]
            for item in case_results
            for check in item.checks
            if check.get("name") == "reading_order_stats"
        ]
        summary["fallback_block_ratio_max"] = max(fallback_ratios, default=0.0)
    return {
        "status": "ok" if summary["failed"] == 0 and summary["errors"] == 0 else "failed",
        "mode": options.mode,
        "manifest_version": manifest.version,
        "manifest_path": str(options.manifest_path),
        "fixtures_dir": str(options.fixtures_dir),
        "require_files": options.require_files,
        "summary": summary,
        "cases": [item.to_dict() for item in case_results],
    }

