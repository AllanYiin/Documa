"""Environment and package readiness diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import sys
from typing import Any

from documa import __version__
from documa.quality.benchmark import BenchmarkOptions, run_fixture_benchmark
from documa.summarization import SummaryError, load_lingxi_summary_provider


@dataclass(frozen=True, slots=True)
class DoctorOptions:
    project_root: Path = Path(".")
    include_benchmark: bool = True


def _check_python_version() -> dict[str, Any]:
    version = sys.version_info
    passed = version >= (3, 10)
    return {
        "name": "python_version",
        "status": "passed" if passed else "failed",
        "details": {
            "required": ">=3.10",
            "current": f"{version.major}.{version.minor}.{version.micro}",
        },
    }


def _check_module(module_name: str, *, required: bool) -> dict[str, Any]:
    available = find_spec(module_name) is not None
    if required:
        status = "passed" if available else "failed"
    else:
        status = "passed" if available else "warning"
    return {
        "name": f"module:{module_name}",
        "status": status,
        "details": {"module": module_name, "required": required, "available": available},
    }


def _check_path(path: Path, *, name: str, required: bool = True) -> dict[str, Any]:
    exists = path.exists()
    return {
        "name": name,
        "status": "passed" if exists or not required else "failed",
        "details": {"path": str(path), "exists": exists, "required": required},
    }


def _check_lingxi_summary() -> dict[str, Any]:
    try:
        provider = load_lingxi_summary_provider()
    except SummaryError as exc:
        return {
            "name": "summary_provider:lingxi",
            "status": "warning",
            "details": {"available": False, "code": exc.code, "message": str(exc), "required": False},
        }
    return {
        "name": "summary_provider:lingxi",
        "status": "passed",
        "details": {"available": True, "provider": provider.name, "version": provider.version, "required": False},
    }


def run_doctor(options: DoctorOptions | None = None) -> dict[str, Any]:
    options = options or DoctorOptions()
    root = Path(options.project_root)
    checks = [
        _check_python_version(),
        _check_module("documa", required=True),
        _check_module("fitz", required=False),
        _check_module("mcp", required=False),
        _check_lingxi_summary(),
        _check_path(root / "pyproject.toml", name="pyproject"),
        _check_path(root / "README.md", name="readme"),
        _check_path(root / "fixtures" / "pdf" / "manifest.json", name="fixture_manifest"),
    ]

    benchmark_summary = None
    if options.include_benchmark:
        benchmark = run_fixture_benchmark(
            BenchmarkOptions(
                manifest_path=root / "fixtures" / "pdf" / "manifest.json",
                fixtures_dir=root / "fixtures" / "pdf",
            )
        )
        benchmark_summary = benchmark["summary"]
        checks.append(
            {
                "name": "fixture_benchmark_readiness",
                "status": "passed" if benchmark["status"] == "ok" else "failed",
                "details": benchmark_summary,
            }
        )

    failed = sum(1 for check in checks if check["status"] == "failed")
    warnings = sum(1 for check in checks if check["status"] == "warning")
    return {
        "status": "ok" if failed == 0 else "failed",
        "documa_version": __version__,
        "project_root": str(root),
        "summary": {
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
            "warnings": warnings,
            "failed": failed,
        },
        "checks": checks,
        "benchmark_summary": benchmark_summary,
    }
