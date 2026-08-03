#!/usr/bin/env python3
"""Validate and summarize the Stage 7.3D timed two-reviewer blind pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

from stage12_order_gold import (
    ADJUDICATION_REASON_CODES,
    labels_equivalent,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_GOLD = (
    ROOT / "tests" / "fixtures" / "stage12" / "quality" / "order" / "public-gold.json"
)
MAX_SECONDS = 7 * 24 * 60 * 60
MAX_COUNT = 10_000_000
SESSION_KEYS = {
    "reviewer_id",
    "active_seconds",
    "pages_completed",
    "brush_transactions",
    "correction_transactions",
    "undo_transactions",
    "export_attempts",
    "validation_errors",
}
ADJUDICATION_KEYS = {
    "active_seconds",
    "pages_reviewed",
    "disagreement_pages",
    "reason_counts",
    "validation_errors",
}


class PilotValidationError(ValueError):
    """Stable validation failure for a malformed pilot log."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pilot-log", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PilotValidationError(message)


def require_dict(value: Any, location: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{location} must be an object")
    return value


def require_exact_keys(
    value: dict[str, Any], expected: set[str], location: str
) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    require(not missing, f"{location} is missing keys: {missing}")
    require(not extra, f"{location} has unsupported keys: {extra}")


def bounded_count(value: Any, location: str, *, positive: bool = False) -> int:
    require(
        isinstance(value, int) and not isinstance(value, bool),
        f"{location} must be an integer",
    )
    minimum = 1 if positive else 0
    require(minimum <= value <= MAX_COUNT, f"{location} is outside the allowed range")
    return value


def bounded_seconds(value: Any, location: str) -> float:
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{location} must be numeric",
    )
    result = float(value)
    require(
        math.isfinite(result) and 0.0 < result <= MAX_SECONDS,
        f"{location} is outside the allowed range",
    )
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_reviewer_ids(manifest: dict[str, Any]) -> list[str]:
    identities: set[str] | None = None
    for document in manifest["documents"]:
        for page in document["pages"]:
            page_ids = {review["reviewer_id"] for review in page["reviews"]}
            identities = page_ids if identities is None else identities
            require(
                page_ids == identities,
                "manifest reviewer identities must match on every page",
            )
    require(
        identities is not None and len(identities) == 2,
        "manifest must have exactly two reviewers",
    )
    return sorted(identities)


def manifest_facts(validated: dict[str, Any]) -> dict[str, Any]:
    pages = validated["pages"]
    reviewer_ids = set(validated["reviewer_ids"])
    disagreement_pages = 0
    reason_counts: dict[str, int] = {}
    for page in pages:
        reviews = page["reviews"]
        disagreement = any(
            not labels_equivalent(review, reviews[0]) for review in reviews[1:]
        )
        if disagreement:
            disagreement_pages += 1
        for reason in page["adjudication_reason_codes"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    require(len(reviewer_ids) == 2, "manifest must have exactly two reviewers")
    return {
        "pages": len(pages),
        "reviewer_ids": sorted(reviewer_ids),
        "disagreement_pages": disagreement_pages,
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def validate_pilot(
    log_value: Any, validated_manifest: dict[str, Any]
) -> dict[str, Any]:
    log = require_dict(log_value, "$")
    require(log.get("schema_version") == 1, "$.schema_version must be 1")
    status = log.get("status")
    if status == "unconfigured":
        require(log.get("sessions") == [], "unconfigured pilot sessions must be empty")
        require(
            log.get("adjudication") == {}, "unconfigured adjudication must be empty"
        )
        return {
            "status": "BLOCKED",
            "reason": "human_order_pilot_unconfigured",
            "pages": len(validated_manifest.get("pages", [])),
        }
    require(status == "complete", "$.status must be complete or unconfigured")
    if validated_manifest["status"] != "READY":
        return {
            "status": "BLOCKED",
            "reason": "human_order_review_incomplete",
            "pages": len(validated_manifest.get("pages", [])),
        }

    packet_hash = log.get("packet_index_sha256")
    require(
        isinstance(packet_hash, str)
        and len(packet_hash) == 64
        and all(character in "0123456789abcdef" for character in packet_hash),
        "$.packet_index_sha256 must be 64 lowercase hex characters",
    )
    facts = manifest_facts(validated_manifest)
    sessions = log.get("sessions")
    require(
        isinstance(sessions, list) and len(sessions) == 2,
        "$.sessions must contain exactly two entries",
    )
    validated_sessions = []
    seen_ids: set[str] = set()
    for index, raw_session in enumerate(sessions):
        location = f"$.sessions[{index}]"
        session = require_dict(raw_session, location)
        require_exact_keys(session, SESSION_KEYS, location)
        reviewer_id = session["reviewer_id"]
        require(
            reviewer_id in facts["reviewer_ids"],
            f"{location}.reviewer_id is not in the manifest",
        )
        require(reviewer_id not in seen_ids, f"{location}.reviewer_id is duplicated")
        seen_ids.add(reviewer_id)
        active_seconds = bounded_seconds(
            session["active_seconds"], f"{location}.active_seconds"
        )
        pages_completed = bounded_count(
            session["pages_completed"], f"{location}.pages_completed", positive=True
        )
        require(
            pages_completed == facts["pages"],
            f"{location}.pages_completed must equal manifest pages",
        )
        row = {
            "reviewer_id": reviewer_id,
            "active_seconds": active_seconds,
            "pages_completed": pages_completed,
        }
        for key in sorted(
            SESSION_KEYS - {"reviewer_id", "active_seconds", "pages_completed"}
        ):
            row[key] = bounded_count(session[key], f"{location}.{key}")
        row["seconds_per_page"] = active_seconds / pages_completed
        row["corrections_per_page"] = row["correction_transactions"] / pages_completed
        row["undo_per_page"] = row["undo_transactions"] / pages_completed
        validated_sessions.append(row)
    require(
        seen_ids == set(facts["reviewer_ids"]),
        "pilot sessions must match both manifest reviewers",
    )

    adjudication = require_dict(log.get("adjudication"), "$.adjudication")
    require_exact_keys(adjudication, ADJUDICATION_KEYS, "$.adjudication")
    adjudication_seconds = bounded_seconds(
        adjudication["active_seconds"], "$.adjudication.active_seconds"
    )
    pages_reviewed = bounded_count(
        adjudication["pages_reviewed"], "$.adjudication.pages_reviewed", positive=True
    )
    require(
        pages_reviewed == facts["pages"],
        "$.adjudication.pages_reviewed must equal manifest pages",
    )
    disagreement_pages = bounded_count(
        adjudication["disagreement_pages"], "$.adjudication.disagreement_pages"
    )
    require(
        disagreement_pages == facts["disagreement_pages"],
        "$.adjudication.disagreement_pages does not match the manifest",
    )
    validation_errors = bounded_count(
        adjudication["validation_errors"], "$.adjudication.validation_errors"
    )
    raw_reason_counts = require_dict(
        adjudication["reason_counts"], "$.adjudication.reason_counts"
    )
    require(
        set(raw_reason_counts) <= ADJUDICATION_REASON_CODES,
        "$.adjudication.reason_counts has an invalid reason code",
    )
    reason_counts = {
        key: bounded_count(value, f"$.adjudication.reason_counts.{key}")
        for key, value in sorted(raw_reason_counts.items())
        if value != 0
    }
    require(
        reason_counts == facts["reason_counts"],
        "$.adjudication.reason_counts does not match the manifest",
    )

    validated_sessions.sort(key=lambda item: item["reviewer_id"])
    seconds_per_page = [item["seconds_per_page"] for item in validated_sessions]
    return {
        "status": "READY",
        "reason": None,
        "pages": facts["pages"],
        "reviewers": len(validated_sessions),
        "packet_index_sha256": packet_hash,
        "agreement": {
            "exact_pages": facts["pages"] - facts["disagreement_pages"],
            "disagreement_pages": facts["disagreement_pages"],
            "exact_page_rate": (facts["pages"] - facts["disagreement_pages"])
            / facts["pages"],
            "reason_counts": facts["reason_counts"],
        },
        "reviewer_sessions": validated_sessions,
        "reviewer_timing": {
            "total_active_seconds": sum(
                item["active_seconds"] for item in validated_sessions
            ),
            "mean_seconds_per_page": statistics.fmean(seconds_per_page),
            "median_seconds_per_page": statistics.median(seconds_per_page),
        },
        "adjudication": {
            "active_seconds": adjudication_seconds,
            "pages_reviewed": pages_reviewed,
            "validation_errors": validation_errors,
            "seconds_per_page": adjudication_seconds / pages_reviewed,
            "seconds_per_disagreement_page": (
                adjudication_seconds / disagreement_pages
                if disagreement_pages
                else None
            ),
        },
    }


def evaluate(manifest_path: Path, pilot_log_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pilot_log = json.loads(pilot_log_path.read_text(encoding="utf-8"))
    validated_manifest = validate_manifest(manifest)
    validated_manifest["reviewer_ids"] = manifest_reviewer_ids(manifest)
    result = validate_pilot(pilot_log, validated_manifest)
    return {
        "schema_version": 1,
        "stage": "stage-7.3d-timed-blind-pilot",
        "contains_extracted_content": False,
        "contains_source_path": False,
        "manifest_sha256": sha256(manifest_path),
        "pilot_log_sha256": sha256(pilot_log_path),
        "stage_7_4_gate_review_allowed": result["status"] == "READY",
        **result,
    }


def reason_counts_for(validated: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for page in validated["pages"]:
        for reason in page["adjudication_reason_codes"]:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def self_test() -> None:
    manifest = json.loads(PUBLIC_GOLD.read_text(encoding="utf-8"))
    validated = validate_manifest(manifest)
    validated["reviewer_ids"] = manifest_reviewer_ids(manifest)
    facts = manifest_facts(validated)
    sessions = []
    for offset, reviewer_id in enumerate(facts["reviewer_ids"]):
        sessions.append(
            {
                "reviewer_id": reviewer_id,
                "active_seconds": 120.0 + offset * 20.0,
                "pages_completed": facts["pages"],
                "brush_transactions": 12 + offset,
                "correction_transactions": 2,
                "undo_transactions": 1,
                "export_attempts": 1,
                "validation_errors": 0,
            }
        )
    log = {
        "schema_version": 1,
        "status": "complete",
        "packet_index_sha256": "a" * 64,
        "sessions": sessions,
        "adjudication": {
            "active_seconds": 45.0,
            "pages_reviewed": facts["pages"],
            "disagreement_pages": facts["disagreement_pages"],
            "reason_counts": reason_counts_for(validated),
            "validation_errors": 0,
        },
    }
    report = validate_pilot(log, validated)
    assert report["status"] == "READY"
    assert report["reviewers"] == 2
    assert report["pages"] == facts["pages"]

    unconfigured = {
        "schema_version": 1,
        "status": "unconfigured",
        "sessions": [],
        "adjudication": {},
    }
    assert validate_pilot(unconfigured, validated)["status"] == "BLOCKED"

    def expect_invalid(mutator: Any, fragment: str) -> None:
        candidate = json.loads(json.dumps(log))
        mutator(candidate)
        try:
            validate_pilot(candidate, validated)
        except PilotValidationError as error:
            assert fragment in str(error), error
        else:
            raise AssertionError(f"pilot mutation unexpectedly passed: {fragment}")

    expect_invalid(
        lambda value: value["sessions"][1].update(
            {"reviewer_id": value["sessions"][0]["reviewer_id"]}
        ),
        "duplicated",
    )
    expect_invalid(
        lambda value: value["sessions"][0].update({"active_seconds": -1}),
        "allowed range",
    )
    expect_invalid(
        lambda value: value["adjudication"].update(
            {"disagreement_pages": facts["disagreement_pages"] + 1}
        ),
        "does not match",
    )
    expect_invalid(
        lambda value: value["sessions"][0].update({"reviewer_name": "private"}),
        "unsupported keys",
    )
    print("stage12 Stage 7.3D timed blind pilot self-test: ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.manifest is None or args.pilot_log is None:
        raise SystemExit("--manifest and --pilot-log are required")
    report = evaluate(args.manifest, args.pilot_log)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"report: {args.output}")
        print(f"sha256: {sha256(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
