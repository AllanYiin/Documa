"""Disposable SQLite retrieval sidecar.

Citation truth stays in Documa IR.  This file contains only versioned,
rebuildable routing and ranking features.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from documa.core.block_scope import top_branch_id
from documa.core.ir import DocumentBlockType, DocumentIR
from documa.pipeline.block_tree import document_block_text


SEARCH_INDEX_VERSION = 1
APPLICATION_ID = 0x444F4355  # "DOCU"
FEATURE_VERSION = "keyword-v2+simhash64+sketch-v1"


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
    return digest.hexdigest()


def _heading_path(block_id: str, by_id: dict[str, Any]) -> list[str]:
    path: list[str] = []
    current = by_id.get(block_id)
    seen: set[str] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        if current.title:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _descendants(block: Any, by_id: dict[str, Any]) -> list[Any]:
    output: list[Any] = []
    pending = list(block.child_ids)
    while pending:
        child = by_id.get(pending.pop(0))
        if child is None:
            continue
        output.append(child)
        pending.extend(child.child_ids)
    return output


def deterministic_section_sketch(document: DocumentIR, block: Any, by_id: dict[str, Any], max_chars: int = 360) -> str:
    """Extract one stable leading unit from each descendant leaf."""
    units: list[str] = []
    for child in _descendants(block, by_id):
        if child.child_ids:
            continue
        text = " ".join(document_block_text(document, child).split())
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
            CREATE INDEX idx_blocks_branch ON blocks(branch_id);
            CREATE INDEX idx_block_terms_term ON block_terms(term);
            """
        )
        metadata = {
            "source_digest": source_digest(document),
            "document_id": document.id,
            "ir_version": document.ir_version,
            "feature_version": FEATURE_VERSION,
            "normalizer_version": "unicode-str-preserve-original-v1",
            "tokenizer_version": "documa-cjk-substring-v2",
            "leaf_count": str(leaf_count),
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
        connection.executemany(
            "INSERT INTO term_stats(term, document_frequency) VALUES (?, ?)",
            sorted(document_frequency.items()),
        )
        for block in document.document_blocks:
            text = document_block_text(document, block)
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
                sketch = deterministic_section_sketch(document, block, by_id)
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
                subtree_cost = sum(len(document_block_text(document, child)) for child in _descendants(block, by_id))
                connection.execute(
                    "INSERT INTO routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        block.id,
                        block.parent_id,
                        " > ".join(_heading_path(block.id, by_id)),
                        pages[0] if pages else None,
                        pages[-1] if pages else None,
                        max(1, subtree_cost // 4),
                        json.dumps(terms, ensure_ascii=False, separators=(",", ":")),
                        sketch,
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
    }


def route_sections(
    path: str | Path,
    query_terms: list[str],
    *,
    source_generation: str | None = None,
    scope_block_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    index_path = Path(path)
    if not index_path.exists() or not query_terms:
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
            rows = connection.execute("SELECT * FROM routes").fetchall()
    except sqlite3.Error:
        return []
    folded_terms = [term.casefold() for term in query_terms]
    ranked = []
    for row in rows:
        if row["parent_id"] is None:
            continue
        if scope_block_id and row["block_id"] != scope_block_id and row["parent_id"] != scope_block_id:
            continue
        haystack = f'{row["heading_path"]} {row["terms_json"]} {row["sketch"]}'.casefold()
        matched = sum(1 for term in folded_terms if term in haystack)
        if matched:
            ranked.append(
                {
                    "block_id": row["block_id"],
                    "score": matched / len(folded_terms),
                    "page_range": [row["page_start"], row["page_end"]],
                    "subtree_cost": row["subtree_cost"],
                    "sketch": row["sketch"],
                }
            )
    ranked.sort(key=lambda item: (-item["score"], item["subtree_cost"], item["block_id"]))
    return ranked[: max(1, int(limit))]
