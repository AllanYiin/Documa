"""Quality contracts, fixture manifests, and benchmark helpers."""

from documa.quality.benchmark import BenchmarkCaseResult, BenchmarkOptions, run_fixture_benchmark
from documa.quality.doctor import DoctorOptions, run_doctor
from documa.quality.fixture_manifest import FixtureCase, FixtureManifest, load_fixture_manifest

__all__ = [
    "BenchmarkCaseResult",
    "BenchmarkOptions",
    "FixtureCase",
    "FixtureManifest",
    "DoctorOptions",
    "load_fixture_manifest",
    "run_doctor",
    "run_fixture_benchmark",
]
