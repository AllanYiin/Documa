"""Disposable SQLite retrieval sidecar.

Citation truth stays in Documa IR.  This file contains only versioned,
rebuildable routing and ranking features.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any

from documa.core.block_scope import top_branch_id
from documa.core.ir import DocumentBlockType, DocumentIR
from documa.pipeline.block_tree import block_text_by_id
from documa.search import hnsw


SEARCH_INDEX_VERSION = 2
APPLICATION_ID = 0x444F4355  # "DOCU"
# route-path-v2: heading paths no longer repeat the document root title.
FEATURE_VERSION = "keyword-v3-provider-aware+ngram-newword-v2+simhash64+sketch-v1+route-path-v2+hnsw-route-v1"


def sidecar_path(ir_path: str | Path) -> Path:
    return Path(ir_path).with_name("documa.search.idx")

def _stable_simhash(text: str, bits: int = 64) -> int:
    import re

    tokens = re.findall(r"[a-z0-9_+\-]{2,}|[\u4e00-\u9fff]{2,}", text.casefold())
    weights = [0] * bits
    for token in tokens:
        digest = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=bits // 8).digest(), "big")
        for index in range(bits):
            weights[index] += 1 if digest & (1 << index) else -1
    return sum((1 << index) for index, value in enumerate(weights) if value >= 0)



def _is_cjk_term(term: str) -> bool:
    return bool(term) and all("\u4e00" <= char <= "\u9fff" for char in term)


def _retrieval_top_k(kind: DocumentBlockType) -> int:
    return {
        DocumentBlockType.PARAGRAPH: 6,
        DocumentBlockType.TABLE: 10,
        DocumentBlockType.SECTION: 12,
        DocumentBlockType.PAGE: 6,
        DocumentBlockType.DOCUMENT: 16,
    }.get(kind, 8)


def _select_retrieval_terms(
    term_frequency: dict[str, int],
    document_frequency: Counter[str],
    *,
    document_leaf_count: int,
    kind: DocumentBlockType,
    entropy_by_term: dict[str, float],
) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for term, frequency in term_frequency.items():
        idf = math.log(1.0 + (document_leaf_count + 0.5) / (document_frequency.get(term, 0) + 0.5))
        phrase_quality = 1.0 + min(len(term), 8) / 8.0
        boundary_entropy = 1.0 + min(1.0, entropy_by_term.get(term, 0.0) / 2.0)
        substring_novelty = 1.0 + min(1.0, len(term) / 8.0)
        ranked.append((frequency * idf * phrase_quality * boundary_entropy * substring_novelty, term))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[tuple[float, str]] = []
    for score, term in ranked:
        if _is_cjk_term(term) and any(
            term in longer and score <= longer_score * 1.25 for longer_score, longer in selected
        ):
            continue
        selected.append((score, term))
        if len(selected) >= _retrieval_top_k(kind):
            break
    return [term for _, term in selected]


def source_digest(document: DocumentIR) -> str:
    digest = hashlib.sha256()
    digest.update(document.id.encode("utf-8"))
    for block in sorted(document.document_blocks, key=lambda item: item.id):
        digest.update(block.id.encode("utf-8"))
        digest.update((block.content_hash or "").encode("ascii", errors="ignore"))
        digest.update("\0".join(block.source_block_ids).encode("utf-8"))
        keyword_input = {
            "provider": block.metadata.get("keyword_provider"),
            "provider_requested": block.metadata.get("keyword_provider_requested"),
            "keyword_terms": block.metadata.get("keyword_terms") or [],
            "new_word_terms": block.metadata.get("new_word_terms") or [],
            "search_terms": block.metadata.get("search_terms") or [],
        }
        digest.update(
            json.dumps(keyword_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
    return digest.hexdigest()


def keyword_provider_signature(document: DocumentIR) -> str:
    requested = sorted(
        {
            str(block.metadata.get("keyword_provider_requested"))
            for block in document.document_blocks
            if block.metadata.get("keyword_provider_requested")
        }
    )
    actual = sorted(
        {
            str(block.metadata.get("keyword_provider"))
            for block in document.document_blocks
            if block.metadata.get("keyword_provider")
        }
    )
    requested_label = "+".join(requested) or "unknown"
    actual_label = "+".join(actual) or "unknown"
    return f"requested-{requested_label}__actual-{actual_label}"


def _heading_path(block_id: str, by_id: dict[str, Any]) -> list[str]:
    path: list[str] = []
    current = by_id.get(block_id)
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        # Skip the document root title (the source name/path); consumers know
        # the document identity from their own envelope.
        if current.title and getattr(current.type, "value", "") != "document":
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _descendants(block: Any, by_id: dict[str, Any]) -> list[Any]:
    output: list[Any] = []
    pending = deque(block.child_ids)
    while pending:
        child = by_id.get(pending.popleft())
        if child is None:
            continue
        output.append(child)
        pending.extend(child.child_ids)
    return output


def deterministic_section_sketch(
    document: DocumentIR,
    block: Any,
    by_id: dict[str, Any],
    max_chars: int = 360,
    *,
    document_block_texts: dict[str, str] | None = None,
) -> str:
    """Extract one stable leading unit from each descendant leaf."""
    if document_block_texts is None:
        source_texts = block_text_by_id(document)
        document_block_texts = {
            item.id: "\n\n".join(
                source_texts[source_id].strip()
                for source_id in item.source_block_ids
                if source_texts.get(source_id, "").strip()
            )
            for item in document.document_blocks
        }
    units: list[str] = []
    for child in _descendants(block, by_id):
        if child.child_ids:
            continue
        text = " ".join(document_block_texts.get(child.id, "").split())
        if not text:
            continue
        cut = len(text)
        for marker in ("。", "！", "？", ". ", "! ", "? "):
            position = text.find(marker)
            if position >= 0:
                cut = min(cut, position + len(marker.rstrip()))
        unit = text[:cut].strip()
        if unit and unit not in units:
            units.append(unit)
        if len(" ".join(units)) >= max_chars:
            break
    return " ".join(units)[:max_chars]


def build_search_sidecar(document: DocumentIR, path: str | Path) -> dict[str, Any]:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.{uuid.uuid4().hex}.tmp")
    if temporary.exists():
        temporary.unlink()
    by_id = {block.id: block for block in document.document_blocks}
    source_texts = block_text_by_id(document)
    document_block_texts = {
        block.id: "\n\n".join(
            source_texts[source_id].strip()
            for source_id in block.source_block_ids
            if source_texts.get(source_id, "").strip()
        )
        for block in document.document_blocks
    }
    leaf_count = sum(1 for block in document.document_blocks if not block.child_ids)
    document_frequency: Counter[str] = Counter()
    for block in document.document_blocks:
        if block.child_ids:
            continue
        stats = block.metadata.get("keyword_stats") or {}
        terms = set((stats.get("term_freq") or {}).keys())
        if not terms:
            terms = set(block.metadata.get("search_terms") or [])
        document_frequency.update(terms)

    with sqlite3.connect(temporary) as connection:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {SEARCH_INDEX_VERSION}")
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY, parent_id TEXT, branch_id TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT, page_start INTEGER, page_end INTEGER,
                char_count INTEGER NOT NULL, content_hash TEXT, simhash TEXT NOT NULL,
                terms_json TEXT NOT NULL, features_json TEXT NOT NULL, sketch TEXT
            );
            CREATE TABLE term_stats (term TEXT PRIMARY KEY, document_frequency INTEGER NOT NULL);
            CREATE TABLE block_terms (
                block_id TEXT NOT NULL, term TEXT NOT NULL, term_frequency INTEGER NOT NULL,
                PRIMARY KEY (block_id, term)
            );
            CREATE TABLE routes (
                block_id TEXT PRIMARY KEY, parent_id TEXT, heading_path TEXT NOT NULL,
                page_start INTEGER, page_end INTEGER, subtree_cost INTEGER NOT NULL,
                terms_json TEXT NOT NULL, sketch TEXT NOT NULL
            );
            CREATE TABLE route_ann_nodes (
                block_id TEXT PRIMARY KEY, level INTEGER NOT NULL, vector BLOB NOT NULL
            );
            CREATE TABLE route_ann_edges (
                block_id TEXT NOT NULL, level INTEGER NOT NULL, neighbor_id TEXT NOT NULL,
                PRIMARY KEY (block_id, level, neighbor_id)
            );
            CREATE INDEX idx_blocks_branch ON blocks(branch_id);
            CREATE INDEX idx_block_terms_term ON block_terms(term);
            CREATE INDEX idx_route_ann_edges_node ON route_ann_edges(block_id, level);
            """
        )
        provider_signature = keyword_provider_signature(document)
        metadata = {
            "source_digest": source_digest(document),
            "document_id": document.id,
            "ir_version": document.ir_version,
            "feature_version": FEATURE_VERSION,
            "normalizer_version": "unicode-str-preserve-original-v1",
            "tokenizer_version": f"documa-{provider_signature}+ngram-newword-v2",
            "keyword_provider_signature": provider_signature,
            "leaf_count": str(leaf_count),
            "ann_vector_version": hnsw.VECTOR_VERSION,
            "ann_dimensions": str(hnsw.DIMENSIONS),
            "ann_m": str(hnsw.M),
            "ann_ef_construction": str(hnsw.EF_CONSTRUCTION),
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
        connection.executemany(
            "INSERT INTO term_stats(term, document_frequency) VALUES (?, ?)",
            sorted(document_frequency.items()),
        )
        ann_parts: dict[str, list[tuple[str, float]]] = {}
        for block in document.document_blocks:
            text = document_block_texts[block.id]
            stats = block.metadata.get("keyword_stats") or {}
            term_freq = {str(key): int(value) for key, value in (stats.get("term_freq") or {}).items()}
            entropy_by_term = {
                str(item.get("term")): min(
                    float(item.get("left_entropy") or 0.0),
                    float(item.get("right_entropy") or 0.0),
                )
                for item in block.metadata.get("new_word_terms") or []
                if item.get("term")
            }
            retrieval_terms = _select_retrieval_terms(
                term_freq, document_frequency, document_leaf_count=leaf_count, kind=block.type, entropy_by_term=entropy_by_term
            )
            terms = list(dict.fromkeys([block.title, *retrieval_terms, *(block.metadata.get("search_terms") or [])]))
            terms = [term for term in terms if term]
            sketch = ""
            if block.type in {DocumentBlockType.DOCUMENT, DocumentBlockType.SECTION}:
                sketch = deterministic_section_sketch(
                    document,
                    block,
                    by_id,
                    document_block_texts=document_block_texts,
                )
            pages = sorted(block.page_refs)
            features = {
                "depth": block.depth,
                "order_index": block.order_index,
                "keyword_top_k": len(block.metadata.get("keyword_terms") or []),
                "retrieval_terms": retrieval_terms,
                "document_idf": True,
                "cjk_substring_suppression": True,
            }
            connection.execute(
                "INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    block.id,
                    block.parent_id,
                    top_branch_id(block.id, by_id),
                    block.type.value,
                    block.title,
                    pages[0] if pages else None,
                    pages[-1] if pages else None,
                    len(text),
                    block.content_hash,
                    f"{_stable_simhash(' '.join([block.title or '', block.text_preview or '', text])):016x}",
                    json.dumps(terms, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(features, ensure_ascii=False, separators=(",", ":")),
                    sketch,
                ),
            )
            connection.executemany(
                "INSERT INTO block_terms(block_id, term, term_frequency) VALUES (?, ?, ?)",
                ((block.id, term, frequency) for term, frequency in sorted(term_freq.items())),
            )
            if block.type in {DocumentBlockType.DOCUMENT, DocumentBlockType.SECTION}:
                subtree_cost = sum(len(document_block_texts[child.id]) for child in _descendants(block, by_id))
                heading_path = " > ".join(_heading_path(block.id, by_id))
                connection.execute(
                    "INSERT INTO routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        block.id,
                        block.parent_id,
                        heading_path,
                        pages[0] if pages else None,
                        pages[-1] if pages else None,
                        max(1, subtree_cost // 4),
                        json.dumps(terms, ensure_ascii=False, separators=(",", ":")),
                        sketch,
                    ),
                )
                if block.parent_id is not None:
                    ann_parts[block.id] = [
                        (block.title or "", 2.5),
                        (heading_path, 2.0),
                        (" ".join(retrieval_terms), 2.0),
                        (" ".join(str(term) for term in block.metadata.get("search_terms") or []), 1.5),
                        (sketch, 0.35),
                    ]
        ann_vectors = {block_id: hnsw.vectorize(parts) for block_id, parts in ann_parts.items()}
        ann_vectors = {block_id: vector for block_id, vector in ann_vectors.items() if any(vector)}
        ann_levels, ann_edges, ann_entry_id, ann_max_level = hnsw.build(ann_vectors)
        connection.executemany(
            "INSERT INTO route_ann_nodes(block_id, level, vector) VALUES (?, ?, ?)",
            (
                (block_id, ann_levels[block_id], hnsw.pack(vector))
                for block_id, vector in ann_vectors.items()
            ),
        )
        connection.executemany(
            "INSERT INTO route_ann_edges(block_id, level, neighbor_id) VALUES (?, ?, ?)",
            (
                (block_id, level, neighbor_id)
                for (block_id, level), neighbor_ids in sorted(ann_edges.items())
                for neighbor_id in sorted(neighbor_ids)
            ),
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("ann_entry_block_id", ann_entry_id or ""),
                ("ann_max_level", str(max(0, ann_max_level))),
                ("ann_node_count", str(len(ann_vectors))),
            ),
        )
        connection.commit()
    connection.close()
    temporary.replace(output)
    return {
        "path": str(output),
        "version": SEARCH_INDEX_VERSION,
        "source_digest": source_digest(document),
        "block_count": len(document.document_blocks),
        "term_count": len(document_frequency),
        "ann_node_count": len(ann_vectors),
    }


def _valid_route_rows(index_path: Path, source_generation: str | None) -> list[sqlite3.Row]:
    """Route rows from a generation-matched sidecar, or [] when unusable."""
    if not index_path.exists():
        return []
    try:
        with sqlite3.connect(index_path) as connection:
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                return []
            if connection.execute("PRAGMA user_version").fetchone()[0] != SEARCH_INDEX_VERSION:
                return []
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if source_generation is not None and metadata.get("source_digest") != source_generation:
                return []
            return connection.execute("SELECT * FROM routes").fetchall()
    except sqlite3.Error:
        return []


def section_sketches(path: str | Path, *, source_generation: str | None = None) -> dict[str, dict[str, Any]]:
    """Precomputed per-section sketches and read costs, keyed by block id.

    Lets overview tools attach a one-glance summary per section without any
    block-body reads; the sketches were paid for once at ingest time.
    """
    return {
        row["block_id"]: {"sketch": row["sketch"], "subtree_cost": row["subtree_cost"]}
        for row in _valid_route_rows(Path(path), source_generation)
        if row["sketch"]
    }


def _hnsw_route_scores(
    index_path: Path,
    query_terms: list[str],
    *,
    source_generation: str | None,
    allowed_ids: set[str],
    limit: int,
) -> dict[str, float]:
    """Query persisted HNSW routing without models or token-counting calls."""
    query_vector = hnsw.vectorize([(" ".join(query_terms), 1.0)])
    if not any(query_vector) or not allowed_ids:
        return {}
    try:
        with sqlite3.connect(index_path) as connection:
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                return {}
            if connection.execute("PRAGMA user_version").fetchone()[0] != SEARCH_INDEX_VERSION:
                return {}
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if source_generation is not None and metadata.get("source_digest") != source_generation:
                return {}
            if metadata.get("ann_vector_version") != hnsw.VECTOR_VERSION:
                return {}
            entry_id = metadata.get("ann_entry_block_id") or ""
            if not entry_id:
                return {}
            max_level = int(metadata.get("ann_max_level") or 0)
            vector_cache: dict[str, hnsw.Vector] = {}
            edge_cache: dict[tuple[str, int], set[str]] = {}

            def vector_for(block_id: str) -> hnsw.Vector:
                if block_id not in vector_cache:
                    row = connection.execute(
                        "SELECT vector FROM route_ann_nodes WHERE block_id = ?", (block_id,)
                    ).fetchone()
                    if row is None:
                        raise KeyError(block_id)
                    vector_cache[block_id] = hnsw.unpack(row[0])
                return vector_cache[block_id]

            def edges_for(block_id: str, level: int) -> set[str]:
                key = (block_id, level)
                if key not in edge_cache:
                    edge_cache[key] = {
                        row[0]
                        for row in connection.execute(
                            "SELECT neighbor_id FROM route_ann_edges WHERE block_id = ? AND level = ?",
                            (block_id, level),
                        )
                    }
                return edge_cache[key]

            nearest = hnsw.search(
                query_vector,
                entry_id=entry_id,
                max_level=max_level,
                ef=max(hnsw.EF_SEARCH, limit * 4),
                vector_for=vector_for,
                edges_for=edges_for,
            )
            return {
                block_id: max(0.0, 1.0 - item_distance)
                for item_distance, block_id in nearest
                if block_id in allowed_ids and 1.0 - item_distance >= hnsw.MIN_SIMILARITY
            }
    except (KeyError, ValueError, sqlite3.Error, struct.error):
        return {}


def route_sections(
    path: str | Path,
    query_terms: list[str],
    *,
    source_generation: str | None = None,
    scope_block_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not query_terms:
        return []
    rows = _valid_route_rows(Path(path), source_generation)
    folded_terms = [term.casefold() for term in query_terms]
    eligible_rows = []
    lexical_scores: dict[str, float] = {}
    for row in rows:
        if row["parent_id"] is None:
            continue
        if scope_block_id and row["block_id"] != scope_block_id and row["parent_id"] != scope_block_id:
            continue
        eligible_rows.append(row)
        haystack = f'{row["heading_path"]} {row["terms_json"]} {row["sketch"]}'.casefold()
        matched = sum(1 for term in folded_terms if term in haystack)
        if matched:
            lexical_scores[row["block_id"]] = matched / len(folded_terms)

    best_lexical = max(lexical_scores.values(), default=0.0)
    ann_scores: dict[str, float] = {}
    if best_lexical < 1.0:
        ann_scores = _hnsw_route_scores(
            Path(path),
            folded_terms,
            source_generation=source_generation,
            allowed_ids={row["block_id"] for row in eligible_rows},
            limit=max(1, int(limit)),
        )

    ranked = []
    for row in eligible_rows:
        lexical_score = lexical_scores.get(row["block_id"], 0.0)
        ann_score = ann_scores.get(row["block_id"], 0.0)
        if lexical_score <= 0 and ann_score <= 0:
            continue
        source = (
            "lexical+hnsw"
            if lexical_score > 0 and ann_score > 0
            else ("lexical" if lexical_score > 0 else "hnsw")
        )
        ranked.append(
            {
                "block_id": row["block_id"],
                "score": round(max(lexical_score, ann_score), 6),
                "lexical_score": round(lexical_score, 6),
                "ann_score": round(ann_score, 6),
                "route_source": source,
                "page_range": [row["page_start"], row["page_end"]],
                "subtree_cost": row["subtree_cost"],
                "sketch": row["sketch"],
                "_rank_score": lexical_score * 2.0 + ann_score * 0.5,
            }
        )
    ranked.sort(key=lambda item: (-item["_rank_score"], item["subtree_cost"], item["block_id"]))
    for item in ranked:
        item.pop("_rank_score", None)
    return ranked[: max(1, int(limit))]
