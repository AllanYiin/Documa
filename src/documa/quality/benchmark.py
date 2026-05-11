"""Benchmark harness for Documa fixture manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from documa.quality.fixture_manifest import FixtureCase, load_fixture_manifest


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    manifest_path: Path = Path("fixtures/pdf/manifest.json")
    fixtures_dir: Path = Path("fixtures/pdf")
    require_files: bool = False


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


def run_fixture_benchmark(options: BenchmarkOptions | None = None) -> dict[str, Any]:
    options = options or BenchmarkOptions()
    manifest = load_fixture_manifest(options.manifest_path)
    case_results = [_case_result(case, options) for case in manifest.cases]
    summary = {
        "case_count": len(case_results),
        "passed": sum(1 for item in case_results if item.status == "passed"),
        "failed": sum(1 for item in case_results if item.status == "failed"),
        "skipped": sum(1 for item in case_results if item.status == "skipped"),
        "issue_types": sorted(manifest.issue_type_values()),
    }
    return {
        "status": "ok" if summary["failed"] == 0 else "failed",
        "manifest_version": manifest.version,
        "manifest_path": str(options.manifest_path),
        "fixtures_dir": str(options.fixtures_dir),
        "require_files": options.require_files,
        "summary": summary,
        "cases": [item.to_dict() for item in case_results],
    }

