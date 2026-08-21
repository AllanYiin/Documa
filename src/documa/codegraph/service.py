"""Bounded graph queries and source-hash verified code evidence reads."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Protocol

from documa.codegraph.models import CodeEdgeType, CodeNodeKind, EdgeResolution, sha256_bytes, sha256_text
from documa.codegraph.store import CodeGraphError, _metadata, open_code_graph


QUERY_INTENTS = {
    "lookup",
    "dependencies",
    "callers",
    "callees",
    "trace",
    "impact",
    "cycles",
    "overview",
    "diff",
}
HARD_RESOLUTIONS = {EdgeResolution.EXACT.value, EdgeResolution.RESOLVED.value}
CALL_EDGE_TYPES = {
    CodeEdgeType.CALLS.value,
    CodeEdgeType.CONSTRUCTS.value,
    CodeEdgeType.REGISTERS_CALLBACK.value,
}
DEPENDENCY_EDGE_TYPES = {CodeEdgeType.IMPORTS_MODULE.value, CodeEdgeType.IMPORTS_SYMBOL.value}
IMPACT_EDGE_TYPES = CALL_EDGE_TYPES | DEPENDENCY_EDGE_TYPES | {
    CodeEdgeType.INHERITS.value,
    CodeEdgeType.IMPLEMENTS.value,
    CodeEdgeType.EXPORTS.value,
}
TRACE_EDGE_TYPES = IMPACT_EDGE_TYPES | {CodeEdgeType.DECORATES.value}


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


def _json(value: str | None) -> dict[str, Any]:
    return json.loads(value) if value else {}


def _span(row: sqlite3.Row, prefix: str = "") -> dict[str, int] | None:
    start = row[f"{prefix}start_line"]
    end = row[f"{prefix}end_line"]
    if start is None or end is None:
        return None
    result = {"startLine": int(start), "endLine": int(end)}
    if row[f"{prefix}start_column"] is not None:
        result["startColumn"] = int(row[f"{prefix}start_column"])
    if row[f"{prefix}end_column"] is not None:
        result["endColumn"] = int(row[f"{prefix}end_column"])
    return result


def _node_payload(row: sqlite3.Row, metric: sqlite3.Row | None = None) -> dict[str, Any]:
    payload = {
        "nodeId": str(row["node_id"]),
        "kind": str(row["kind"]),
        "qualifiedName": str(row["qualified_name"]),
        "displayName": str(row["display_name"]),
        "sourceLocator": row["source_locator"],
        "contentHash": row["content_hash"],
        "summary": row["summary"],
        **({"signature": row["signature"]} if row["signature"] else {}),
        **({"role": row["role"]} if row["role"] else {}),
        **({"sourceSpan": value} if (value := _span(row)) else {}),
        "metadata": _json(row["metadata_json"]),
    }
    if metric is not None:
        payload["metrics"] = {
            "fanIn": int(metric["fan_in"]),
            "fanOut": int(metric["fan_out"]),
            "afferentCoupling": int(metric["afferent_coupling"]),
            "efferentCoupling": int(metric["efferent_coupling"]),
            "instability": float(metric["instability"]),
            **({"cycleId": metric["cycle_id"], "cycleSize": int(metric["cycle_size"])} if metric["cycle_id"] else {}),
        }
    return payload


def _edge_payload(row: sqlite3.Row) -> dict[str, Any]:
    span = _span(row)
    return {
        "edgeId": str(row["edge_id"]),
        "sourceNodeId": str(row["source_node_id"]),
        "targetNodeId": str(row["target_node_id"]),
        "type": str(row["type"]),
        "resolution": str(row["resolution"]),
        "resolver": str(row["resolver"]),
        **({"confidence": float(row["confidence"])} if row["confidence"] is not None else {}),
        "evidence": {
            **({"occurrenceId": row["occurrence_id"]} if row["occurrence_id"] else {}),
            **({"fileId": row["file_id"]} if row["file_id"] else {}),
            **({"sourceSpan": span} if span else {}),
            **({"sourceHash": row["evidence_hash"]} if row["evidence_hash"] else {}),
        },
        "metadata": _json(row["metadata_json"]),
    }


def _find_nodes(connection: sqlite3.Connection, selectors: list[str], query: str | None = None) -> list[str]:
    found: list[str] = []
    for selector in selectors:
        rows = connection.execute(
            """
            SELECT node_id FROM nodes
            WHERE node_id = ? OR qualified_name = ? OR display_name = ?
            ORDER BY CASE WHEN node_id = ? THEN 0 WHEN qualified_name = ? THEN 1 ELSE 2 END, qualified_name
            LIMIT 12
            """,
            (selector, selector, selector, selector, selector),
        )
        found.extend(str(row["node_id"]) for row in rows)
    if query and not found:
        terms = [term.casefold() for term in re.findall(r"[\w.:-]+", query, flags=re.UNICODE) if len(term) > 1]
        if terms:
            clauses = " OR ".join("lower(qualified_name) LIKE ? OR lower(display_name) LIKE ? OR lower(summary) LIKE ?" for _ in terms)
            values: list[str] = []
            for term in terms:
                pattern = f"%{term}%"
                values.extend([pattern, pattern, pattern])
            rows = connection.execute(
                f"SELECT node_id FROM nodes WHERE {clauses} ORDER BY kind, qualified_name LIMIT 12",
                values,
            )
            found.extend(str(row["node_id"]) for row in rows)
    output: list[str] = []
    seen: set[str] = set()
    for node_id in found:
        if node_id not in seen:
            seen.add(node_id)
            output.append(node_id)
    return output


def _eligible_edges(
    connection: sqlite3.Connection,
    edge_types: set[str],
    include_possible: bool,
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in edge_types)
    resolutions = sorted(HARD_RESOLUTIONS | ({EdgeResolution.POSSIBLE.value} if include_possible else set()))
    resolution_placeholders = ",".join("?" for _ in resolutions)
    return list(
        connection.execute(
            f"SELECT * FROM edges WHERE type IN ({placeholders}) AND resolution IN ({resolution_placeholders}) ORDER BY type,edge_id",
            [*sorted(edge_types), *resolutions],
        )
    )


def _traverse(
    seeds: list[str],
    edges: list[sqlite3.Row],
    *,
    direction: str,
    max_hops: int,
    max_nodes: int,
    targets: set[str] | None = None,
) -> tuple[list[str], dict[str, list[str]], dict[str, sqlite3.Row]]:
    adjacency: dict[str, list[tuple[str, sqlite3.Row]]] = defaultdict(list)
    by_edge_id: dict[str, sqlite3.Row] = {}
    for edge in edges:
        edge_id = str(edge["edge_id"])
        by_edge_id[edge_id] = edge
        if direction in {"outgoing", "both"}:
            adjacency[str(edge["source_node_id"])].append((str(edge["target_node_id"]), edge))
        if direction in {"incoming", "both"}:
            adjacency[str(edge["target_node_id"])].append((str(edge["source_node_id"]), edge))
    queue = deque((seed, 0, []) for seed in seeds)
    visited: set[str] = set()
    ordered: list[str] = []
    paths: dict[str, list[str]] = {}
    while queue and len(ordered) < max_nodes:
        node_id, depth, path = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        ordered.append(node_id)
        paths[node_id] = path
        if targets and node_id in targets:
            continue
        if depth >= max_hops:
            continue
        for neighbor, edge in adjacency.get(node_id, []):
            if neighbor not in visited:
                queue.append((neighbor, depth + 1, [*path, str(edge["edge_id"])]))
    return ordered, paths, by_edge_id


def _metric_map(connection: sqlite3.Connection, node_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not node_ids:
        return {}
    placeholders = ",".join("?" for _ in node_ids)
    return {
        str(row["node_id"]): row
        for row in connection.execute(f"SELECT * FROM metrics WHERE node_id IN ({placeholders})", node_ids)
    }


def _is_public_api(node: dict[str, Any]) -> bool:
    if node["kind"] not in {CodeNodeKind.FUNCTION.value, CodeNodeKind.METHOD.value, CodeNodeKind.CLASS.value}:
        return False
    decorators = [str(value).casefold() for value in node.get("metadata", {}).get("decorators", [])]
    route_markers = (".get", ".post", ".put", ".patch", ".delete", ".route", "endpoint", "command")
    return not node["displayName"].startswith("_") and (not decorators or any(marker in value for value in decorators for marker in route_markers))


def query_code_graph(
    workspace_id: str,
    *,
    query: str | None = None,
    intent: str = "lookup",
    symbols: list[str] | None = None,
    targets: list[str] | None = None,
    max_hops: int = 2,
    max_nodes: int = 12,
    max_evidence_blocks: int = 3,
    include_possible: bool = False,
    expected_generation: str | None = None,
    max_navigation_tokens: int | None = None,
    max_navigation_bytes: int | None = None,
    token_counter: TokenCounter | None = None,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    """Return a bounded proof-carrying repository graph slice."""

    if intent not in QUERY_INTENTS:
        raise CodeGraphError("CODE_GRAPH_INTENT_INVALID", f"Unknown code graph intent: {intent}")
    if max_hops < 0 or max_hops > 5 or max_nodes < 1 or max_nodes > 100 or not 1 <= max_evidence_blocks <= 3:
        raise CodeGraphError("CODE_GRAPH_LIMIT_INVALID", "Code graph query limits are outside the supported range.")
    if max_navigation_tokens is not None and token_counter is None:
        raise CodeGraphError("TOKEN_COUNTER_UNAVAILABLE", "A real token counter is required for a token hard cap.")
    connection = open_code_graph(workspace_id, store_dir)
    try:
        metadata = _metadata(connection)
        generation = metadata.get("active_generation")
        if expected_generation is not None and expected_generation != generation:
            raise CodeGraphError("CODE_GRAPH_STALE", "Code graph generation changed before query.")
        selector_values = list(dict.fromkeys(symbols or []))
        seed_ids = _find_nodes(connection, selector_values, query)
        target_ids = _find_nodes(connection, list(dict.fromkeys(targets or [])))
        if intent == "trace" and (not seed_ids or not target_ids):
            raise CodeGraphError("CODE_GRAPH_TRACE_ENDPOINTS_REQUIRED", "Trace requires resolvable source and target symbols.")

        paths: dict[str, list[str]] = {}
        edge_rows: dict[str, sqlite3.Row] = {}
        change_payload: list[dict[str, Any]] = []
        if intent == "diff":
            changes = list(connection.execute("SELECT * FROM changes ORDER BY entity_type,change_type,entity_id"))
            change_payload = [
                {
                    "changeType": str(row["change_type"]),
                    "entityType": str(row["entity_type"]),
                    "entityId": str(row["entity_id"]),
                    **({"before": json.loads(row["before_json"])} if row["before_json"] else {}),
                    **({"after": json.loads(row["after_json"])} if row["after_json"] else {}),
                }
                for row in changes[:max_nodes]
            ]
            selected_ids = [
                str(row["entity_id"])
                for row in changes
                if row["entity_type"] == "node" and row["change_type"] != "removed"
            ][:max_nodes]
        elif intent == "cycles":
            rows = connection.execute(
                "SELECT node_id FROM metrics WHERE cycle_id IS NOT NULL ORDER BY cycle_id,node_id LIMIT ?",
                (max_nodes,),
            )
            selected_ids = [str(row["node_id"]) for row in rows]
        elif intent == "overview":
            rows = connection.execute(
                "SELECT node_id FROM metrics ORDER BY cycle_size DESC, (fan_in+fan_out) DESC, node_id LIMIT ?",
                (max_nodes,),
            )
            selected_ids = [str(row["node_id"]) for row in rows]
        else:
            if not seed_ids:
                selected_ids = []
            else:
                if intent == "dependencies":
                    edge_types, direction = DEPENDENCY_EDGE_TYPES, "outgoing"
                elif intent == "callers":
                    edge_types, direction = CALL_EDGE_TYPES, "incoming"
                elif intent == "callees":
                    edge_types, direction = CALL_EDGE_TYPES, "outgoing"
                elif intent == "impact":
                    edge_types, direction = IMPACT_EDGE_TYPES, "incoming"
                elif intent == "trace":
                    edge_types, direction = TRACE_EDGE_TYPES, "outgoing"
                else:
                    edge_types, direction = TRACE_EDGE_TYPES | {CodeEdgeType.CONTAINS.value}, "outgoing"
                eligible = _eligible_edges(connection, edge_types, include_possible)
                selected_ids, paths, edge_rows = _traverse(
                    seed_ids,
                    eligible,
                    direction=direction,
                    max_hops=0 if intent == "lookup" else max_hops,
                    max_nodes=max_nodes,
                    targets=set(target_ids) if intent == "trace" else None,
                )
        if selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            rows_by_id = {
                str(row["node_id"]): row
                for row in connection.execute(f"SELECT * FROM nodes WHERE node_id IN ({placeholders})", selected_ids)
            }
        else:
            rows_by_id = {}
        metrics = _metric_map(connection, selected_ids)
        nodes = [_node_payload(rows_by_id[node_id], metrics.get(node_id)) for node_id in selected_ids if node_id in rows_by_id]
        enrichment_provider = metadata.get("summary_enrichment_provider")
        enrichment_version = metadata.get("summary_enrichment_version")
        if enrichment_provider and enrichment_version:
            for node in nodes:
                enriched = connection.execute(
                    """
                    SELECT summary FROM enrichments
                    WHERE node_id=? AND provider=? AND provider_version=? AND source_hash=?
                    """,
                    (
                        node["nodeId"],
                        enrichment_provider,
                        enrichment_version,
                        node.get("contentHash"),
                    ),
                ).fetchone()
                if enriched:
                    node["enrichedSummary"] = str(enriched["summary"])
                    node["summaryEnrichment"] = {
                        "provider": enrichment_provider,
                        "version": enrichment_version,
                        "authoritative": False,
                    }
        used_edge_ids = list(dict.fromkeys(edge_id for node_id in selected_ids for edge_id in paths.get(node_id, [])))
        edges = [_edge_payload(edge_rows[value]) for value in used_edge_ids if value in edge_rows]
        proof_paths = [
            {"nodeId": node_id, "edgeIds": paths[node_id]}
            for node_id in selected_ids
            if paths.get(node_id)
        ]
        readable = [
            node["nodeId"]
            for node in nodes
            if node.get("sourceLocator")
            and node["kind"] in {
                CodeNodeKind.METHOD.value,
                CodeNodeKind.FUNCTION.value,
                CodeNodeKind.CLASS.value,
                CodeNodeKind.FILE.value,
                CodeNodeKind.MODULE.value,
            }
        ][:max_evidence_blocks]
        file_ids = [str(rows_by_id[node_id]["file_id"]) for node_id in selected_ids if node_id in rows_by_id and rows_by_id[node_id]["file_id"]]
        if file_ids:
            placeholders = ",".join("?" for _ in set(file_ids))
            blindspots = list(
                connection.execute(
                    f"SELECT * FROM blindspots WHERE file_id IN ({placeholders}) ORDER BY source_locator,start_line,code LIMIT 50",
                    sorted(set(file_ids)),
                )
            )
        else:
            blindspots = []
        uncertainty = {
            "count": len(blindspots),
            "items": [
                {
                    "code": str(row["code"]),
                    "sourceLocator": str(row["source_locator"]),
                    "expression": str(row["expression"]),
                    **({"sourceSpan": value} if (value := _span(row)) else {}),
                    "metadata": _json(row["metadata_json"]),
                }
                for row in blindspots
            ],
        }
        result: dict[str, Any] = {
            "workspaceId": workspace_id,
            "generation": generation,
            "previousGeneration": metadata.get("previous_generation") or None,
            "sourceTreeHash": metadata.get("source_tree_hash"),
            "graphFreshness": "verified",
            "intent": intent,
            "includePossible": include_possible,
            "nodes": nodes,
            "edges": edges,
            "proofPaths": proof_paths,
            "uncertaintyReceipt": uncertainty,
            **({"changes": change_payload} if intent == "diff" else {}),
            **(
                {
                    "impactReceipt": {
                        "affectedPublicApis": [node["nodeId"] for node in nodes if _is_public_api(node)],
                        "candidateTests": [node["nodeId"] for node in nodes if node.get("role") == "test"],
                        "hardPathCount": sum(
                            1 for edge in edges if edge["resolution"] in HARD_RESOLUTIONS
                        ),
                        "possiblePathCount": sum(
                            1 for edge in edges if edge["resolution"] == EdgeResolution.POSSIBLE.value
                        ),
                    }
                }
                if intent == "impact"
                else {}
            ),
            "recommendedNext": [
                {
                    "tool": "documa_read_code_evidence",
                    "arguments": {
                        "workspace_id": workspace_id,
                        "block_ids": readable,
                        "expected_generation": generation,
                    },
                }
            ]
            if readable
            else [],
        }
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if max_navigation_tokens is not None and token_counter and token_counter.count(serialized) > max_navigation_tokens:
            raise CodeGraphError("CODE_GRAPH_NAVIGATION_BUDGET_EXCEEDED", "Code graph navigation exceeds the token budget.")
        if max_navigation_bytes is not None and len(serialized.encode("utf-8")) > max_navigation_bytes:
            raise CodeGraphError("CODE_GRAPH_NAVIGATION_BUDGET_EXCEEDED", "Code graph navigation exceeds the byte budget.")
        return result
    finally:
        connection.close()


def read_code_evidence(
    workspace_id: str,
    block_ids: list[str],
    *,
    expected_generation: str | None = None,
    total_max_tokens: int | None = None,
    total_max_bytes: int | None = None,
    token_counter: TokenCounter | None = None,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    """Read one to three source blocks and fail closed on any hash drift."""

    requested = list(dict.fromkeys(block_ids))
    if not requested or len(requested) > 3:
        raise CodeGraphError("CODE_GRAPH_BLOCK_LIMIT", "Read one to three code evidence blocks.")
    if total_max_tokens is not None and token_counter is None:
        raise CodeGraphError("TOKEN_COUNTER_UNAVAILABLE", "A real token counter is required for a token hard cap.")
    connection = open_code_graph(workspace_id, store_dir)
    try:
        metadata = _metadata(connection)
        generation = metadata.get("active_generation")
        if expected_generation is not None and expected_generation != generation:
            raise CodeGraphError("CODE_GRAPH_STALE", "Code graph generation changed before evidence read.")
        root = Path(metadata["workspace_root"]).resolve()
        placeholders = ",".join("?" for _ in requested)
        rows = list(connection.execute(f"SELECT * FROM nodes WHERE node_id IN ({placeholders})", requested))
        by_id = {str(row["node_id"]): row for row in rows}
        missing = [block_id for block_id in requested if block_id not in by_id]
        if missing:
            raise CodeGraphError("CODE_GRAPH_BLOCK_NOT_FOUND", f"Unknown code graph blocks: {', '.join(missing)}")
        evidence: list[dict[str, Any]] = []
        unread: list[str] = []
        spent_bytes = 0
        spent_tokens = 0
        for block_id in requested:
            row = by_id[block_id]
            locator = row["source_locator"]
            if not locator or row["kind"] in {CodeNodeKind.WORKSPACE.value, CodeNodeKind.PACKAGE.value, CodeNodeKind.EXTERNAL_MODULE.value, CodeNodeKind.EXTERNAL_SYMBOL.value}:
                raise CodeGraphError("CODE_GRAPH_BLOCK_UNREADABLE", f"Code graph node has no readable source: {block_id}")
            source = (root / str(locator)).resolve()
            if root not in source.parents or not source.is_file():
                raise CodeGraphError("CODE_GRAPH_SOURCE_INVALID", f"Code evidence source is missing or escapes the workspace: {locator}")
            raw = source.read_bytes()
            file_row = connection.execute("SELECT digest FROM files WHERE file_id = ?", (row["file_id"],)).fetchone()
            if file_row is None or sha256_bytes(raw) != file_row["digest"]:
                raise CodeGraphError("CODE_GRAPH_SOURCE_STALE", f"Code source changed after indexing: {locator}")
            text = raw.decode("utf-8")
            if row["kind"] in {CodeNodeKind.FILE.value, CodeNodeKind.MODULE.value}:
                body = text
                actual_hash = sha256_bytes(raw)
            else:
                lines = text.splitlines()
                body = "\n".join(lines[int(row["start_line"]) - 1 : int(row["end_line"])])
                actual_hash = sha256_text(body)
            if actual_hash != row["content_hash"]:
                raise CodeGraphError("CODE_GRAPH_BODY_HASH_MISMATCH", f"Code evidence body hash mismatch: {block_id}")
            body_bytes = len(body.encode("utf-8"))
            body_tokens = token_counter.count(body) if token_counter else None
            exceeds = (total_max_bytes is not None and spent_bytes + body_bytes > total_max_bytes) or (
                total_max_tokens is not None and spent_tokens + int(body_tokens or 0) > total_max_tokens
            )
            if exceeds:
                unread.append(block_id)
                continue
            evidence.append(
                {
                    "blockId": block_id,
                    "title": str(row["qualified_name"]),
                    "body": body,
                    "sourceLocator": str(locator),
                    "sourceSpan": _span(row),
                    "sourceFreshness": "fresh",
                    "authority": "developer",
                    "contentHash": actual_hash,
                    "bytes": body_bytes,
                    **({"tokens": body_tokens} if body_tokens is not None else {}),
                }
            )
            spent_bytes += body_bytes
            spent_tokens += int(body_tokens or 0)
        return {
            "workspaceId": workspace_id,
            "generation": generation,
            "sourceTreeHash": metadata.get("source_tree_hash"),
            "blocks": evidence,
            "totalBytes": spent_bytes,
            **({"totalTokens": spent_tokens} if token_counter else {}),
            "unreadBlockIds": unread,
        }
    finally:
        connection.close()


def code_context(
    workspace_id: str,
    query: str,
    *,
    intent: str = "lookup",
    symbols: list[str] | None = None,
    targets: list[str] | None = None,
    max_hops: int = 2,
    include_possible: bool = False,
    expected_generation: str | None = None,
    total_max_tokens: int | None = None,
    total_max_bytes: int | None = None,
    token_counter: TokenCounter | None = None,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    """One-call agent workflow: bounded navigation plus verified source evidence."""

    navigation = query_code_graph(
        workspace_id,
        query=query,
        intent=intent,
        symbols=symbols,
        targets=targets,
        max_hops=max_hops,
        max_nodes=12,
        max_evidence_blocks=3,
        include_possible=include_possible,
        expected_generation=expected_generation,
        token_counter=token_counter,
        store_dir=store_dir,
    )
    actions = navigation.get("recommendedNext") or []
    block_ids = actions[0]["arguments"]["block_ids"] if actions else []
    evidence = (
        read_code_evidence(
            workspace_id,
            block_ids,
            expected_generation=str(navigation["generation"]),
            total_max_tokens=total_max_tokens,
            total_max_bytes=total_max_bytes,
            token_counter=token_counter,
            store_dir=store_dir,
        )
        if block_ids
        else {"blocks": [], "totalBytes": 0, "unreadBlockIds": []}
    )
    navigation["evidence"] = evidence
    navigation["recommendedNext"] = []
    return navigation
