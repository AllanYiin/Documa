"""Disposable catalog, lexical metadata, and local HNSW routing for skills."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from documa.interfaces.retrieval_policy import stable_simhash
from documa.pipeline.block_keywords import extract_new_word_terms
from documa.search import hnsw
from documa.skills.store import active_skill_entries, index_path, load_skill_ir


SKILL_INDEX_VERSION = 3
SKILL_APPLICATION_ID = 0x534B494C  # SKIL
SKILL_FEATURE_VERSION = "skill-lexical-v2+tfdf+newword+simhash64+dedupe+role-v1+feature-hash-hnsw-v1"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.-]{1,}|[\u3400-\u9fff]+", re.IGNORECASE)
_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "use", "using", "when", "into", "skill",
    "請", "使用", "幫我", "以及", "這個", "一個", "需要", "可以", "進行",
}


def _lexical_term_stream(text: str) -> list[str]:
    output: list[str] = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if all("\u3400" <= char <= "\u9fff" for char in token):
            if len(token) <= 12:
                output.append(token)
            for width in (2, 3, 4):
                output.extend(token[index : index + width] for index in range(max(0, len(token) - width + 1)))
        else:
            output.append(token)
    return [item for item in output if item and item not in _STOPWORDS]


def lexical_terms(text: str) -> list[str]:
    return list(dict.fromkeys(_lexical_term_stream(text)))


def _frontmatter_terms(frontmatter: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tags", "keywords", "triggers"):
        value = frontmatter.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return lexical_terms(" ".join(values))


def _frontmatter_text(frontmatter: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("tags", "keywords", "triggers"):
        value = frontmatter.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return " ".join(values)


def _enrichment_terms(metadata: dict[str, Any]) -> list[str]:
    enrichment = metadata.get("enrichment") or {}
    values: list[str] = []
    for key in ("synonyms", "positive_triggers", "topic_tags"):
        values.extend(str(item) for item in enrichment.get(key) or [])
    return lexical_terms(" ".join(values))


def _negative_enrichment_terms(metadata: dict[str, Any]) -> list[str]:
    enrichment = metadata.get("enrichment") or {}
    return lexical_terms(" ".join(str(item) for item in enrichment.get("negative_triggers") or []))


def _explicit_mention(value: str, folded_task: str) -> bool:
    folded = value.casefold()
    if any("\u3400" <= char <= "\u9fff" for char in folded):
        return folded in folded_task
    return re.search(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", folded_task) is not None


def build_skill_index(*, store_dir: str | Path = ".documa") -> dict[str, Any]:
    output = index_path(store_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.{uuid.uuid4().hex}.tmp")
    if temporary.exists():
        temporary.unlink()
    entries = active_skill_entries(store_dir)
    skills = [(entry, load_skill_ir(entry, store_dir)) for entry in entries]

    skill_vectors: dict[str, hnsw.Vector] = {}
    with sqlite3.connect(temporary) as connection:
        connection.execute(f"PRAGMA application_id = {SKILL_APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {SKILL_INDEX_VERSION}")
        connection.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE skills (
                skill_id TEXT PRIMARY KEY, qualified_name TEXT NOT NULL, name TEXT NOT NULL,
                description TEXT NOT NULL, priority INTEGER NOT NULL, generation TEXT NOT NULL,
                ir_path TEXT NOT NULL, name_terms TEXT NOT NULL, description_terms TEXT NOT NULL,
                trigger_terms TEXT NOT NULL, enrichment_terms TEXT NOT NULL,
                negative_terms TEXT NOT NULL, simhash TEXT NOT NULL
            );
            CREATE TABLE skill_terms (
                skill_id TEXT NOT NULL, term TEXT NOT NULL, field TEXT NOT NULL, term_frequency INTEGER NOT NULL,
                PRIMARY KEY (skill_id, term, field)
            );
            CREATE TABLE term_stats (term TEXT PRIMARY KEY, document_frequency INTEGER NOT NULL);
            CREATE TABLE blocks (
                block_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, resource_path TEXT NOT NULL,
                role TEXT NOT NULL, title TEXT, parent_id TEXT, order_index INTEGER NOT NULL,
                required INTEGER NOT NULL, terms_json TEXT NOT NULL, content_hash TEXT NOT NULL,
                simhash TEXT NOT NULL, new_words_json TEXT NOT NULL
            );
            CREATE TABLE edges (
                skill_id TEXT NOT NULL, edge_type TEXT NOT NULL, from_id TEXT NOT NULL,
                to_id TEXT NOT NULL, metadata_json TEXT NOT NULL,
                PRIMARY KEY (skill_id, edge_type, from_id, to_id)
            );
            CREATE TABLE resources (
                skill_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,
                media_type TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                text_indexed INTEGER NOT NULL, PRIMARY KEY (skill_id, path)
            );
            CREATE TABLE ann_nodes (skill_id TEXT PRIMARY KEY, level INTEGER NOT NULL, vector BLOB NOT NULL);
            CREATE TABLE ann_edges (
                skill_id TEXT NOT NULL, level INTEGER NOT NULL, neighbor_id TEXT NOT NULL,
                PRIMARY KEY (skill_id, level, neighbor_id)
            );
            CREATE INDEX idx_skill_terms_term ON skill_terms(term);
            CREATE INDEX idx_blocks_skill ON blocks(skill_id);
            CREATE INDEX idx_edges_from ON edges(skill_id, from_id);
            """
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("feature_version", SKILL_FEATURE_VERSION),
                ("skill_count", str(len(skills))),
                ("ann_vector_version", hnsw.VECTOR_VERSION),
            ),
        )
        for entry, skill in skills:
            name_terms = lexical_terms(skill.name)
            description_terms = lexical_terms(skill.description)
            trigger_terms = _frontmatter_terms(skill.frontmatter)
            enrichment_terms = _enrichment_terms(skill.metadata)
            negative_terms = _negative_enrichment_terms(skill.metadata)
            routing_text = " ".join((skill.name, skill.description, " ".join(trigger_terms), " ".join(enrichment_terms)))
            connection.execute(
                "INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill.skill_id,
                    skill.qualified_name,
                    skill.name,
                    skill.description,
                    int(entry.get("priority", 0)),
                    skill.generation,
                    str(entry["ir_path"]),
                    json.dumps(name_terms, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(description_terms, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(trigger_terms, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(enrichment_terms, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(negative_terms, ensure_ascii=False, separators=(",", ":")),
                    f"{stable_simhash(routing_text):016x}",
                ),
            )
            enrichment = skill.metadata.get("enrichment") or {}
            field_texts = (
                ("name", skill.name),
                ("description", skill.description),
                ("trigger", _frontmatter_text(skill.frontmatter)),
                (
                    "enrichment",
                    " ".join(
                        str(item)
                        for key in ("synonyms", "positive_triggers", "topic_tags")
                        for item in enrichment.get(key) or []
                    ),
                ),
                ("negative", " ".join(str(item) for item in enrichment.get("negative_triggers") or [])),
            )
            for field, field_text in field_texts:
                counts = Counter(_lexical_term_stream(field_text))
                connection.executemany(
                    "INSERT OR IGNORE INTO skill_terms VALUES (?, ?, ?, ?)",
                    ((skill.skill_id, term, field, frequency) for term, frequency in counts.items()),
                )
            skill_vectors[skill.skill_id] = hnsw.vectorize(
                [
                    (skill.name, 5.0),
                    (skill.description, 4.0),
                    (" ".join(trigger_terms), 4.0),
                    (" ".join(enrichment_terms), 2.0),
                ]
            )
            for block in skill.blocks:
                block_text = " ".join((block.title or "", block.text.normalized_text or block.text.raw_text))
                terms = lexical_terms(block_text)
                new_words = extract_new_word_terms(block_text, top_k=12)
                connection.execute(
                    "INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        block.id,
                        skill.skill_id,
                        block.resource_path,
                        block.role.value,
                        block.title,
                        block.parent_id,
                        block.order_index,
                        int(bool(block.metadata.get("required"))),
                        json.dumps(terms, ensure_ascii=False, separators=(",", ":")),
                        block.content_hash,
                        f"{stable_simhash(block_text):016x}",
                        json.dumps(new_words, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
            connection.executemany(
                "INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        skill.skill_id,
                        edge.type.value,
                        edge.from_id,
                        edge.to_id,
                        json.dumps(edge.metadata, ensure_ascii=False, separators=(",", ":")),
                    )
                    for edge in skill.edges
                ),
            )
            connection.executemany(
                "INSERT INTO resources VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        skill.skill_id,
                        resource.path,
                        resource.kind,
                        resource.media_type,
                        resource.sha256,
                        resource.size,
                        int(resource.text_indexed),
                    )
                    for resource in skill.resources
                ),
            )

        connection.execute(
            """
            INSERT INTO term_stats(term, document_frequency)
            SELECT term, COUNT(DISTINCT skill_id)
            FROM skill_terms WHERE field != 'negative' GROUP BY term
            """
        )

        skill_vectors = {key: value for key, value in skill_vectors.items() if any(value)}
        levels, edges, entry_id, max_level = hnsw.build(skill_vectors)
        connection.executemany(
            "INSERT INTO ann_nodes VALUES (?, ?, ?)",
            ((skill_id, levels[skill_id], hnsw.pack(vector)) for skill_id, vector in skill_vectors.items()),
        )
        connection.executemany(
            "INSERT INTO ann_edges VALUES (?, ?, ?)",
            (
                (skill_id, level, neighbor_id)
                for (skill_id, level), neighbors in sorted(edges.items())
                for neighbor_id in sorted(neighbors)
            ),
        )
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (("ann_entry_skill_id", entry_id or ""), ("ann_max_level", str(max(0, max_level)))),
        )
        connection.commit()
    connection.close()
    temporary.replace(output)
    return {"status": "ok", "path": str(output), "skill_count": len(skills), "ann_node_count": len(skill_vectors)}


def _rows(path: Path) -> list[sqlite3.Row]:
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA user_version").fetchone()[0] != SKILL_INDEX_VERSION:
            return []
        rows = connection.execute("SELECT * FROM skills ORDER BY priority DESC, qualified_name, skill_id").fetchall()
    connection.close()
    return rows


def _ann_scores(path: Path, task: str, limit: int) -> dict[str, float]:
    query = hnsw.vectorize([(task, 1.0)])
    if not any(query) or not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        entry_id = metadata.get("ann_entry_skill_id") or ""
        if not entry_id:
            return {}

        def vector_for(skill_id: str) -> hnsw.Vector:
            row = connection.execute("SELECT vector FROM ann_nodes WHERE skill_id = ?", (skill_id,)).fetchone()
            return hnsw.unpack(row[0]) if row else tuple(0.0 for _ in range(hnsw.DIMENSIONS))

        def edges_for(skill_id: str, level: int) -> set[str]:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT neighbor_id FROM ann_edges WHERE skill_id = ? AND level = ?", (skill_id, level)
                )
            }

        ranked = hnsw.search(
            query,
            entry_id=entry_id,
            max_level=int(metadata.get("ann_max_level") or 0),
            ef=max(8, limit * 4),
            vector_for=vector_for,
            edges_for=edges_for,
        )
        scores = {skill_id: max(0.0, 1.0 - distance) for distance, skill_id in ranked[: max(1, limit)]}
    connection.close()
    return scores


def query_skill_candidates(
    task: str,
    *,
    skill_names: list[str] | None = None,
    max_skills: int = 3,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    path = index_path(store_dir)
    rows = _rows(path)
    if not rows:
        return {"status": "needs_narrowing", "code": "SKILL_CONFIG_MISSING", "candidates": []}
    requested = [item.casefold() for item in skill_names or []]
    if requested:
        selected: list[sqlite3.Row] = []
        for name in requested:
            matches = [
                row
                for row in rows
                if name in {row["skill_id"].casefold(), row["qualified_name"].casefold(), row["name"].casefold()}
            ]
            if not matches:
                return {"status": "needs_narrowing", "code": "SKILL_LOW_CONFIDENCE", "candidates": []}
            highest = max(int(row["priority"]) for row in matches)
            matches = [row for row in matches if int(row["priority"]) == highest]
            if len(matches) > 1:
                return {
                    "status": "needs_narrowing",
                    "code": "SKILL_AMBIGUOUS",
                    "candidates": [_candidate(row, 1.0, 1.0, "exact") for row in matches],
                }
            selected.append(matches[0])
        return {
            "status": "ok",
            "candidates": [_candidate(row, 100.0 - index, 1.0, "exact") for index, row in enumerate(selected[:max_skills])],
        }

    folded_task = task.casefold()
    qualified_mentions = [row for row in rows if _explicit_mention(row["qualified_name"], folded_task)]
    if qualified_mentions:
        qualified_mentions.sort(key=lambda row: (-int(row["priority"]), row["qualified_name"], row["skill_id"]))
        return {
            "status": "ok",
            "candidates": [
                _candidate(row, 100.0 - index, 1.0, "exact")
                for index, row in enumerate(qualified_mentions[:max_skills])
            ],
        }
    mentioned_names = sorted(
        {row["name"].casefold() for row in rows if _explicit_mention(row["name"], folded_task)},
        key=lambda value: (-len(value), value),
    )
    if mentioned_names:
        selected = []
        for name in mentioned_names:
            matches = [row for row in rows if row["name"].casefold() == name]
            highest = max(int(row["priority"]) for row in matches)
            matches = [row for row in matches if int(row["priority"]) == highest]
            if len(matches) > 1:
                return {
                    "status": "needs_narrowing",
                    "code": "SKILL_AMBIGUOUS",
                    "candidates": [_candidate(row, 1.0, 1.0, "exact") for row in matches[:max_skills]],
                }
            selected.append(matches[0])
        return {
            "status": "ok",
            "candidates": [
                _candidate(row, 100.0 - index, 1.0, "exact")
                for index, row in enumerate(selected[:max_skills])
            ],
        }

    query = lexical_terms(task)
    if not query:
        return {"status": "needs_narrowing", "code": "SKILL_LOW_CONFIDENCE", "candidates": []}
    query_set = set(query)
    term_df: Counter[str] = Counter()
    row_terms: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        fields = {
            field: set(json.loads(row[f"{field}_terms"]))
            for field in ("name", "description", "trigger", "enrichment", "negative")
        }
        row_terms[row["skill_id"]] = fields
        term_df.update(set().union(*(terms for field, terms in fields.items() if field != "negative")))
    ann = _ann_scores(path, task, max(max_skills * 3, 8))
    scored: list[tuple[float, float, str, sqlite3.Row]] = []
    for row in rows:
        fields = row_terms[row["skill_id"]]
        positive_terms = set().union(*(terms for field, terms in fields.items() if field != "negative"))
        matched = query_set.intersection(positive_terms)
        negative_matches = query_set.intersection(fields["negative"])
        lexical = 0.0
        for term in matched:
            idf = math.log(1.0 + (len(rows) + 0.5) / (term_df[term] + 0.5))
            lexical += idf * (
                (5.0 if term in fields["name"] else 0.0)
                + (4.0 if term in fields["description"] else 0.0)
                + (4.0 if term in fields["trigger"] else 0.0)
                + (2.0 if term in fields["enrichment"] else 0.0)
            )
        exact = row["name"].casefold() in task.casefold() or row["qualified_name"].casefold() in task.casefold()
        coverage = len(matched) / max(1, len(query_set))
        route = "lexical"
        score = lexical + (100.0 if exact else 0.0)
        if negative_matches and not exact:
            score -= 8.0 * len(negative_matches)
        if coverage < 0.2 and ann.get(row["skill_id"], 0.0) >= 0.35:
            score += ann[row["skill_id"]] * 2.0
            route = "lexical+hnsw" if lexical else "hnsw"
        if score > 0:
            scored.append((score, coverage, route, row))
    scored.sort(key=lambda item: (-item[0], -int(item[3]["priority"]), item[3]["qualified_name"], item[3]["skill_id"]))
    if not scored:
        return {"status": "needs_narrowing", "code": "SKILL_LOW_CONFIDENCE", "candidates": []}
    top_score = scored[0][0]
    candidates = [
        _candidate(row, score, coverage, route)
        for score, coverage, route, row in scored
        if score >= top_score * 0.35
    ][:max_skills]
    top_exact = scored[0][3]["name"].casefold() in task.casefold() or scored[0][3]["qualified_name"].casefold() in task.casefold()
    if not top_exact and scored[0][1] < 0.15 and top_score < 1.0:
        return {"status": "needs_narrowing", "code": "SKILL_LOW_CONFIDENCE", "candidates": candidates}
    if not top_exact and len(scored) > 1 and (top_score - scored[1][0]) / max(top_score, 1e-9) < 0.08:
        return {"status": "needs_narrowing", "code": "SKILL_LOW_CONFIDENCE", "candidates": candidates}
    return {"status": "ok", "candidates": candidates}


def _candidate(row: sqlite3.Row, score: float, coverage: float, route: str) -> dict[str, Any]:
    return {
        "skill_id": row["skill_id"],
        "qualified_name": row["qualified_name"],
        "name": row["name"],
        "description": row["description"],
        "priority": int(row["priority"]),
        "generation": row["generation"],
        "ir_path": row["ir_path"],
        "score": round(float(score), 6),
        "coverage": round(float(coverage), 6),
        "route_source": route,
    }


def block_scores(skill_id: str, task: str, *, store_dir: str | Path = ".documa") -> dict[str, float]:
    query = set(lexical_terms(task))
    if not query or not index_path(store_dir).exists():
        return {}
    with sqlite3.connect(index_path(store_dir)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM blocks WHERE skill_id = ?", (skill_id,)).fetchall()
    connection.close()
    scores: dict[str, float] = {}
    role_bonus = {"guardrail": 0.4, "scope": 0.3, "workflow": 0.25, "step": 0.2, "example": -0.05}
    for row in rows:
        terms = set(json.loads(row["terms_json"]))
        overlap = query.intersection(terms)
        new_words = {str(item.get("term")) for item in json.loads(row["new_words_json"]) if item.get("term")}
        new_word_overlap = query.intersection(new_words)
        if overlap:
            scores[row["block_id"]] = (
                len(overlap) / max(1, len(query))
                + 0.25 * len(new_word_overlap)
                + role_bonus.get(row["role"], 0.0)
            )
    return scores


def inspect_index(store_dir: str | Path = ".documa") -> dict[str, Any]:
    path = index_path(store_dir)
    if not path.exists():
        return {"status": "warning", "code": "SKILL_INDEX_MISSING", "path": str(path)}
    with sqlite3.connect(path) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        counts = {
            "skills": connection.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
            "blocks": connection.execute("SELECT COUNT(*) FROM blocks").fetchone()[0],
            "edges": connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "resources": connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
            "terms": connection.execute("SELECT COUNT(*) FROM term_stats").fetchone()[0],
        }
    connection.close()
    return {"status": "ok", "path": str(path), "version": SKILL_INDEX_VERSION, "metadata": metadata, **counts}
