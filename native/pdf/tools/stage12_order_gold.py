#!/usr/bin/env python3
"""Validate and score Stage 7.3B block-level human reading-order gold."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

from stage12_baseline import digest_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "stage12" / "quality" / "order"
DEFAULT_PUBLIC_GOLD = DEFAULT_FIXTURE_ROOT / "public-gold.json"
DEFAULT_PUBLIC_PERFECT = DEFAULT_FIXTURE_ROOT / "public-candidate-perfect.json"
DEFAULT_PUBLIC_INVERTED = DEFAULT_FIXTURE_ROOT / "public-candidate-inverted.json"
GOLD_SCHEMA_VERSION = 2
CANDIDATE_SCHEMA_VERSION = 2
INTERNAL_ORDER_UNSPECIFIED = "unspecified"
ARTIFACT_ROLES = {"artifact", "page_header", "page_footer", "page_number"}
BLOCK_ROLES = {"main_flow", *ARTIFACT_ROLES}
ADJUDICATION_REASON_CODES = {
    "column_order",
    "sidebar_policy",
    "artifact_policy",
    "caption_anchor",
    "rotation_policy",
    "tag_conflict",
    "block_membership",
    "other_reviewed",
}
FORBIDDEN_KEYS = {
    "text",
    "raw_text",
    "normalized_text",
    "source_path",
    "url",
    "uri",
    "note",
    "notes",
}


class GoldError(ValueError):
    """Manifest or candidate validation error."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PUBLIC_GOLD)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GoldError(message)


def require_list(value: Any, location: str) -> list[Any]:
    require(isinstance(value, list), f"{location} must be an array")
    return value


def require_dict(value: Any, location: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{location} must be an object")
    return value


def unique_strings(value: Any, location: str) -> list[str]:
    items = require_list(value, location)
    require(
        all(isinstance(item, str) and item for item in items),
        f"{location} has invalid id",
    )
    require(len(items) == len(set(items)), f"{location} has duplicate ids")
    return items


def find_forbidden_key(value: Any, location: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
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


def validate_acyclic(
    nodes: set[str], pairs: list[tuple[str, str]], location: str
) -> None:
    edges: dict[str, set[str]] = {node: set() for node in nodes}
    indegree = {node: 0 for node in nodes}
    for before, after in pairs:
        if after not in edges[before]:
            edges[before].add(after)
            indegree[after] += 1
    queue = collections.deque(
        sorted(node for node, degree in indegree.items() if degree == 0)
    )
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for after in sorted(edges[node]):
            indegree[after] -= 1
            if indegree[after] == 0:
                queue.append(after)
    require(visited == len(nodes), f"{location} block precedence contains a cycle")


def validate_labels(
    value: Any,
    node_ids: list[str],
    location: str,
    *,
    require_coverage: bool,
) -> dict[str, Any]:
    labels = require_dict(value, location)
    nodes = set(node_ids)
    blocks_raw = require_list(labels.get("blocks"), f"{location}.blocks")
    blocks: list[dict[str, Any]] = []
    block_by_id: dict[str, dict[str, Any]] = {}
    assigned: dict[str, str] = {}
    for index, raw in enumerate(blocks_raw):
        block_location = f"{location}.blocks[{index}]"
        entry = require_dict(raw, block_location)
        block_id = entry.get("block_id")
        require(
            isinstance(block_id, str) and block_id,
            f"{block_location}.block_id is invalid",
        )
        require(block_id not in block_by_id, f"{location} has duplicate block ids")
        members = unique_strings(
            entry.get("member_node_ids"), f"{block_location}.member_node_ids"
        )
        require(members, f"{block_location} cannot be empty")
        require(set(members) <= nodes, f"{block_location} references unknown node")
        role = entry.get("role")
        require(role in BLOCK_ROLES, f"{block_location} has invalid block role")
        require(
            entry.get("internal_order") == INTERNAL_ORDER_UNSPECIFIED,
            f"{block_location}.internal_order must be unspecified",
        )
        for node_id in members:
            require(
                node_id not in assigned,
                f"{location} assigns node {node_id} to multiple blocks",
            )
            assigned[node_id] = str(block_id)
        block = {
            "block_id": str(block_id),
            "member_node_ids": members,
            "role": str(role),
            "internal_order": INTERNAL_ORDER_UNSPECIFIED,
        }
        blocks.append(block)
        block_by_id[str(block_id)] = block
    if require_coverage:
        require(
            set(assigned) == nodes,
            f"{location} must assign every node to exactly one block",
        )

    main_flow_block_ids = {
        block["block_id"] for block in blocks if block["role"] == "main_flow"
    }
    pairs_raw = require_list(
        labels.get("block_precedence_pairs"),
        f"{location}.block_precedence_pairs",
    )
    pairs: list[tuple[str, str]] = []
    for index, raw in enumerate(pairs_raw):
        pair = require_list(raw, f"{location}.block_precedence_pairs[{index}]")
        require(len(pair) == 2, f"{location} block precedence must have two ids")
        before, after = pair
        require(
            isinstance(before, str) and isinstance(after, str),
            f"{location} block precedence ids must be strings",
        )
        require(before != after, f"{location} block precedence cannot self-reference")
        require(
            before in main_flow_block_ids and after in main_flow_block_ids,
            f"{location} block precedence must reference main-flow blocks",
        )
        pairs.append((before, after))
    require(
        len(pairs) == len(set(pairs)),
        f"{location} has duplicate block precedence pairs",
    )
    validate_acyclic(main_flow_block_ids, pairs, location)
    if require_coverage and len(main_flow_block_ids) > 1:
        involved = {block_id for pair in pairs for block_id in pair}
        require(
            involved == main_flow_block_ids,
            f"{location} block precedence does not cover every main-flow block",
        )

    main_flow: list[str] = []
    artifact_roles: dict[str, str] = {}
    for block in blocks:
        if block["role"] == "main_flow":
            main_flow.extend(block["member_node_ids"])
        else:
            for node_id in block["member_node_ids"]:
                artifact_roles[node_id] = block["role"]
    return {
        "blocks": blocks,
        "block_by_id": block_by_id,
        "block_precedence_pairs": pairs,
        "main_flow": main_flow,
        "artifact_roles": artifact_roles,
    }


def block_signature(block: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return (block["role"], tuple(sorted(block["member_node_ids"])))


def canonical_partition(labels: dict[str, Any]) -> set[tuple[str, tuple[str, ...]]]:
    return {block_signature(block) for block in labels["blocks"]}


def canonical_precedence(
    labels: dict[str, Any],
) -> set[tuple[tuple[str, tuple[str, ...]], tuple[str, tuple[str, ...]]]]:
    signatures = {
        block_id: block_signature(block)
        for block_id, block in labels["block_by_id"].items()
    }
    return {
        (signatures[before], signatures[after])
        for before, after in labels["block_precedence_pairs"]
    }


def labels_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return canonical_partition(left) == canonical_partition(
        right
    ) and canonical_precedence(left) == canonical_precedence(right)


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = require_dict(value, "$")
    forbidden = find_forbidden_key(manifest)
    require(forbidden is None, f"privacy-forbidden key: {forbidden}")
    require(
        manifest.get("schema_version") == GOLD_SCHEMA_VERSION,
        "$.schema_version must be 2; v1 click-per-node manifests are superseded",
    )
    require(
        isinstance(manifest.get("private_corpus"), bool),
        "$.private_corpus must be boolean",
    )
    require(
        isinstance(manifest.get("redistributable"), bool),
        "$.redistributable must be boolean",
    )
    documents = require_list(manifest.get("documents"), "$.documents")
    if not documents:
        require(
            manifest.get("status") == "unconfigured",
            "empty manifest must have status=unconfigured",
        )
        return {
            "status": "BLOCKED",
            "reason": "human_order_gold_unconfigured",
            "documents": [],
            "pages": [],
        }
    review_required = manifest.get("status") == "review_required"
    require(
        manifest.get("status") in {"complete", "review_required"},
        "$.status must be complete or review_required",
    )

    document_ids: set[str] = set()
    validated_pages: list[dict[str, Any]] = []
    for document_index, raw_document in enumerate(documents):
        location = f"$.documents[{document_index}]"
        document = require_dict(raw_document, location)
        document_id = document.get("id")
        require(
            isinstance(document_id, str) and document_id,
            f"{location}.id is invalid",
        )
        require(document_id not in document_ids, f"{location}.id is duplicated")
        document_ids.add(document_id)
        sha256 = document.get("file_sha256")
        require(
            isinstance(sha256, str)
            and len(sha256) == 64
            and all(character in "0123456789abcdef" for character in sha256),
            f"{location}.file_sha256 is invalid",
        )
        pages = require_list(document.get("pages"), f"{location}.pages")
        page_numbers: set[int] = set()
        for page_index, raw_page in enumerate(pages):
            page_location = f"{location}.pages[{page_index}]"
            page = require_dict(raw_page, page_location)
            page_number = page.get("page_number")
            require(
                isinstance(page_number, int) and page_number > 0,
                f"{page_location}.page_number is invalid",
            )
            require(
                page_number not in page_numbers,
                f"{page_location}.page_number is duplicated",
            )
            page_numbers.add(page_number)
            node_ids = unique_strings(page.get("node_ids"), f"{page_location}.node_ids")
            require(node_ids, f"{page_location}.node_ids cannot be empty")
            reviews = require_list(page.get("reviews"), f"{page_location}.reviews")
            require(len(reviews) >= 2, f"{page_location} requires at least two reviews")
            reviewer_ids: set[str] = set()
            validated_reviews = []
            for review_index, raw_review in enumerate(reviews):
                review_location = f"{page_location}.reviews[{review_index}]"
                review = require_dict(raw_review, review_location)
                reviewer_id = review.get("reviewer_id")
                require(
                    isinstance(reviewer_id, str) and reviewer_id,
                    f"{review_location}.reviewer_id is invalid",
                )
                require(
                    reviewer_id not in reviewer_ids,
                    f"{review_location} reviewer is duplicated",
                )
                reviewer_ids.add(reviewer_id)
                validated_reviews.append(
                    validate_labels(
                        review.get("labels"),
                        node_ids,
                        f"{review_location}.labels",
                        require_coverage=not review_required,
                    )
                )
            adjudicated = validate_labels(
                page.get("adjudicated"),
                node_ids,
                f"{page_location}.adjudicated",
                require_coverage=not review_required,
            )
            reason_codes = unique_strings(
                page.get("adjudication_reason_codes", []),
                f"{page_location}.adjudication_reason_codes",
            )
            require(
                set(reason_codes) <= ADJUDICATION_REASON_CODES,
                f"{page_location} has invalid adjudication reason code",
            )
            if any(
                not labels_equivalent(review, validated_reviews[0])
                for review in validated_reviews[1:]
            ):
                require(
                    reason_codes,
                    f"{page_location} reviewer disagreement requires adjudication reason code",
                )
            validated_pages.append(
                {
                    "document_id": document_id,
                    "page_number": page_number,
                    "node_ids": node_ids,
                    "reviews": validated_reviews,
                    "adjudicated": adjudicated,
                    "adjudication_reason_codes": reason_codes,
                }
            )
    return {
        "status": "BLOCKED" if review_required else "READY",
        "reason": "human_order_review_incomplete" if review_required else None,
        "documents": sorted(document_ids),
        "pages": validated_pages,
    }


def validate_candidate(value: Any) -> dict[tuple[str, int], dict[str, Any]]:
    candidate = require_dict(value, "$candidate")
    forbidden = find_forbidden_key(candidate, "$candidate")
    require(forbidden is None, f"privacy-forbidden candidate key: {forbidden}")
    require(
        candidate.get("schema_version") == CANDIDATE_SCHEMA_VERSION,
        "$candidate.schema_version must be 2",
    )
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for document_index, raw_document in enumerate(
        require_list(candidate.get("documents"), "$candidate.documents")
    ):
        document_location = f"$candidate.documents[{document_index}]"
        document = require_dict(raw_document, document_location)
        document_id = document.get("id")
        require(
            isinstance(document_id, str) and document_id,
            f"{document_location}.id is invalid",
        )
        for page_index, raw_page in enumerate(
            require_list(document.get("pages"), f"{document_location}.pages")
        ):
            location = f"{document_location}.pages[{page_index}]"
            page = require_dict(raw_page, location)
            page_number = page.get("page_number")
            require(
                isinstance(page_number, int) and page_number > 0,
                f"{location}.page_number is invalid",
            )
            key = (str(document_id), page_number)
            require(key not in result, f"{location} is duplicated")
            order = unique_strings(
                page.get("inferred_order"), f"{location}.inferred_order"
            )
            main_flow = unique_strings(
                page.get("main_flow_node_ids"), f"{location}.main_flow_node_ids"
            )
            roles: dict[str, str] = {}
            for role_index, raw_role in enumerate(
                require_list(page.get("artifact_roles"), f"{location}.artifact_roles")
            ):
                role_location = f"{location}.artifact_roles[{role_index}]"
                role_entry = require_dict(raw_role, role_location)
                node_id, role = role_entry.get("node_id"), role_entry.get("role")
                require(
                    isinstance(node_id, str) and node_id,
                    f"{role_location}.node_id is invalid",
                )
                require(role in ARTIFACT_ROLES, f"{role_location}.role is invalid")
                require(node_id not in roles, f"{location} has duplicate artifact")
                roles[node_id] = str(role)
            require(
                set(main_flow) <= set(order),
                f"{location}.main_flow_node_ids references unknown order node",
            )
            require(
                set(roles) <= set(order),
                f"{location}.artifact_roles references unknown order node",
            )
            require(
                not (set(main_flow) & set(roles)),
                f"{location} overlaps main flow and artifacts",
            )
            result[key] = {
                "order": order,
                "main_flow": main_flow,
                "artifact_roles": roles,
            }
    return result


def f1(overlap: int, predicted: int, expected: int) -> dict[str, float]:
    if not predicted and not expected:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    precision = overlap / predicted if predicted else 0.0
    recall = overlap / expected if expected else 0.0
    value = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": value}


def reviewer_agreement(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    partitions = [canonical_partition(review) for review in reviews]
    precedences = [canonical_precedence(review) for review in reviews]
    artifacts = [review["artifact_roles"] for review in reviews]
    precedence_scores = []
    for left_index in range(len(reviews)):
        for right_index in range(left_index + 1, len(reviews)):
            union = precedences[left_index] | precedences[right_index]
            precedence_scores.append(
                len(precedences[left_index] & precedences[right_index]) / len(union)
                if union
                else 1.0
            )
    minimum = min(precedence_scores, default=1.0)
    return {
        "reviewers": len(reviews),
        "block_precedence_jaccard_min": minimum,
        "precedence_pair_jaccard_min": minimum,
        "block_partition_exact": all(
            value == partitions[0] for value in partitions[1:]
        ),
        "artifact_roles_exact": all(value == artifacts[0] for value in artifacts[1:]),
    }


def score_block_pair(
    before: dict[str, Any],
    after: dict[str, Any],
    positions: dict[str, int],
) -> tuple[int, int, float]:
    total = len(before["member_node_ids"]) * len(after["member_node_ids"])
    correct = sum(
        left in positions and right in positions and positions[left] < positions[right]
        for left in before["member_node_ids"]
        for right in after["member_node_ids"]
    )
    return correct, total, correct / total if total else 1.0


def score(
    validated: dict[str, Any], candidate: dict[tuple[str, int], dict[str, Any]]
) -> dict[str, Any]:
    require(validated["status"] == "READY", "gold manifest is not ready")
    expected_keys = {
        (page["document_id"], page["page_number"]) for page in validated["pages"]
    }
    require(
        set(candidate) == expected_keys, "candidate page coverage does not match gold"
    )
    block_pair_score_sum = 0.0
    block_pair_total = 0
    cross_node_correct = 0
    cross_node_total = 0
    flow_overlap = 0
    flow_predicted = 0
    flow_expected = 0
    artifact_correct = 0
    artifact_total = 0
    pages = []
    for gold_page in validated["pages"]:
        key = (gold_page["document_id"], gold_page["page_number"])
        predicted = candidate[key]
        require(
            set(predicted["order"]) == set(gold_page["node_ids"]),
            f"candidate order coverage does not match {key[0]} page {key[1]}",
        )
        positions = {node_id: index for index, node_id in enumerate(predicted["order"])}
        gold = gold_page["adjudicated"]
        page_score_sum = 0.0
        page_pair_count = 0
        page_cross_correct = 0
        page_cross_total = 0
        for before_id, after_id in gold["block_precedence_pairs"]:
            correct, total, pair_score = score_block_pair(
                gold["block_by_id"][before_id],
                gold["block_by_id"][after_id],
                positions,
            )
            page_score_sum += pair_score
            page_pair_count += 1
            page_cross_correct += correct
            page_cross_total += total
        predicted_flow = set(predicted["main_flow"])
        expected_flow = set(gold["main_flow"])
        page_flow_overlap = len(predicted_flow & expected_flow)
        page_artifact_correct = sum(
            predicted["artifact_roles"].get(node_id) == role
            for node_id, role in gold["artifact_roles"].items()
        )
        block_pair_score_sum += page_score_sum
        block_pair_total += page_pair_count
        cross_node_correct += page_cross_correct
        cross_node_total += page_cross_total
        flow_overlap += page_flow_overlap
        flow_predicted += len(predicted_flow)
        flow_expected += len(expected_flow)
        artifact_correct += page_artifact_correct
        artifact_total += len(gold["artifact_roles"])
        pages.append(
            {
                "document_id": key[0],
                "page_number": key[1],
                "block_pair_concordance": {
                    "pairs": page_pair_count,
                    "macro_accuracy": (
                        page_score_sum / page_pair_count if page_pair_count else 1.0
                    ),
                    "cross_node_correct": page_cross_correct,
                    "cross_node_total": page_cross_total,
                },
                "main_flow": f1(
                    page_flow_overlap, len(predicted_flow), len(expected_flow)
                ),
                "artifact_role_correct": page_artifact_correct,
                "artifact_role_total": len(gold["artifact_roles"]),
                "reviewer_agreement": reviewer_agreement(gold_page["reviews"]),
            }
        )
    macro_accuracy = (
        block_pair_score_sum / block_pair_total if block_pair_total else 1.0
    )
    cross_node_accuracy = (
        cross_node_correct / cross_node_total if cross_node_total else 1.0
    )
    artifact_accuracy = artifact_correct / artifact_total if artifact_total else 1.0
    return {
        "status": "PASS" if macro_accuracy >= 0.95 else "FAIL",
        "block_pair_concordance": {
            "pairs": block_pair_total,
            "macro_accuracy": macro_accuracy,
            "cross_node_correct": cross_node_correct,
            "cross_node_total": cross_node_total,
            "cross_node_accuracy": cross_node_accuracy,
        },
        "main_flow": f1(flow_overlap, flow_predicted, flow_expected),
        "artifact_role_accuracy": artifact_accuracy,
        "pages": pages,
    }


def blocked_report(manifest_path: Path, reason: str, pages: int) -> dict[str, Any]:
    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "stage": "stage-7.3b-block-order-gold",
        "status": "BLOCKED",
        "reason": reason,
        "review_pages": pages,
        "manifest_sha256": digest_file(manifest_path),
        "contains_extracted_content": False,
        "contains_source_path": False,
        "default_provider_cutover_allowed": False,
    }


def evaluate(manifest_path: Path, candidate_path: Path | None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validated = validate_manifest(manifest)
    if validated["status"] == "BLOCKED":
        return blocked_report(
            manifest_path,
            str(validated["reason"]),
            len(validated["pages"]),
        )
    report: dict[str, Any] = {
        "schema_version": GOLD_SCHEMA_VERSION,
        "stage": "stage-7.3b-block-order-gold",
        "status": "VALID",
        "manifest_sha256": digest_file(manifest_path),
        "private_corpus": bool(manifest["private_corpus"]),
        "documents": len(validated["documents"]),
        "pages": len(validated["pages"]),
        "contains_extracted_content": False,
        "contains_source_path": False,
        "default_provider_cutover_allowed": False,
    }
    if candidate_path is not None:
        candidate = validate_candidate(
            json.loads(candidate_path.read_text(encoding="utf-8"))
        )
        report["candidate_sha256"] = digest_file(candidate_path)
        report["score"] = score(validated, candidate)
        report["status"] = report["score"]["status"]
    return report


def expect_invalid(value: dict[str, Any], fragment: str) -> None:
    try:
        validate_manifest(value)
    except GoldError as error:
        require(fragment in str(error), f"unexpected validation error: {error}")
    else:
        raise AssertionError("invalid manifest was accepted")


def self_test() -> None:
    manifest = json.loads(DEFAULT_PUBLIC_GOLD.read_text(encoding="utf-8"))
    validated = validate_manifest(manifest)
    perfect = validate_candidate(
        json.loads(DEFAULT_PUBLIC_PERFECT.read_text(encoding="utf-8"))
    )
    inverted = validate_candidate(
        json.loads(DEFAULT_PUBLIC_INVERTED.read_text(encoding="utf-8"))
    )
    perfect_score = score(validated, perfect)
    inverted_score = score(validated, inverted)
    assert perfect_score["block_pair_concordance"]["macro_accuracy"] == 1.0
    assert perfect_score["main_flow"]["f1"] == 1.0
    assert perfect_score["artifact_role_accuracy"] == 1.0
    assert inverted_score["block_pair_concordance"]["macro_accuracy"] < 1.0
    assert inverted_score["main_flow"]["f1"] < 1.0
    assert all(
        page["reviewer_agreement"]["block_partition_exact"]
        for page in perfect_score["pages"]
    )

    duplicate_node = json.loads(json.dumps(manifest))
    duplicate_node["documents"][0]["pages"][0]["node_ids"].append("heading")
    expect_invalid(duplicate_node, "duplicate ids")

    duplicate_membership = json.loads(json.dumps(manifest))
    duplicate_membership["documents"][0]["pages"][0]["adjudicated"]["blocks"][0][
        "member_node_ids"
    ].append("paragraph_1")
    expect_invalid(duplicate_membership, "multiple blocks")

    cycle = json.loads(json.dumps(manifest))
    cycle_labels = cycle["documents"][0]["pages"][0]["adjudicated"]
    cycle_labels["block_precedence_pairs"].append(["body", "heading"])
    expect_invalid(cycle, "cycle")

    missing = json.loads(json.dumps(manifest))
    missing["documents"][0]["pages"][0]["adjudicated"]["blocks"][1][
        "member_node_ids"
    ].pop()
    expect_invalid(missing, "assign every node")

    empty = json.loads(json.dumps(manifest))
    empty["documents"][0]["pages"][0]["adjudicated"]["blocks"][0][
        "member_node_ids"
    ] = []
    expect_invalid(empty, "cannot be empty")

    artifact_precedence = json.loads(json.dumps(manifest))
    artifact_precedence["documents"][0]["pages"][0]["adjudicated"][
        "block_precedence_pairs"
    ].append(["body", "footer"])
    expect_invalid(artifact_precedence, "main-flow blocks")

    disagreement = json.loads(json.dumps(manifest))
    review_blocks = disagreement["documents"][0]["pages"][0]["reviews"][1]["labels"][
        "blocks"
    ]
    next(block for block in review_blocks if block["role"] == "page_footer")["role"] = (
        "artifact"
    )
    expect_invalid(disagreement, "requires adjudication reason code")

    forbidden = json.loads(json.dumps(manifest))
    forbidden["documents"][0]["pages"][0]["text"] = "forbidden"
    expect_invalid(forbidden, "privacy-forbidden")

    schema_v1 = json.loads(json.dumps(manifest))
    schema_v1["schema_version"] = 1
    expect_invalid(schema_v1, "v1 click-per-node manifests are superseded")

    private_template = DEFAULT_FIXTURE_ROOT / "private-order-manifest.example.json"
    assert evaluate(private_template, None)["status"] == "BLOCKED"
    print("stage12 Stage 7.3B block order gold self-test: ok")


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.manifest.is_file():
        raise FileNotFoundError(f"order gold manifest not found: {args.manifest}")
    if not args.validate_only and args.candidate is None:
        raise SystemExit("provide --candidate or use --validate-only")
    report = evaluate(args.manifest, None if args.validate_only else args.candidate)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(encoded)
        print(f"report: {args.output}")
        print(f"sha256: {hashlib.sha256(encoded).hexdigest()}")
    else:
        print(encoded.decode("utf-8"))
    return 0 if report["status"] in {"VALID", "PASS", "BLOCKED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
