"""SQLite-backed collection index for local multi-document block search."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from documa.collections import registry as registry_store
from documa.core import snippet_windows
from documa.core.query import parse_query
from documa.core.serialization import document_from_plain_data
from documa.pipeline.block_tree import document_block_text
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata

# Version 2: FTS columns store CJK text with per-character segmentation so the
# unicode61 tokenizer can match CJK sub-phrases.
# Version 3: documents rows carry block_count/page_count aggregates that feed
# grouped search rollups. Outdated indexes are reported stale by
# store_collection_health and must be rebuilt via build_collection_index.
INDEX_VERSION = "3"
DEFAULT_COLLECTION_ID = "default"
INDEX_FILENAME = "collection_index.sqlite3"

ToolPayload = dict[str, Any]

_CJK_CHAR_RE = re.compile(r"([぀-ヿ㐀-䶿一-鿿豈-﫿])")
_FTS_TERM_RE = re.compile(r"[\w぀-ヿ㐀-䶿一-鿿豈-﫿]+", re.UNICODE)


def _fts_segment(text: str) -> str:
    """Space-pad each CJK character so unicode61 indexes it as its own token.

    unicode61 treats a contiguous CJK run as one token, which makes CJK
    sub-phrase queries unmatchable. Per-character tokens plus FTS5 phrase
    queries restore substring semantics for CJK while leaving non-CJK text
    untouched.
    """
    return _CJK_CHAR_RE.sub(r" \1 ", text)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def index_path(store_dir: str | Path) -> Path:
    return Path(store_dir) / INDEX_FILENAME


def _connect(store_dir: str | Path) -> sqlite3.Connection:
    path = index_path(store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Concurrent ingests serialize on the write lock instead of erroring.
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS collections (
          collection_id TEXT PRIMARY KEY,
          index_version TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
          collection_id TEXT NOT NULL,
          registry_document_id TEXT NOT NULL,
          ir_document_id TEXT,
          source_name TEXT,
          source_path TEXT,
          content_hash TEXT,
          ir_path TEXT NOT NULL,
          status TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          block_count INTEGER,
          page_count INTEGER,
          PRIMARY KEY (collection_id, registry_document_id)
        );
        CREATE TABLE IF NOT EXISTS blocks (
          rowid INTEGER PRIMARY KEY,
          collection_id TEXT NOT NULL,
          registry_document_id TEXT NOT NULL,
          ir_document_id TEXT,
          source_name TEXT,
          block_id TEXT NOT NULL,
          block_type TEXT,
          title TEXT,
          heading_path_json TEXT NOT NULL,
          text_preview TEXT,
          body TEXT,
          keywords_json TEXT NOT NULL,
          page_refs_json TEXT NOT NULL,
          bbox_refs_json TEXT NOT NULL,
          citation_string TEXT,
          dedupe_key TEXT,
          order_index INTEGER,
          created_at TEXT NOT NULL,
          UNIQUE (collection_id, registry_document_id, block_id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(
          collection_id UNINDEXED,
          registry_document_id UNINDEXED,
          block_id UNINDEXED,
          title,
          heading_path,
          preview,
          body,
          keywords
        );
        """
    )
    _ensure_documents_columns(conn)


def _ensure_documents_columns(conn: sqlite3.Connection) -> None:
    """Additively migrate pre-v3 documents tables so old indexes stay queryable.

    store_collection_health flags version-outdated indexes as stale, which
    guides a full rebuild; until then searches must not crash on missing
    aggregate columns.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    for column in ("block_count", "page_count"):
        if column not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {column} INTEGER")


def _load_document(store: Path, entry: dict[str, Any]):
    ir_path = store / Path(PurePosixPath(entry["ir_path"]))
    payload = json.loads(ir_path.read_text(encoding="utf-8"))
    return document_from_plain_data(payload), str(ir_path)


def _block_path(block_id: str, by_id: dict[str, Any]) -> list[str]:
    path: list[str] = []
    current = by_id.get(block_id)
    while current is not None:
        if current.title:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _keywords(block: Any) -> list[str]:
    metadata = block.metadata or {}
    terms = metadata.get("search_terms") or metadata.get("keyword_terms") or []
    return [str(term) for term in terms if str(term).strip()]


def _citation_string(source_name: str, citation_meta: dict[str, Any]) -> str:
    label = str(citation_meta.get("citation_label") or "").strip()
    return f"[{source_name}, {label}]" if label else f"[{source_name}]"


def _record_rows(store: Path, collection_id: str, entry: dict[str, Any], now: str) -> tuple[list[dict[str, Any]], int]:
    """Build block rows for one registry document; also returns its page count."""
    document, _ = _load_document(store, entry)
    page_citations = ensure_page_citation_map(document)
    by_id = {block.id: block for block in document.document_blocks}
    rows = []
    for block in document.document_blocks:
        body = document_block_text(document, block)
        heading_path = _block_path(block.id, by_id)
        citation_meta = page_citation_metadata(block.page_refs, page_citations)
        keywords = _keywords(block)
        source_name = entry.get("source_name") or document.source_name
        rows.append(
            {
                "collection_id": collection_id,
                "registry_document_id": entry["document_id"],
                "ir_document_id": entry.get("ir_document_id") or document.id,
                "source_name": source_name,
                "block_id": block.id,
                "block_type": block.type.value,
                "title": block.title or "",
                "heading_path_json": _json(heading_path),
                "heading_path_text": " > ".join(heading_path),
                "text_preview": block.text_preview or "",
                "body": body,
                "keywords_json": _json(keywords),
                "keywords_text": " ".join(keywords),
                "page_refs_json": _json(block.page_refs),
                "bbox_refs_json": _json(block.bbox_refs),
                "citation_string": _citation_string(source_name, citation_meta),
                "dedupe_key": block.content_hash or "",
                "order_index": block.order_index,
                "created_at": now,
            }
        )
    return rows, len(document.pages)


def _active_registry_entries(store_dir: Path) -> list[dict[str, Any]]:
    registry = registry_store.load_registry(store_dir)
    if registry.get("code") == "REGISTRY_CORRUPTED":
        raise ValueError(registry.get("message", "registry is corrupted"))
    return [entry for entry in registry.get("documents", []) if entry.get("status", "active") == "active"]


# Active-entry maps cached by (registry.json path, mtime_ns, size): the key
# self-invalidates on every registry write. Cached maps are shared read-only
# state — callers must never mutate them.
_REGISTRY_MAP_CACHE: OrderedDict[tuple[str, int, int], dict[str, dict[str, Any]]] = OrderedDict()
_REGISTRY_MAP_CACHE_MAX_ENTRIES = 4


def clear_registry_entry_cache() -> None:
    """Drop cached active-entry maps (used by tests)."""
    _REGISTRY_MAP_CACHE.clear()


def _active_registry_entry_map(store_dir: Path) -> dict[str, dict[str, Any]]:
    registry_path = Path(store_dir) / "registry.json"
    try:
        stat = registry_path.stat()
    except OSError:
        # Missing registry: load_registry returns an empty default cheaply.
        return {entry["document_id"]: entry for entry in _active_registry_entries(store_dir)}
    cache_key = (str(registry_path.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _REGISTRY_MAP_CACHE.get(cache_key)
    if cached is not None:
        _REGISTRY_MAP_CACHE.move_to_end(cache_key)
        return cached
    entry_map = {entry["document_id"]: entry for entry in _active_registry_entries(store_dir)}
    _REGISTRY_MAP_CACHE[cache_key] = entry_map
    while len(_REGISTRY_MAP_CACHE) > _REGISTRY_MAP_CACHE_MAX_ENTRIES:
        _REGISTRY_MAP_CACHE.popitem(last=False)
    return entry_map


def _insert_document_rows(
    conn: sqlite3.Connection,
    store: Path,
    collection_id: str,
    entry: dict[str, Any],
    now: str,
) -> int:
    """Insert one registry document's documents/blocks/blocks_fts rows.

    Shared by the full rebuild and the incremental upsert; returns the number
    of block rows inserted.
    """
    rows, page_count = _record_rows(store, collection_id, entry, now)
    conn.execute(
        """
        INSERT INTO documents(
          collection_id, registry_document_id, ir_document_id, source_name,
          source_path, content_hash, ir_path, status, updated_at,
          block_count, page_count
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collection_id,
            entry["document_id"],
            entry.get("ir_document_id"),
            entry.get("source_name"),
            entry.get("source_path"),
            entry.get("content_hash"),
            entry["ir_path"],
            entry.get("status", "active"),
            now,
            len(rows),
            page_count,
        ),
    )
    for row in rows:
        cursor = conn.execute(
            """
            INSERT INTO blocks(
              collection_id, registry_document_id, ir_document_id, source_name,
              block_id, block_type, title, heading_path_json, text_preview, body,
              keywords_json, page_refs_json, bbox_refs_json, citation_string,
              dedupe_key, order_index, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["collection_id"],
                row["registry_document_id"],
                row["ir_document_id"],
                row["source_name"],
                row["block_id"],
                row["block_type"],
                row["title"],
                row["heading_path_json"],
                row["text_preview"],
                row["body"],
                row["keywords_json"],
                row["page_refs_json"],
                row["bbox_refs_json"],
                row["citation_string"],
                row["dedupe_key"],
                row["order_index"],
                row["created_at"],
            ),
        )
        conn.execute(
            """
            INSERT INTO blocks_fts(
              rowid, collection_id, registry_document_id, block_id,
              title, heading_path, preview, body, keywords
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                row["collection_id"],
                row["registry_document_id"],
                row["block_id"],
                _fts_segment(row["title"]),
                _fts_segment(row["heading_path_text"]),
                _fts_segment(row["text_preview"]),
                _fts_segment(row["body"]),
                _fts_segment(row["keywords_text"]),
            ),
        )
    return len(rows)


def build_collection_index(
    store_dir: str | Path = registry_store.DEFAULT_STORE_DIR,
    collection_id: str = DEFAULT_COLLECTION_ID,
) -> ToolPayload:
    """Rebuild the local collection index from active registry documents."""

    store = Path(store_dir)
    now = _now_iso()
    try:
        entries = _active_registry_entries(store)
        with _connect(store) as conn:
            _init_schema(conn)
            conn.execute("DELETE FROM blocks_fts WHERE collection_id = ?", (collection_id,))
            conn.execute("DELETE FROM blocks WHERE collection_id = ?", (collection_id,))
            conn.execute("DELETE FROM documents WHERE collection_id = ?", (collection_id,))
            conn.execute(
                """
                INSERT INTO collections(collection_id, index_version, created_at, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(collection_id) DO UPDATE SET
                  index_version = excluded.index_version,
                  updated_at = excluded.updated_at
                """,
                (collection_id, INDEX_VERSION, now, now),
            )

            block_count = 0
            for entry in entries:
                block_count += _insert_document_rows(conn, store, collection_id, entry, now)
            conn.commit()
    except (OSError, sqlite3.Error, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "code": "COLLECTION_INDEX_BUILD_FAILED", "message": str(exc)}

    return {
        "status": "ok",
        "collection_id": collection_id,
        "index_path": str(index_path(store)),
        "document_count": len(entries),
        "block_count": block_count,
        "index_version": INDEX_VERSION,
    }


def _fts_phrase(unit: str) -> str | None:
    """Convert one query unit (term or quoted phrase) into an FTS5 phrase.

    CJK characters are segmented exactly like the indexed text, so a CJK unit
    becomes a multi-token phrase that matches the same character sequence.
    """
    tokens = _FTS_TERM_RE.findall(_fts_segment(unit))
    if not tokens:
        return None
    joined = " ".join(tokens).replace('"', '""')
    return f'"{joined}"'


def _query_units(query: str) -> list[str]:
    """Raw query units — double-quoted phrases first, then bare terms.

    Shared by the FTS MATCH builder and snippet centering so snippets follow
    exactly what was searched.
    """
    return list(parse_query(query).units)


def _fts_query_variants(query: str) -> tuple[str, str]:
    """Build (all_units, any_unit) FTS5 MATCH expressions from a raw query.

    Double-quoted segments stay together as single phrase units; the remaining
    text contributes one unit per term. The all-units (AND) form is the primary
    query; the any-unit (OR) form is the precision fallback.
    """
    fts_units: list[str] = []
    for unit in _query_units(query):
        fts_unit = _fts_phrase(unit)
        if fts_unit and fts_unit not in fts_units:
            fts_units.append(fts_unit)
    return " AND ".join(fts_units), " OR ".join(fts_units)


def _snippet(row: sqlite3.Row, units: list[str]) -> str:
    """Query-centered snippet: the first field containing a query unit wins.

    A fixed-prefix snippet frequently misses the match entirely, so the window
    centers on the hit (CJK ±chars / ASCII ±words); the preview prefix is only
    a fallback when no unit appears in any field.
    """
    fields = [str(row[key] or "").strip() for key in ("text_preview", "body", "title")]
    fields = [field for field in fields if field]
    if not fields:
        return ""
    for text in fields:
        for unit in units:
            hits = snippet_windows.find_hits(text, unit)
            if hits:
                start, end = hits[0]
                return snippet_windows.make_snippet(text, start, end, unit, chars=40)
    return fields[0][:240]


def _row_to_result(row: sqlite3.Row, score: float, units: list[str]) -> dict[str, Any]:
    return {
        "registry_document_id": row["registry_document_id"],
        "ir_document_id": row["ir_document_id"],
        "source_name": row["source_name"],
        "block_id": row["block_id"],
        "block_type": row["block_type"],
        "heading_path": json.loads(row["heading_path_json"]),
        "score": score,
        "snippet": _snippet(row, units),
        "page_refs": json.loads(row["page_refs_json"]),
        "bbox_refs": json.loads(row["bbox_refs_json"]),
        "citation_string": row["citation_string"],
        "dedupe_key": row["dedupe_key"],
        "read_ref": {"ir_path": row["registry_document_id"], "block_id": row["block_id"]},
    }


def _delete_document_rows(conn: sqlite3.Connection, collection_id: str, registry_document_id: str) -> None:
    predicate = "collection_id = ? AND registry_document_id = ?"
    params = (collection_id, registry_document_id)
    conn.execute(f"DELETE FROM blocks_fts WHERE {predicate}", params)
    conn.execute(f"DELETE FROM blocks WHERE {predicate}", params)
    conn.execute(f"DELETE FROM documents WHERE {predicate}", params)


def _collection_row(conn: sqlite3.Connection, collection_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM collections WHERE collection_id = ?", (collection_id,)).fetchone()


def upsert_document_index(
    store_dir: str | Path = registry_store.DEFAULT_STORE_DIR,
    entry: dict[str, Any] | None = None,
    collection_id: str = DEFAULT_COLLECTION_ID,
    *,
    force: bool = False,
) -> ToolPayload:
    """Incrementally (re)index one registry document.

    Keeps the derived index coherent after every ingest without a full
    rebuild. Skips cheaply when no index exists yet, when the index schema
    version is outdated (repair path: build_collection_index), or when the
    indexed content hash already matches the entry.
    """
    store = Path(store_dir)
    if not entry or not entry.get("document_id"):
        return {"status": "error", "code": "REGISTRY_ENTRY_INVALID", "message": "A registry entry with document_id is required."}
    if not index_path(store).exists():
        return {"status": "skipped", "code": "COLLECTION_INDEX_NOT_FOUND", "collection_id": collection_id}
    document_id = entry["document_id"]
    now = _now_iso()
    try:
        with _connect(store) as conn:
            _init_schema(conn)
            collection = _collection_row(conn, collection_id)
            if collection is None:
                return {"status": "skipped", "code": "COLLECTION_INDEX_NOT_FOUND", "collection_id": collection_id}
            if str(collection["index_version"]) != INDEX_VERSION:
                return {
                    "status": "skipped",
                    "code": "COLLECTION_INDEX_VERSION_OUTDATED",
                    "collection_id": collection_id,
                    "index_version": collection["index_version"],
                }
            indexed = conn.execute(
                "SELECT content_hash FROM documents WHERE collection_id = ? AND registry_document_id = ?",
                (collection_id, document_id),
            ).fetchone()
            if indexed is not None and indexed["content_hash"] == entry.get("content_hash") and not force:
                return {"status": "ok", "collection_id": collection_id, "document_id": document_id, "updated": False, "reason": "content_hash_unchanged"}
            _delete_document_rows(conn, collection_id, document_id)
            block_count = _insert_document_rows(conn, store, collection_id, entry, now)
            conn.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (now, collection_id))
            conn.commit()
    except (OSError, sqlite3.Error, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "code": "COLLECTION_INDEX_UPSERT_FAILED", "message": str(exc), "document_id": document_id}
    return {"status": "ok", "collection_id": collection_id, "document_id": document_id, "updated": True, "block_count": block_count}


def remove_document_index(
    store_dir: str | Path = registry_store.DEFAULT_STORE_DIR,
    registry_document_id: str = "",
    collection_id: str = DEFAULT_COLLECTION_ID,
) -> ToolPayload:
    """Drop one document's rows from the collection index (delete/supersede)."""
    store = Path(store_dir)
    if not registry_document_id:
        return {"status": "error", "code": "REGISTRY_ENTRY_INVALID", "message": "registry_document_id is required."}
    if not index_path(store).exists():
        return {"status": "skipped", "code": "COLLECTION_INDEX_NOT_FOUND", "collection_id": collection_id}
    now = _now_iso()
    try:
        with _connect(store) as conn:
            _init_schema(conn)
            _delete_document_rows(conn, collection_id, registry_document_id)
            conn.execute("UPDATE collections SET updated_at = ? WHERE collection_id = ?", (now, collection_id))
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        return {"status": "error", "code": "COLLECTION_INDEX_REMOVE_FAILED", "message": str(exc), "document_id": registry_document_id}
    return {"status": "ok", "collection_id": collection_id, "document_id": registry_document_id, "removed": True}


# bm25() weights are positional in blocks_fts column-declaration order:
# collection_id, registry_document_id, block_id (UNINDEXED -> 0.0), then
# title=4, heading_path=2, preview=1.5, body=1, keywords=3 — mirroring the
# single-document _SEARCH_FIELD_WEIGHTS so title/keyword hits outrank body.
_BM25_RANK = "bm25(blocks_fts, 0.0, 0.0, 0.0, 4.0, 2.0, 1.5, 1.0, 3.0)"
_MAX_DOCUMENT_IDS = 100
_UNLIMITED_DOC_RANK = 2**31
_GROUP_TOP_REFS = 3


def _match_sql(document_id_count: int) -> str:
    # bm25() is an FTS5 auxiliary function and may only appear in the query
    # level that MATCHes blocks_fts, so the innermost query materializes it as
    # a plain `rank` column; window functions run one level up over that.
    doc_filter = ""
    if document_id_count:
        placeholders = ", ".join("?" for _ in range(document_id_count))
        doc_filter = f" AND blocks.registry_document_id IN ({placeholders})"
    return f"""
        SELECT * FROM (
            SELECT matched.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY matched.registry_document_id
                       ORDER BY matched.rank ASC, matched.order_index ASC
                   ) AS doc_rank,
                   COUNT(*) OVER (PARTITION BY matched.registry_document_id) AS doc_hit_count
            FROM (
                SELECT blocks.*, documents.content_hash AS indexed_content_hash,
                       documents.block_count AS doc_block_count,
                       documents.page_count AS doc_page_count,
                       {_BM25_RANK} AS rank
                FROM blocks_fts
                JOIN blocks ON blocks.rowid = blocks_fts.rowid
                JOIN documents ON documents.collection_id = blocks.collection_id
                  AND documents.registry_document_id = blocks.registry_document_id
                WHERE blocks_fts MATCH ? AND blocks.collection_id = ?{doc_filter}
            ) AS matched
        )
        WHERE doc_rank <= ?
        ORDER BY rank ASC, registry_document_id ASC, order_index ASC
        LIMIT ?
        """


def search_collection(
    store_dir: str | Path = registry_store.DEFAULT_STORE_DIR,
    query: str = "",
    collection_id: str = DEFAULT_COLLECTION_ID,
    limit: int = 20,
    offset: int = 0,
    per_document_limit: int | None = None,
    document_ids: list[str] | None = None,
    group_by_document: bool = False,
) -> ToolPayload:
    """Search the local collection index and return citation-ready block hits.

    With ``group_by_document`` the response pages over document-level rollups
    (exact hit_count, best_score, top snippet, up to three read-ready
    top_blocks per document) instead of a flat block list — the right shape
    for breadth questions like "which documents mention X".
    """

    store = Path(store_dir)
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    scoped_ids: list[str] = []
    if document_ids is not None:
        scoped_ids = [str(item).strip() for item in document_ids]
        if not scoped_ids or any(not item for item in scoped_ids) or len(scoped_ids) > _MAX_DOCUMENT_IDS:
            return {
                "status": "error",
                "code": "DOCUMENT_IDS_INVALID",
                "message": f"document_ids must contain 1-{_MAX_DOCUMENT_IDS} non-empty document ids.",
            }
    units = _query_units(query)
    and_query, or_query = _fts_query_variants(query)
    if not and_query:
        return {
            "status": "ok",
            "collection_id": collection_id,
            "query": query,
            "match_mode": None,
            "result_count": 0,
            "offset": offset,
            "has_more": False,
            "results": [],
        }
    if not index_path(store).exists():
        return {
            "status": "error",
            "code": "COLLECTION_INDEX_NOT_FOUND",
            "message": f"Collection index not found: {index_path(store)}",
        }

    # Per-document capping happens inside SQL via ROW_NUMBER, so fetching one
    # unit past the requested page (after the registry staleness post-filter)
    # keeps has_more exact without over-fetch multipliers. In grouped mode the
    # paging unit is a document, and each contributes at most doc_rank_cap rows.
    fetch_budget = offset + limit + 1
    if group_by_document:
        doc_rank_cap = _GROUP_TOP_REFS
        if per_document_limit is not None:
            doc_rank_cap = max(1, min(int(per_document_limit), _GROUP_TOP_REFS))
        fetch_limit = fetch_budget * doc_rank_cap
    else:
        doc_rank_cap = max(1, int(per_document_limit)) if per_document_limit is not None else _UNLIMITED_DOC_RANK
        fetch_limit = fetch_budget
    match_sql = _match_sql(len(scoped_ids))
    try:
        active_entries = _active_registry_entry_map(store)
        with _connect(store) as conn:
            _init_schema(conn)
            # All query units are required first; fall back to any-unit matching
            # only when the strict query finds nothing, and say so in match_mode
            # so callers know precision was degraded.
            match_mode = "all_terms"
            base_params = (collection_id, *scoped_ids, doc_rank_cap, fetch_limit)
            rows = conn.execute(match_sql, (and_query, *base_params)).fetchall()
            if not rows and or_query != and_query:
                match_mode = "any_term"
                rows = conn.execute(match_sql, (or_query, *base_params)).fetchall()
    except sqlite3.OperationalError as exc:
        return {"status": "error", "code": "FTS_QUERY_INVALID", "message": str(exc), "query": query}
    except (OSError, ValueError) as exc:
        return {"status": "error", "code": "REGISTRY_LOAD_FAILED", "message": str(exc)}
    except sqlite3.Error as exc:
        return {"status": "error", "code": "COLLECTION_SEARCH_FAILED", "message": str(exc)}

    if group_by_document:
        rollups: list[dict[str, Any]] = []
        by_doc: dict[str, dict[str, Any]] = {}
        for row in rows:
            doc_id = row["registry_document_id"]
            active_entry = active_entries.get(doc_id)
            if active_entry is None or active_entry.get("content_hash") != row["indexed_content_hash"]:
                continue
            rollup = by_doc.get(doc_id)
            block_result = _row_to_result(row, score=round(float(-row["rank"]), 6), units=units)
            if rollup is None:
                if len(rollups) >= fetch_budget:
                    continue
                rollup = {
                    "registry_document_id": doc_id,
                    "ir_document_id": row["ir_document_id"],
                    "source_name": row["source_name"],
                    # Exact: the window COUNT runs before the doc_rank cut.
                    "hit_count": int(row["doc_hit_count"]),
                    "best_score": block_result["score"],
                    "top_snippet": block_result["snippet"],
                    "total_blocks": row["doc_block_count"],
                    "page_count": row["doc_page_count"],
                    "top_blocks": [],
                }
                by_doc[doc_id] = rollup
                rollups.append(rollup)
            rollup["top_blocks"].append(block_result)
        results = rollups[offset : offset + limit]
        return {
            "status": "ok",
            "collection_id": collection_id,
            "query": query,
            "match_mode": match_mode,
            "group_by_document": True,
            "result_count": len(results),
            "document_count": len(results),
            "offset": offset,
            "has_more": len(rollups) > offset + limit,
            "results": results,
        }

    filtered = []
    for row in rows:
        doc_id = row["registry_document_id"]
        active_entry = active_entries.get(doc_id)
        if active_entry is None or active_entry.get("content_hash") != row["indexed_content_hash"]:
            continue
        filtered.append(_row_to_result(row, score=round(float(-row["rank"]), 6), units=units))
        if len(filtered) >= fetch_budget:
            break

    results = filtered[offset : offset + limit]
    return {
        "status": "ok",
        "collection_id": collection_id,
        "query": query,
        "match_mode": match_mode,
        "result_count": len(results),
        "offset": offset,
        "has_more": len(filtered) > offset + limit,
        "results": results,
    }


def store_collection_health(
    store_dir: str | Path = registry_store.DEFAULT_STORE_DIR,
    collection_id: str = DEFAULT_COLLECTION_ID,
) -> ToolPayload:
    path = index_path(store_dir)
    if not path.exists():
        return {
            "status": "warning",
            "code": "COLLECTION_INDEX_NOT_FOUND",
            "collection_id": collection_id,
            "index_path": str(path),
        }
    try:
        with _connect(store_dir) as conn:
            _init_schema(conn)
            collection = conn.execute(
                "SELECT * FROM collections WHERE collection_id = ?", (collection_id,)
            ).fetchone()
            if collection is None:
                return {
                    "status": "warning",
                    "code": "COLLECTION_INDEX_NOT_FOUND",
                    "collection_id": collection_id,
                    "index_path": str(path),
                }
            indexed_documents = conn.execute(
                "SELECT registry_document_id, content_hash FROM documents WHERE collection_id = ?", (collection_id,)
            ).fetchall()
            document_count = len(indexed_documents)
            block_count = conn.execute(
                "SELECT COUNT(*) FROM blocks WHERE collection_id = ?", (collection_id,)
            ).fetchone()[0]
        active_entries = _active_registry_entry_map(Path(store_dir))
    except sqlite3.Error as exc:
        return {"status": "error", "code": "COLLECTION_INDEX_CORRUPTED", "message": str(exc), "index_path": str(path)}
    except (OSError, ValueError) as exc:
        return {"status": "error", "code": "REGISTRY_LOAD_FAILED", "message": str(exc), "index_path": str(path)}

    indexed_hashes = {row["registry_document_id"]: row["content_hash"] for row in indexed_documents}
    active_hashes = {doc_id: entry.get("content_hash") for doc_id, entry in active_entries.items()}
    missing_from_index = sorted(set(active_hashes) - set(indexed_hashes))
    stale_documents = sorted(
        doc_id for doc_id, content_hash in indexed_hashes.items() if active_hashes.get(doc_id) != content_hash
    )
    index_version_outdated = str(collection["index_version"]) != INDEX_VERSION
    stale = bool(missing_from_index or stale_documents or index_version_outdated)

    return {
        "status": "warning" if stale else "ok",
        "code": "COLLECTION_INDEX_STALE" if stale else None,
        "collection_id": collection_id,
        "index_path": str(path),
        "index_version": collection["index_version"],
        "index_version_outdated": index_version_outdated,
        "updated_at": collection["updated_at"],
        "document_count": document_count,
        "active_document_count": len(active_entries),
        "block_count": block_count,
        "missing_from_index": missing_from_index,
        "stale_documents": stale_documents,
    }
