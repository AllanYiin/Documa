"""Deterministic local HNSW primitives for disposable retrieval indexes.

The vectors in this module are lexical feature hashes.  Building or querying
one never invokes an embedding service, language model, or token counter.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import re
import struct
from collections.abc import Callable


VECTOR_VERSION = "local-feature-hash-v1"
DIMENSIONS = 192
M = 8
EF_CONSTRUCTION = 32
EF_SEARCH = 32
MIN_SIMILARITY = 0.08

Vector = tuple[float, ...]
Edges = dict[tuple[str, int], set[str]]


def _is_cjk(value: str) -> bool:
    return bool(value) and all("\u4e00" <= char <= "\u9fff" for char in value)


def _features(text: str) -> list[str]:
    output: list[str] = []
    for token in re.findall(r"[a-z0-9_+\-]+|[\u4e00-\u9fff]+", text.casefold()):
        output.append(f"w:{token}")
        if token.isdigit():
            # Exact numbers and identifiers must survive normalization by a
            # much longer section sketch; repeated accumulation gives them a
            # deterministic weight without a separate model.
            output.extend([f"n:{token}"] * 4)
        compact = token.replace("_", "").replace("-", "")
        if _is_cjk(compact):
            output.extend(f"c2:{compact[index:index + 2]}" for index in range(max(0, len(compact) - 1)))
        elif len(compact) >= 4:
            output.extend(f"c3:{compact[index:index + 3]}" for index in range(len(compact) - 2))
    return output


def vectorize(weighted_parts: list[tuple[str, float]]) -> Vector:
    """Return a normalized fixed-width vector made only from local text features."""
    values = [0.0] * DIMENSIONS
    for text, weight in weighted_parts:
        if not text or weight <= 0:
            continue
        for feature in _features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % DIMENSIONS
            values[bucket] += weight if digest[4] & 1 else -weight
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return tuple(values)
    return tuple(value / norm for value in values)


def pack(vector: Vector) -> bytes:
    return struct.pack(f"<{DIMENSIONS}f", *vector)


def unpack(value: bytes) -> Vector:
    return struct.unpack(f"<{DIMENSIONS}f", value)


def similarity(left: Vector, right: Vector) -> float:
    return sum(a * b for a, b in zip(left, right))


def distance(left: Vector, right: Vector) -> float:
    return 1.0 - similarity(left, right)


def _level(block_id: str) -> int:
    """Stable geometric level assignment, with probability 1 / M per level."""
    value = int.from_bytes(hashlib.blake2b(block_id.encode("utf-8"), digest_size=8).digest(), "big")
    level = 0
    while level < 8 and value % M == 0:
        level += 1
        value //= M
    return level


def _search_layer(
    query: Vector,
    entry_ids: list[str],
    *,
    level: int,
    ef: int,
    vector_for: Callable[[str], Vector],
    edges_for: Callable[[str, int], set[str]],
) -> list[tuple[float, str]]:
    visited = set(entry_ids)
    candidates: list[tuple[float, str]] = []
    nearest: list[tuple[float, str]] = []
    for block_id in entry_ids:
        item_distance = distance(query, vector_for(block_id))
        heapq.heappush(candidates, (item_distance, block_id))
        heapq.heappush(nearest, (-item_distance, block_id))
    while candidates:
        item_distance, block_id = heapq.heappop(candidates)
        if len(nearest) >= ef and item_distance > -nearest[0][0]:
            break
        for neighbor_id in edges_for(block_id, level):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            neighbor_distance = distance(query, vector_for(neighbor_id))
            if len(nearest) < ef or neighbor_distance < -nearest[0][0]:
                heapq.heappush(candidates, (neighbor_distance, neighbor_id))
                heapq.heappush(nearest, (-neighbor_distance, neighbor_id))
                if len(nearest) > ef:
                    heapq.heappop(nearest)
    return sorted((-negative_distance, block_id) for negative_distance, block_id in nearest)


def build(vectors: dict[str, Vector]) -> tuple[dict[str, int], Edges, str | None, int]:
    """Build a deterministic, bounded-degree, multi-layer HNSW graph."""
    levels: dict[str, int] = {}
    edges: Edges = {}
    entry_id: str | None = None
    previous_id: str | None = None
    protected_level_zero: set[frozenset[str]] = set()
    max_level = -1

    def vector_for(block_id: str) -> Vector:
        return vectors[block_id]

    def edges_for(block_id: str, level: int) -> set[str]:
        return edges.get((block_id, level), set())

    def prune(block_id: str, level: int) -> None:
        neighbors = edges[(block_id, level)]
        capacity = M * 2 if level == 0 else M
        if len(neighbors) <= capacity:
            return
        protected = {
            neighbor_id
            for neighbor_id in neighbors
            if level == 0 and frozenset((block_id, neighbor_id)) in protected_level_zero
        }
        ranked = sorted(
            neighbors - protected,
            key=lambda candidate_id: (distance(vectors[block_id], vectors[candidate_id]), candidate_id),
        )
        retained = protected | set(ranked[: max(0, capacity - len(protected))])
        removed = neighbors - retained
        edges[(block_id, level)] = retained
        for removed_id in removed:
            edges[(removed_id, level)].discard(block_id)

    for block_id in vectors:
        node_level = _level(block_id)
        levels[block_id] = node_level
        for layer in range(node_level + 1):
            edges[(block_id, layer)] = set()
        if entry_id is None:
            entry_id = block_id
            previous_id = block_id
            max_level = node_level
            continue

        # Standard HNSW permits a larger layer-0 degree.  Retaining one
        # document-order link per insertion prevents a distant but valid
        # section from being pruned into an unreachable component.
        if previous_id is not None:
            edges[(block_id, 0)].add(previous_id)
            edges[(previous_id, 0)].add(block_id)
            protected_level_zero.add(frozenset((block_id, previous_id)))
            prune(previous_id, 0)
            prune(block_id, 0)

        current = entry_id
        for layer in range(max_level, node_level, -1):
            current = _search_layer(
                vectors[block_id],
                [current],
                level=layer,
                ef=1,
                vector_for=vector_for,
                edges_for=edges_for,
            )[0][1]
        for layer in range(min(node_level, max_level), -1, -1):
            nearest = _search_layer(
                vectors[block_id],
                [current],
                level=layer,
                ef=EF_CONSTRUCTION,
                vector_for=vector_for,
                edges_for=edges_for,
            )
            for neighbor_id in [candidate_id for _, candidate_id in nearest[:M]]:
                edges[(block_id, layer)].add(neighbor_id)
                edges[(neighbor_id, layer)].add(block_id)
                prune(neighbor_id, layer)
            prune(block_id, layer)
            if nearest:
                current = nearest[0][1]
        if node_level > max_level:
            entry_id = block_id
            max_level = node_level
        previous_id = block_id
    return levels, edges, entry_id, max_level


def search(
    query: Vector,
    *,
    entry_id: str,
    max_level: int,
    ef: int,
    vector_for: Callable[[str], Vector],
    edges_for: Callable[[str, int], set[str]],
) -> list[tuple[float, str]]:
    """Search an HNSW graph and return ascending cosine distances."""
    current = entry_id
    for layer in range(max_level, 0, -1):
        current = _search_layer(
            query,
            [current],
            level=layer,
            ef=1,
            vector_for=vector_for,
            edges_for=edges_for,
        )[0][1]
    return _search_layer(
        query,
        [current],
        level=0,
        ef=max(1, ef),
        vector_for=vector_for,
        edges_for=edges_for,
    )
