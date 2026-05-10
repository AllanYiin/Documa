"""Fixture manifest contract for Documa parsing risks."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from documa.core.ir import FixtureIssueType


REQUIRED_ISSUE_TYPES = frozenset(item.value for item in FixtureIssueType)


@dataclass(frozen=True, slots=True)
class FixtureCase:
    id: str
    title: str
    issue_type: FixtureIssueType
    description: str
    expected_capabilities: list[str]
    file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    version: str
    cases: list[FixtureCase]

    def issue_type_values(self) -> set[str]:
        return {case.issue_type.value for case in self.cases}

    def missing_required_issue_types(self) -> set[str]:
        return REQUIRED_ISSUE_TYPES - self.issue_type_values()

    def validate(self) -> None:
        missing = self.missing_required_issue_types()
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"Fixture manifest is missing required issue types: {missing_text}")
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Fixture manifest contains duplicate case ids: {', '.join(duplicates)}")


def load_fixture_manifest(path: str | Path) -> FixtureManifest:
    """Load and validate a fixture manifest JSON file."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [
        FixtureCase(
            id=item["id"],
            title=item["title"],
            issue_type=FixtureIssueType(item["issue_type"]),
            description=item["description"],
            expected_capabilities=list(item["expected_capabilities"]),
            file=item.get("file"),
            metadata=dict(item.get("metadata", {})),
        )
        for item in payload["cases"]
    ]
    manifest = FixtureManifest(version=payload["version"], cases=cases)
    manifest.validate()
    return manifest

