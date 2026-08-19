"""Hash-bound lexical and graph navigation over a shared ContextIR."""

from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Protocol

from documa.context.models import ContextBlock, ContextIR, ContextRelation, RelationOrigin, sha256_text
from documa.core.query import parse_query


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ContextContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _LexicalRow:
    block: ContextBlock
    score: float
    matched_terms: list[str]


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _query_terms(query: str) -> list[str]:
    parsed = [_normalize(value) for value in parse_query(query).units if value.strip()]
    return list(dict.fromkeys(parsed))


def _snippet(body: str, terms: list[str], limit: int = 240) -> str:
    clean = " ".join(body.split())
    if not clean:
        return ""
    folded = _normalize(clean)
    positions = [folded.find(term) for term in terms if folded.find(term) >= 0]
    start = max(0, min(positions) - 48) if positions else 0
    value = clean[start : start + limit]
    return ("…" if start else "") + value + ("…" if start + limit < len(clean) else "")


class ContextService:
    def __init__(self, context: ContextIR, *, token_counter: TokenCounter | None = None):
        self.context = context
        self.token_counter = token_counter
        self.by_id = {block.block_id: block for block in context.blocks}
        if len(self.by_id) != len(context.blocks):
            raise ContextContractError("CONTEXT_DUPLICATE_BLOCK", "ContextIR block ids must be unique.")
        for block in context.blocks:
            if block.content_hash != sha256_text(block.body):
                raise ContextContractError("CONTEXT_BODY_HASH_MISMATCH", f"Body hash mismatch: {block.block_id}")
            if block.parent_id and block.parent_id not in self.by_id:
                raise ContextContractError("CONTEXT_PARENT_MISSING", f"Unknown parent block: {block.parent_id}")
            if any(value not in self.by_id for value in block.depends_on):
                raise ContextContractError("CONTEXT_DEPENDENCY_MISSING", f"Unknown dependency for block: {block.block_id}")
        for relation in context.relations:
            if relation.source_block_id not in self.by_id or relation.target_block_id not in self.by_id:
                raise ContextContractError("CONTEXT_RELATION_MISSING", "Context relation references an unknown block.")

    def _lexical(self, query: str) -> tuple[list[_LexicalRow], list[str]]:
        terms = _query_terms(query)
        if not terms:
            return [], []
        rows: list[_LexicalRow] = []
        for block in self.context.blocks:
            title = _normalize(block.title)
            body = _normalize(block.body)
            tags = _normalize(" ".join(block.route_tags))
            matched = [term for term in terms if term in title or term in body or term in tags]
            if not matched:
                continue
            score = 0.0
            for term in matched:
                score += (4.0 if term in title else 0.0) + (2.0 if term in tags else 0.0)
                count = body.count(term)
                score += 1.0 + math.log1p(count)
            score *= 0.5 + 0.5 * len(matched) / max(1, len(terms))
            rows.append(_LexicalRow(block, score, matched))
        rows.sort(key=lambda row: (-row.score, row.block.block_id))
        return rows, terms

    @staticmethod
    def _is_hard(relation: ContextRelation) -> bool:
        return relation.origin == RelationOrigin.EXTRACTED

    def _adjacency(
        self,
        *,
        direction: str,
        allow_semantic_edges: bool,
    ) -> dict[str, list[tuple[str, ContextRelation]]]:
        output: dict[str, list[tuple[str, ContextRelation]]] = defaultdict(list)
        for relation in self.context.relations:
            if not self._is_hard(relation) and not allow_semantic_edges:
                continue
            if direction in {"outgoing", "both"}:
                output[relation.source_block_id].append((relation.target_block_id, relation))
            if direction in {"incoming", "both"}:
                output[relation.target_block_id].append((relation.source_block_id, relation))
        for edges in output.values():
            edges.sort(key=lambda item: (not self._is_hard(item[1]), item[1].type, item[0]))
        return output

    def _expand(
        self,
        seeds: list[str],
        *,
        direction: str,
        max_hops: int,
        max_nodes: int,
        allow_semantic_edges: bool,
        targets: set[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        adjacency = self._adjacency(direction=direction, allow_semantic_edges=allow_semantic_edges)
        queue = deque((seed, 0, []) for seed in seeds if seed in self.by_id)
        best: dict[str, list[dict[str, Any]]] = {}
        visited: set[str] = set()
        while queue and len(visited) < max_nodes:
            block_id, depth, path = queue.popleft()
            if block_id in visited:
                continue
            visited.add(block_id)
            best[block_id] = path
            if targets and block_id in targets:
                break
            if depth >= max_hops:
                continue
            for neighbor, relation in adjacency.get(block_id, []):
                if neighbor in visited:
                    continue
                queue.append(
                    (
                        neighbor,
                        depth + 1,
                        [
                            *path,
                            {
                                "source": relation.source_block_id,
                                "target": relation.target_block_id,
                                "type": relation.type,
                                "origin": relation.origin.value,
                                "provenanceMethod": relation.metadata.get("method", "parser"),
                            },
                        ],
                    )
                )
        return best

    def search(
        self,
        query: str,
        *,
        expected_source_digest: str | None = None,
        route: str = "auto",
        intent: str | None = None,
        seed_block_ids: list[str] | None = None,
        target_block_ids: list[str] | None = None,
        direction: str | None = None,
        allow_semantic_edges: bool = False,
        max_hops: int = 1,
        max_graph_nodes: int = 12,
        max_evidence_blocks: int = 3,
        max_navigation_tokens: int | None = None,
        max_navigation_bytes: int | None = None,
    ) -> dict[str, Any]:
        if not query.strip():
            raise ContextContractError("CONTEXT_QUERY_REQUIRED", "Context query cannot be empty.")
        if max_hops < 0 or max_hops > 2 or max_graph_nodes < 1 or not 1 <= max_evidence_blocks <= 3:
            raise ContextContractError("CONTEXT_LIMIT_INVALID", "Context graph limits are outside the supported range.")
        if direction is not None and direction not in {"incoming", "outgoing", "both"}:
            raise ContextContractError("CONTEXT_DIRECTION_INVALID", f"Unknown graph direction: {direction}")
        if route not in {"auto", "lexical-first", "graph-first", "overview"}:
            raise ContextContractError("CONTEXT_ROUTE_INVALID", f"Unknown context route: {route}")
        rows, terms = self._lexical(query)
        resolved_intent = intent or ("overview" if "overview" in _normalize(query) or "概覽" in query else "lookup")
        if resolved_intent not in {"lookup", "explore", "impact", "trace", "overview"}:
            raise ContextContractError("CONTEXT_INTENT_INVALID", f"Unknown context intent: {resolved_intent}")
        if resolved_intent == "trace" and (not seed_block_ids or not target_block_ids):
            raise ContextContractError("CONTEXT_TRACE_ENDPOINTS_REQUIRED", "Trace requires seed and target block ids.")
        precision = "no-match" if not rows else "pass"
        if rows and len(terms) >= 3 and len(rows[0].matched_terms) <= 1:
            precision = "low-precision"
        digest_matches = expected_source_digest is None or expected_source_digest == self.context.source_digest
        graph_freshness = "verified" if digest_matches else "stale"
        relation_query = any(term in _normalize(query) for term in ("depend", "impact", "relation", "依賴", "影響", "關係", "路徑"))
        resolved_route = route
        if resolved_route == "auto":
            resolved_route = "overview" if resolved_intent == "overview" else (
                "graph-first" if resolved_intent in {"explore", "impact", "trace"} or relation_query else "lexical-first"
            )
        should_graph = digest_matches and (
            resolved_route in {"graph-first", "overview"} or precision in {"low-precision", "no-match"}
        )
        effective_direction = direction or ("incoming" if resolved_intent == "impact" else "outgoing")
        seeds = list(dict.fromkeys(seed_block_ids or [row.block.block_id for row in rows[:6]]))
        if resolved_intent == "overview" and not seeds:
            degree: dict[str, int] = defaultdict(int)
            for relation in self.context.relations:
                if self._is_hard(relation):
                    degree[relation.source_block_id] += 1
                    degree[relation.target_block_id] += 1
            seeds = [value for value, _ in sorted(degree.items(), key=lambda item: (-item[1], item[0]))[:3]]
        graph_paths: dict[str, list[dict[str, Any]]] = {}
        if should_graph and seeds:
            graph_paths = self._expand(
                seeds,
                direction=effective_direction,
                max_hops=max_hops,
                max_nodes=max_graph_nodes,
                allow_semantic_edges=allow_semantic_edges,
                targets=set(target_block_ids or []) if resolved_intent == "trace" else None,
            )
        score_by_id = {row.block.block_id: row for row in rows}
        candidate_ids = list(dict.fromkeys([row.block.block_id for row in rows] + list(graph_paths)))
        candidate_ids.sort(
            key=lambda block_id: (
                0 if block_id in score_by_id else 1,
                -(score_by_id[block_id].score if block_id in score_by_id else 0.0),
                len(graph_paths.get(block_id, [])),
                block_id,
            )
        )
        candidates: list[dict[str, Any]] = []
        for block_id in candidate_ids[:max_evidence_blocks]:
            block = self.by_id[block_id]
            lexical = score_by_id.get(block_id)
            path = graph_paths.get(block_id, [])
            reasons = []
            if lexical:
                reasons.append("LEXICAL_MATCH")
            if block_id in graph_paths:
                reasons.append("GRAPH_HARD_PATH" if all(step["origin"] == "EXTRACTED" for step in path) else "GRAPH_SOFT_CANDIDATE")
            candidates.append(
                {
                    "blockId": block.block_id,
                    "title": block.title,
                    "sourceLocator": block.source_locator,
                    **({"sourceSpan": _span(block)} if block.source_span else {}),
                    "sourceFreshness": "fresh" if digest_matches else "stale",
                    "authority": block.authority.value,
                    "snippet": _snippet(block.body, terms),
                    "matchedTerms": lexical.matched_terms if lexical else [],
                    "termCoverage": len(lexical.matched_terms) / max(1, len(terms)) if lexical else 0.0,
                    "reasons": reasons,
                    "graphPath": path,
                }
            )
        required = [block.block_id for block in self.context.blocks if block.pinned]
        warnings = [] if digest_matches else ["CONTEXT_GRAPH_STALE: source digest mismatch; lexical-only fallback used."]
        result = {
            "query": query,
            "route": resolved_route,
            "intent": resolved_intent,
            "graphFreshness": graph_freshness,
            "graphUsed": bool(graph_paths),
            "staleSourceLocators": [] if digest_matches else sorted({block.source_locator for block in self.context.blocks}),
            "precisionGate": precision,
            "candidates": candidates,
            "requiredBlockIds": required,
            "warnings": warnings,
            "recommendedNext": [
                {
                    "tool": "context_read_blocks",
                    "arguments": {"blockIds": [item["blockId"] for item in candidates], "requiredBlockIds": required},
                }
            ]
            if candidates
            else [],
        }
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if max_navigation_tokens is not None:
            if self.token_counter is None:
                raise ContextContractError("TOKEN_COUNTER_UNAVAILABLE", "A real token counter is required for a token hard cap.")
            if self.token_counter.count(serialized) > max_navigation_tokens:
                raise ContextContractError("CONTEXT_NAVIGATION_BUDGET_EXCEEDED", "Context navigation exceeds the token budget.")
        if max_navigation_bytes is not None and len(serialized.encode("utf-8")) > max_navigation_bytes:
            raise ContextContractError("CONTEXT_NAVIGATION_BUDGET_EXCEEDED", "Context navigation exceeds the byte budget.")
        return result

    def read_blocks(
        self,
        block_ids: list[str],
        *,
        required_block_ids: list[str] | None = None,
        expected_source_digest: str | None = None,
        total_max_tokens: int | None = None,
        total_max_bytes: int | None = None,
    ) -> dict[str, Any]:
        requested = list(dict.fromkeys(block_ids))
        if not requested or len(requested) > 3:
            raise ContextContractError("CONTEXT_BLOCK_LIMIT", "Read one to three candidate blocks.")
        required = list(dict.fromkeys(required_block_ids or []))
        selected = list(dict.fromkeys([*required, *requested]))
        missing = [block_id for block_id in selected if block_id not in self.by_id]
        if missing:
            raise ContextContractError("CONTEXT_BLOCK_NOT_FOUND", f"Unknown context blocks: {', '.join(missing)}")
        if expected_source_digest is not None and expected_source_digest != self.context.source_digest:
            raise ContextContractError("CONTEXT_SOURCE_STALE", "Context source digest changed before evidence read.")
        if total_max_tokens is not None and self.token_counter is None:
            raise ContextContractError("TOKEN_COUNTER_UNAVAILABLE", "A real token counter is required for a token hard cap.")
        evidence: list[dict[str, Any]] = []
        spent_tokens = 0
        spent_bytes = 0
        unread: list[str] = []
        required_set = set(required)
        for block_id in selected:
            block = self.by_id[block_id]
            body_bytes = len(block.body.encode("utf-8"))
            body_tokens = self.token_counter.count(block.body) if self.token_counter else None
            exceeds = (total_max_bytes is not None and spent_bytes + body_bytes > total_max_bytes) or (
                total_max_tokens is not None and spent_tokens + int(body_tokens or 0) > total_max_tokens
            )
            if exceeds:
                if block_id in required_set:
                    raise ContextContractError("CONTEXT_REQUIRED_BLOCK_BUDGET", "A required block cannot fit in the evidence budget.")
                unread.append(block_id)
                continue
            evidence.append(
                {
                    "blockId": block.block_id,
                    "title": block.title,
                    "body": block.body,
                    "sourceLocator": block.source_locator,
                    **({"sourceSpan": _span(block)} if block.source_span else {}),
                    "sourceFreshness": "fresh",
                    "authority": block.authority.value,
                    "contentHash": block.content_hash,
                    **({"tokens": body_tokens} if body_tokens is not None else {}),
                    "bytes": body_bytes,
                }
            )
            spent_bytes += body_bytes
            spent_tokens += int(body_tokens or 0)
        return {
            "blocks": evidence,
            **({"totalTokens": spent_tokens} if self.token_counter else {}),
            "totalBytes": spent_bytes,
            "unreadBlockIds": unread,
            "sourceTreeHash": self.context.source_digest,
        }


def _span(block: ContextBlock) -> dict[str, int]:
    assert block.source_span is not None
    return {
        "startLine": block.source_span.start_line,
        "endLine": block.source_span.end_line,
        **({"startColumn": block.source_span.start_column} if block.source_span.start_column is not None else {}),
        **({"endColumn": block.source_span.end_column} if block.source_span.end_column is not None else {}),
    }
