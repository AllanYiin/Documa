"""Persistent, incrementally updated SQLite store for repository graphs."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock, Timeout

from documa.codegraph.document_adapter import RepositoryDocumentAdapter, is_repository_markdown
from documa.codegraph.models import (
    CODE_GRAPH_ANALYZER_VERSION,
    CODE_GRAPH_SCHEMA_VERSION,
    CodeEdge,
    CodeEdgeType,
    CodeNode,
    CodeNodeKind,
    CodeOccurrence,
    CodeSpan,
    EdgeResolution,
    ParsedCodeFile,
    ParseStatus,
    edge_id,
    sha256_bytes,
    stable_id,
)
from documa.codegraph.python_adapter import PythonCodeAdapter
from documa.codegraph.rust_adapter import RustCodeAdapter
from documa.codegraph.rust_graph import resolved_rust_edges


DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".documa",
    ".hg",
    ".svn",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "build",
    "dist",
    "target",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
SYNC_LOCK_TIMEOUT_SECONDS = 30.0


class CodeGraphError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def workspace_id_for_root(root: str | Path) -> str:
    normalized = Path(root).resolve().as_posix().casefold() if os.name == "nt" else Path(root).resolve().as_posix()
    return stable_id("cw", normalized)


def codegraph_root(store_dir: str | Path = ".documa") -> Path:
    return Path(store_dir) / "code"


def index_path(workspace_id: str, store_dir: str | Path = ".documa") -> Path:
    if not re.fullmatch(r"cw_[0-9a-f]{24}", workspace_id):
        raise CodeGraphError("CODE_GRAPH_NOT_FOUND", "Code graph workspace ID is invalid.")
    return codegraph_root(store_dir) / workspace_id / "graph.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    _ensure_schema(connection)
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        PRAGMA user_version = {CODE_GRAPH_SCHEMA_VERSION};
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            source_root TEXT NOT NULL,
            language TEXT NOT NULL,
            digest TEXT NOT NULL,
            parse_status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            file_id TEXT,
            kind TEXT NOT NULL,
            qualified_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            source_locator TEXT,
            content_hash TEXT,
            start_line INTEGER,
            end_line INTEGER,
            start_column INTEGER,
            end_column INTEGER,
            parent_id TEXT,
            signature TEXT,
            docstring TEXT,
            summary TEXT,
            role TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS nodes_qualified_idx ON nodes(qualified_name);
        CREATE INDEX IF NOT EXISTS nodes_display_idx ON nodes(display_name);
        CREATE INDEX IF NOT EXISTS nodes_file_idx ON nodes(file_id);
        CREATE INDEX IF NOT EXISTS nodes_parent_idx ON nodes(parent_id);
        CREATE TABLE IF NOT EXISTS occurrences (
            occurrence_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            start_column INTEGER,
            end_column INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS occurrences_file_idx ON occurrences(file_id);
        CREATE INDEX IF NOT EXISTS occurrences_source_idx ON occurrences(source_node_id);
        CREATE INDEX IF NOT EXISTS occurrences_role_idx ON occurrences(role);
        CREATE TABLE IF NOT EXISTS structural_edges (
            edge_id TEXT PRIMARY KEY,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            type TEXT NOT NULL,
            resolution TEXT NOT NULL,
            resolver TEXT NOT NULL,
            occurrence_id TEXT,
            file_id TEXT,
            start_line INTEGER,
            end_line INTEGER,
            start_column INTEGER,
            end_column INTEGER,
            evidence_hash TEXT,
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        );
        CREATE INDEX IF NOT EXISTS structural_edges_file_idx ON structural_edges(file_id);
        CREATE TABLE IF NOT EXISTS edges (
            edge_id TEXT PRIMARY KEY,
            source_node_id TEXT NOT NULL,
            target_node_id TEXT NOT NULL,
            type TEXT NOT NULL,
            resolution TEXT NOT NULL,
            resolver TEXT NOT NULL,
            occurrence_id TEXT,
            file_id TEXT,
            start_line INTEGER,
            end_line INTEGER,
            start_column INTEGER,
            end_column INTEGER,
            evidence_hash TEXT,
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{{}}'
        );
        CREATE INDEX IF NOT EXISTS edges_source_idx ON edges(source_node_id, type);
        CREATE INDEX IF NOT EXISTS edges_target_idx ON edges(target_node_id, type);
        CREATE INDEX IF NOT EXISTS edges_type_idx ON edges(type, resolution);
        CREATE INDEX IF NOT EXISTS edges_file_idx ON edges(file_id);
        CREATE INDEX IF NOT EXISTS edges_occurrence_idx ON edges(occurrence_id);
        CREATE TABLE IF NOT EXISTS metrics (
            node_id TEXT PRIMARY KEY,
            fan_in INTEGER NOT NULL DEFAULT 0,
            fan_out INTEGER NOT NULL DEFAULT 0,
            afferent_coupling INTEGER NOT NULL DEFAULT 0,
            efferent_coupling INTEGER NOT NULL DEFAULT 0,
            instability REAL NOT NULL DEFAULT 0,
            cycle_id TEXT,
            cycle_size INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS blindspots (
            blindspot_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            code TEXT NOT NULL,
            source_locator TEXT NOT NULL,
            start_line INTEGER,
            end_line INTEGER,
            start_column INTEGER,
            end_column INTEGER,
            expression TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            FOREIGN KEY(file_id) REFERENCES files(file_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS blindspots_file_idx ON blindspots(file_id);
        CREATE TABLE IF NOT EXISTS changes (
            generation TEXT NOT NULL,
            change_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            PRIMARY KEY(generation, entity_type, entity_id, change_type)
        );
        CREATE TABLE IF NOT EXISTS enrichments (
            node_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_version TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            summary TEXT NOT NULL,
            PRIMARY KEY(node_id, provider, provider_version, source_hash)
        );
        """
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {str(row["key"]): str(row["value"]) for row in connection.execute("SELECT key, value FROM metadata")}


def _set_metadata(connection: sqlite3.Connection, values: dict[str, str]) -> None:
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        sorted(values.items()),
    )


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _normalize_source_roots(root: Path, source_roots: Iterable[str | Path] | None) -> list[Path]:
    if source_roots is None:
        candidate = root / "src"
        roots = [candidate] if candidate.is_dir() else [root]
    else:
        roots = [(root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve() for value in source_roots]
    if root not in roots:
        roots.append(root)
    for item in roots:
        if not item.is_dir() or not _inside(root, item):
            raise CodeGraphError("CODE_SOURCE_ROOT_INVALID", f"Source root is missing or escapes the workspace: {item}")
    return sorted(set(roots), key=lambda value: (-len(value.parts), value.as_posix()))


def _is_selected(
    relative: str, include: list[str] | None, exclude: list[str] | None, document_paths: set[str], languages: set[str],
) -> bool:
    parts = PurePathParts(relative)
    if any(part in DEFAULT_EXCLUDED_DIRS for part in parts):
        return False
    if include and not any(fnmatch.fnmatch(relative, pattern) for pattern in include):
        return False
    if exclude and any(fnmatch.fnmatch(relative, pattern) for pattern in exclude):
        return False
    return (
        ("python" in languages and relative.endswith(".py"))
        or ("rust" in languages and (relative.endswith(".rs") or parts[-1] == "Cargo.toml"))
        or is_repository_markdown(relative) or relative in document_paths
    )


def PurePathParts(relative: str) -> tuple[str, ...]:
    return tuple(part for part in relative.replace("\\", "/").split("/") if part)


def _discover(root: Path, scope: dict[str, Any]) -> list[Path]:
    include, exclude = scope["include"], scope["exclude"]
    document_paths = set(scope["document_paths"])
    languages = set(scope["languages"])
    paths: set[Path] = set()
    for relative in document_paths:
        document = root / relative
        if document.resolve() != document:
            raise CodeGraphError("CODE_DOCUMENT_OUTSIDE_SCOPE", f"Document location changed: {relative}")
        if document.is_file() and _is_selected(relative, include, exclude, document_paths, languages):
            paths.add(document)
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except FileNotFoundError:
        probe = None
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodeGraphError("CODE_GRAPH_DISCOVERY_FAILED", "Could not inspect the selected repository with Git.") from exc
    if probe is not None and probe.returncode == 0 and probe.stdout.strip() == b"true":
        try:
            listed = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                capture_output=True, check=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodeGraphError("CODE_GRAPH_DISCOVERY_FAILED", "Could not read the repository's Git file scope.") from exc
        else:
            for raw in listed.stdout.split(b"\0"):
                if not raw:
                    continue
                relative = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
                candidate = (root / relative).resolve()
                if _inside(root, candidate) and candidate.is_file() and _is_selected(
                    candidate.relative_to(root).as_posix(), include, exclude, document_paths, languages
                ):
                    paths.add(candidate)
            return sorted(paths, key=lambda value: value.as_posix())
    for directory, names, files in os.walk(root):
        names[:] = [name for name in names if name not in DEFAULT_EXCLUDED_DIRS]
        base = Path(directory)
        for name in files:
            candidate = (base / name).resolve()
            if not _inside(root, candidate):
                continue
            relative = candidate.relative_to(root).as_posix()
            if _is_selected(relative, include, exclude, document_paths, languages):
                paths.add(candidate)
    return sorted(paths, key=lambda value: value.as_posix())


def _source_root_for(path: Path, source_roots: list[Path]) -> Path:
    for root in source_roots:
        if path == root or root in path.parents:
            return root
    raise CodeGraphError("CODE_SOURCE_OUTSIDE_ROOTS", f"Python source is outside configured roots: {path}")


def _source_manifest(root: Path, scope: dict[str, Any]) -> dict[str, str]:
    try:
        return {
            candidate.relative_to(root).as_posix(): sha256_bytes(candidate.read_bytes())
            for candidate in _discover(root, scope)
        }
    except OSError as exc:
        raise CodeGraphError("CODE_GRAPH_SOURCE_UNAVAILABLE", "Could not read the selected source manifest.") from exc


def _index_scope(
    root: Path,
    source_roots: list[Path],
    include: list[str] | None,
    exclude: list[str] | None,
    analysis_profile: str,
    document_paths: Iterable[str] | None,
    languages: Iterable[str] | None,
) -> dict[str, Any]:
    documents: set[str] = set()
    for path in document_paths or ():
        candidate = (root / path).resolve()
        if root not in candidate.parents or candidate.is_dir():
            raise CodeGraphError("CODE_DOCUMENT_OUTSIDE_SCOPE", f"Document must be a file within the repository: {path}")
        documents.add(candidate.relative_to(root).as_posix())
    selected = set(languages if languages is not None else ["python"])
    if not selected or selected - {"python", "rust"}:
        raise CodeGraphError("CODE_LANGUAGE_INVALID", "Choose one or more supported code languages: python, rust.")
    adapters = {RepositoryDocumentAdapter.name: RepositoryDocumentAdapter.version}
    if "python" in selected:
        adapters[PythonCodeAdapter.name] = PythonCodeAdapter.version
    if "rust" in selected:
        adapters[RustCodeAdapter.name] = RustCodeAdapter.capability_version()
    return {
        "source_roots": [value.relative_to(root).as_posix() for value in source_roots],
        "include": sorted(set(include or [])),
        "exclude": sorted(set(exclude or [])),
        "analysis_profile": analysis_profile,
        "document_paths": sorted(documents),
        "languages": sorted(selected),
        "adapters": adapters,
    }


def _snapshot_nodes(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(row["node_id"]): {
            "kind": row["kind"],
            "qualifiedName": row["qualified_name"],
            "contentHash": row["content_hash"],
        }
        for row in connection.execute("SELECT node_id, kind, qualified_name, content_hash FROM nodes")
    }


def _snapshot_edges(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        str(row["edge_id"]): {
            "sourceNodeId": row["source_node_id"],
            "targetNodeId": row["target_node_id"],
            "type": row["type"],
            "resolution": row["resolution"],
        }
        for row in connection.execute("SELECT edge_id, source_node_id, target_node_id, type, resolution FROM edges")
    }


def _span_values(span: CodeSpan | None) -> tuple[int | None, int | None, int | None, int | None]:
    if span is None:
        return None, None, None, None
    return span.start_line, span.end_line, span.start_column, span.end_column


def _insert_node(connection: sqlite3.Connection, node: CodeNode) -> None:
    start_line, end_line, start_column, end_column = _span_values(node.span)
    connection.execute(
        """
        INSERT OR REPLACE INTO nodes(
            node_id,file_id,kind,qualified_name,display_name,source_locator,content_hash,
            start_line,end_line,start_column,end_column,parent_id,signature,docstring,summary,role,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            node.node_id,
            node.file_id,
            node.kind.value,
            node.qualified_name,
            node.display_name,
            node.source_locator,
            node.content_hash,
            start_line,
            end_line,
            start_column,
            end_column,
            node.parent_id,
            node.signature,
            node.docstring,
            node.summary,
            node.role,
            json.dumps(node.metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )


def _insert_occurrence(connection: sqlite3.Connection, occurrence: CodeOccurrence) -> None:
    start_line, end_line, start_column, end_column = _span_values(occurrence.span)
    connection.execute(
        """
        INSERT OR REPLACE INTO occurrences(
            occurrence_id,file_id,source_node_id,role,text,start_line,end_line,start_column,end_column,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            occurrence.occurrence_id,
            occurrence.file_id,
            occurrence.source_node_id,
            occurrence.role,
            occurrence.text,
            start_line,
            end_line,
            start_column,
            end_column,
            json.dumps(occurrence.metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )


def _insert_edge(connection: sqlite3.Connection, table: str, edge: CodeEdge) -> None:
    if table not in {"structural_edges", "edges"}:
        raise ValueError("Invalid edge table")
    start_line, end_line, start_column, end_column = _span_values(edge.evidence_span)
    connection.execute(
        f"""
        INSERT OR REPLACE INTO {table}(
            edge_id,source_node_id,target_node_id,type,resolution,resolver,occurrence_id,file_id,
            start_line,end_line,start_column,end_column,evidence_hash,confidence,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            edge.edge_id,
            edge.source_node_id,
            edge.target_node_id,
            edge.type.value,
            edge.resolution.value,
            edge.resolver,
            edge.evidence_occurrence_id,
            edge.evidence_file_id,
            start_line,
            end_line,
            start_column,
            end_column,
            edge.evidence_hash,
            edge.confidence,
            json.dumps(edge.metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )


def _insert_blindspot(connection: sqlite3.Connection, item: dict[str, Any]) -> None:
    span = item.get("span") or {}
    connection.execute(
        """
        INSERT OR REPLACE INTO blindspots(
            blindspot_id,file_id,code,source_locator,start_line,end_line,start_column,end_column,expression,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            item["blindspot_id"],
            item["file_id"],
            item["code"],
            item["source_locator"],
            span.get("startLine"),
            span.get("endLine"),
            span.get("startColumn"),
            span.get("endColumn"),
            item.get("expression", ""),
            json.dumps(item.get("metadata") or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        ),
    )


def _insert_parsed(connection: sqlite3.Connection, parsed: Any, source_root: Path, workspace_node_id: str) -> None:
    connection.execute("DELETE FROM structural_edges WHERE file_id = ?", (parsed.file_id,))
    connection.execute("DELETE FROM occurrences WHERE file_id = ?", (parsed.file_id,))
    connection.execute("DELETE FROM blindspots WHERE file_id = ?", (parsed.file_id,))
    connection.execute("DELETE FROM nodes WHERE file_id = ?", (parsed.file_id,))
    connection.execute(
        """
        INSERT INTO files(file_id,relative_path,source_root,language,digest,parse_status,error_code,error_message)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(file_id) DO UPDATE SET
            relative_path=excluded.relative_path,source_root=excluded.source_root,language=excluded.language,
            digest=excluded.digest,parse_status=excluded.parse_status,error_code=excluded.error_code,error_message=excluded.error_message
        """,
        (
            parsed.file_id,
            parsed.relative_path,
            source_root.as_posix(),
            parsed.language,
            parsed.digest,
            parsed.parse_status.value,
            parsed.error_code,
            parsed.error_message,
        ),
    )
    for node in parsed.nodes:
        if node.kind == CodeNodeKind.FILE:
            node.parent_id = workspace_node_id
        _insert_node(connection, node)
    for occurrence in parsed.occurrences:
        _insert_occurrence(connection, occurrence)
    for edge in parsed.structural_edges:
        _insert_edge(connection, "structural_edges", edge)
    for item in parsed.blindspots:
        _insert_blindspot(connection, item)


def _external_node(connection: sqlite3.Connection, workspace_id: str, qualified_name: str, kind: CodeNodeKind) -> str:
    node_id = stable_id("cx", workspace_id, kind.value, qualified_name)
    if connection.execute("SELECT 1 FROM nodes WHERE node_id = ?", (node_id,)).fetchone() is None:
        _insert_node(
            connection,
            CodeNode(
                node_id=node_id,
                kind=kind,
                qualified_name=qualified_name,
                display_name=qualified_name.rsplit(".", 1)[-1],
                source_locator=None,
                content_hash=None,
                summary=f"External Python {kind.value.replace('_', ' ')} {qualified_name}.",
                metadata={"language": "python", "external": True},
            ),
        )
    return node_id


def _relative_import(current_module: str, locator: str, module: str, level: int) -> str:
    if level <= 0:
        return module
    package = current_module if locator.endswith("/__init__.py") or locator == "__init__.py" else current_module.rpartition(".")[0]
    parts = [part for part in package.split(".") if part]
    remove = max(0, level - 1)
    if remove:
        parts = parts[:-remove] if remove <= len(parts) else []
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _node_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(connection.execute("SELECT * FROM nodes ORDER BY qualified_name,node_id"))


def _python_binding_nodes(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return [row for row in _node_rows(connection) if json.loads(row["metadata_json"]).get("language") in {None, "python"}]


def _local_resolution_files(connection: sqlite3.Connection, parsed_files: list[ParsedCodeFile]) -> set[str] | None:
    """Use reverse dependencies when the global Python binding namespace is unchanged."""
    dirty = {parsed.file_id for parsed in parsed_files}
    for parsed in parsed_files:
        nodes = {
            (node.node_id, node.kind.value, node.qualified_name, node.display_name, node.parent_id)
            for node in parsed.nodes if node.kind != CodeNodeKind.FILE
        }
        previous_nodes = {
            tuple(row)
            for row in connection.execute(
                "SELECT node_id,kind,qualified_name,display_name,parent_id FROM nodes WHERE file_id = ? AND kind != 'file'",
                (parsed.file_id,),
            )
        }
        if parsed.parse_status != ParseStatus.OK or nodes != previous_nodes:
            return None
        # Binding order can affect aliases and inheritance. Compare it without evidence offsets or digests.
        previous_bindings = [
            (row["role"], row["source_node_id"], row["text"], json.loads(row["metadata_json"]))
            for row in connection.execute(
                "SELECT * FROM occurrences WHERE file_id = ? AND role IN ('import','base','decorator') "
                "ORDER BY start_line,occurrence_id", (parsed.file_id,),
            )
        ]
        bindings = [
            (item.role, item.source_node_id, item.text, item.metadata)
            for item in sorted(parsed.occurrences, key=lambda item: (item.span.start_line, item.occurrence_id))
            if item.role in {"import", "base", "decorator"}
        ]
        if len(previous_bindings) != len(bindings):
            return None
        for old, new in zip(previous_bindings, bindings):
            if old[:3] != new[:3] or {k: v for k, v in old[3].items() if k != "sourceDigest"} != {
                k: v for k, v in new[3].items() if k != "sourceDigest"
            }:
                return None

    inbound: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute("""
        SELECT DISTINCT target.file_id AS dependency, edges.file_id AS dependent
        FROM edges JOIN nodes target ON target.node_id = edges.target_node_id
        WHERE edges.file_id IS NOT NULL AND target.file_id IS NOT NULL AND edges.file_id != target.file_id
    """):
        inbound[row["dependency"]].add(row["dependent"])
    pending = list(dirty)
    while pending:
        dependents = inbound[pending.pop()] - dirty
        dirty.update(dependents)
        pending.extend(dependents)
    return dirty


def _resolve_graph(
    connection: sqlite3.Connection,
    root: Path,
    workspace_id: str,
    workspace_node_id: str,
    dirty_files: set[str] | None = None,
) -> None:
    if dirty_files is None:
        connection.execute("DELETE FROM edges")
        connection.execute("DELETE FROM blindspots WHERE code LIKE 'UNRESOLVED_%'")
        connection.execute("DELETE FROM nodes WHERE file_id IS NULL AND kind IN ('external_module','external_symbol','package')")
        connection.execute("INSERT INTO edges SELECT * FROM structural_edges")
    else:
        for file_id in sorted(dirty_files):
            connection.execute("DELETE FROM edges WHERE file_id = ?", (file_id,))
            connection.execute("DELETE FROM blindspots WHERE file_id = ? AND code LIKE 'UNRESOLVED_%'", (file_id,))
            connection.execute("INSERT INTO edges SELECT * FROM structural_edges WHERE file_id = ?", (file_id,))

    rows = _python_binding_nodes(connection)
    by_id = {str(row["node_id"]): row for row in rows}
    modules = {str(row["qualified_name"]): row for row in rows if row["kind"] == CodeNodeKind.MODULE.value}
    files = {str(row["file_id"]): row for row in connection.execute("SELECT * FROM files")}
    module_by_file = {str(row["file_id"]): row for row in rows if row["kind"] == CodeNodeKind.MODULE.value}
    by_qname: dict[str, list[sqlite3.Row]] = defaultdict(list)
    by_display: dict[str, list[sqlite3.Row]] = defaultdict(list)
    children: dict[str, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_qname[str(row["qualified_name"])].append(row)
        by_display[str(row["display_name"])].append(row)
        if row["parent_id"]:
            children[str(row["parent_id"])][str(row["display_name"])].append(row)

    package_nodes: dict[str, str] = {}
    for module_name, module_row in (sorted(modules.items()) if dirty_files is None else []):
        parts = module_name.split(".")[:-1]
        parent_id = workspace_node_id
        for index in range(1, len(parts) + 1):
            package = ".".join(parts[:index])
            package_id = package_nodes.get(package)
            if package_id is None:
                package_id = stable_id("cp", workspace_id, package)
                package_nodes[package] = package_id
                _insert_node(
                    connection,
                    CodeNode(
                        node_id=package_id,
                        kind=CodeNodeKind.PACKAGE,
                        qualified_name=package,
                        display_name=parts[index - 1],
                        source_locator=None,
                        content_hash=None,
                        parent_id=parent_id,
                        summary=f"Python package {package}.",
                        metadata={"language": "python"},
                    ),
                )
                _insert_edge(
                    connection,
                    "edges",
                    CodeEdge(
                        edge_id=edge_id(parent_id, package_id, CodeEdgeType.CONTAINS),
                        source_node_id=parent_id,
                        target_node_id=package_id,
                        type=CodeEdgeType.CONTAINS,
                        resolution=EdgeResolution.EXACT,
                        resolver=CODE_GRAPH_ANALYZER_VERSION,
                    ),
                )
            parent_id = package_id
        if parts:
            _insert_edge(
                connection,
                "edges",
                CodeEdge(
                    edge_id=edge_id(parent_id, str(module_row["node_id"]), CodeEdgeType.CONTAINS),
                    source_node_id=parent_id,
                    target_node_id=str(module_row["node_id"]),
                    type=CodeEdgeType.CONTAINS,
                    resolution=EdgeResolution.EXACT,
                    resolver=CODE_GRAPH_ANALYZER_VERSION,
                ),
            )
    for file_row in (files.values() if dirty_files is None else []):
        _insert_edge(
            connection,
            "edges",
            CodeEdge(
                edge_id=edge_id(workspace_node_id, str(file_row["file_id"]), CodeEdgeType.CONTAINS),
                source_node_id=workspace_node_id,
                target_node_id=str(file_row["file_id"]),
                type=CodeEdgeType.CONTAINS,
                resolution=EdgeResolution.EXACT,
                resolver=CODE_GRAPH_ANALYZER_VERSION,
            ),
        )

    def write_resolution(edge: CodeEdge) -> None:
        if dirty_files is None or edge.evidence_file_id in dirty_files:
            _insert_edge(connection, "edges", edge)

    imports_by_file: dict[str, dict[str, tuple[str, str | None]]] = defaultdict(dict)
    occurrences = list(connection.execute("SELECT * FROM occurrences ORDER BY file_id, start_line, occurrence_id"))
    for occurrence in occurrences:
        if occurrence["role"] != "import":
            continue
        metadata = json.loads(occurrence["metadata_json"])
        file_id = str(occurrence["file_id"])
        module_row = module_by_file.get(file_id)
        if module_row is None:
            continue
        imported_module = _relative_import(
            str(module_row["qualified_name"]),
            str(module_row["source_locator"]),
            str(metadata.get("module") or ""),
            int(metadata.get("level") or 0),
        )
        imported_name = metadata.get("name")
        alias = str(metadata.get("alias") or imported_name or imported_module.split(".", 1)[0])
        imported_submodule = (
            modules.get(f"{imported_module}.{imported_name}".strip("."))
            if imported_name and imported_name != "*"
            else None
        )
        target_module_row = imported_submodule or modules.get(imported_module)
        target_module_id = (
            str(target_module_row["node_id"])
            if target_module_row is not None
            else _external_node(connection, workspace_id, imported_module or alias, CodeNodeKind.EXTERNAL_MODULE)
        )
        imports_by_file[file_id][alias] = (
            target_module_id,
            None if imported_submodule is not None else str(imported_name) if imported_name else None,
        )
        span = CodeSpan(
            int(occurrence["start_line"]),
            int(occurrence["end_line"]),
            occurrence["start_column"],
            occurrence["end_column"],
        )
        common = {
            "classification": metadata.get("classification", "runtime"),
            "importedModule": imported_module,
            **({"importedName": imported_name} if imported_name else {}),
        }
        write_resolution(
            CodeEdge(
                edge_id=edge_id(str(module_row["node_id"]), target_module_id, CodeEdgeType.IMPORTS_MODULE, occurrence["occurrence_id"]),
                source_node_id=str(module_row["node_id"]),
                target_node_id=target_module_id,
                type=CodeEdgeType.IMPORTS_MODULE,
                resolution=EdgeResolution.RESOLVED,
                resolver=CODE_GRAPH_ANALYZER_VERSION,
                evidence_occurrence_id=str(occurrence["occurrence_id"]),
                evidence_file_id=file_id,
                evidence_span=span,
                evidence_hash=metadata.get("sourceDigest"),
                confidence=1.0,
                metadata=common,
            ),
        )
        if imported_name and imported_name != "*" and imported_submodule is None:
            symbol_targets = children.get(target_module_id, {}).get(str(imported_name), [])
            target_symbol_id = (
                str(symbol_targets[0]["node_id"])
                if len(symbol_targets) == 1
                else _external_node(
                    connection, workspace_id, f"{imported_module}.{imported_name}".strip("."), CodeNodeKind.EXTERNAL_SYMBOL
                )
            )
            imports_by_file[file_id][alias] = (target_symbol_id, str(imported_name))
            write_resolution(
                CodeEdge(
                    edge_id=edge_id(str(module_row["node_id"]), target_symbol_id, CodeEdgeType.IMPORTS_SYMBOL, occurrence["occurrence_id"]),
                    source_node_id=str(module_row["node_id"]),
                    target_node_id=target_symbol_id,
                    type=CodeEdgeType.IMPORTS_SYMBOL,
                    resolution=EdgeResolution.RESOLVED,
                    resolver=CODE_GRAPH_ANALYZER_VERSION,
                    evidence_occurrence_id=str(occurrence["occurrence_id"]),
                    evidence_file_id=file_id,
                    evidence_span=span,
                    evidence_hash=metadata.get("sourceDigest"),
                    confidence=1.0 if len(symbol_targets) == 1 else 0.95,
                    metadata=common,
                ),
            )

    # Refresh lookup maps after package/external nodes were created.
    rows = _python_binding_nodes(connection)
    by_id = {str(row["node_id"]): row for row in rows}
    by_qname = defaultdict(list)
    by_display = defaultdict(list)
    children = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_qname[str(row["qualified_name"])].append(row)
        by_display[str(row["display_name"])].append(row)
        if row["parent_id"]:
            children[str(row["parent_id"])][str(row["display_name"])].append(row)

    class_bases: dict[str, list[str]] = defaultdict(list)

    def source_module(row: sqlite3.Row) -> sqlite3.Row | None:
        return module_by_file.get(str(row["file_id"])) if row["file_id"] else None

    def unique(values: Iterable[sqlite3.Row]) -> list[sqlite3.Row]:
        output: list[sqlite3.Row] = []
        seen: set[str] = set()
        for value in values:
            if str(value["node_id"]) not in seen:
                seen.add(str(value["node_id"]))
                output.append(value)
        return output

    def resolve_type(type_name: str, source_row: sqlite3.Row, file_id: str) -> list[sqlite3.Row]:
        type_name = type_name.replace("()", "").split("[")[0]
        module_row = source_module(source_row)
        candidates: list[sqlite3.Row] = []
        if module_row is not None:
            candidates.extend(children.get(str(module_row["node_id"]), {}).get(type_name.split(".")[-1], []))
        imported = imports_by_file.get(file_id, {}).get(type_name.split(".")[0])
        if imported and imported[0] in by_id:
            imported_row = by_id[imported[0]]
            if imported_row["kind"] == CodeNodeKind.CLASS.value:
                candidates.append(imported_row)
            elif "." in type_name:
                candidates.extend(children.get(str(imported_row["node_id"]), {}).get(type_name.split(".")[-1], []))
        candidates.extend(row for row in by_qname.get(type_name, []) if row["kind"] == CodeNodeKind.CLASS.value)
        if not candidates:
            candidates.extend(row for row in by_display.get(type_name.split(".")[-1], []) if row["kind"] == CodeNodeKind.CLASS.value)
        return unique(candidates)

    def resolve_reference(
        target: str,
        source_row: sqlite3.Row,
        file_id: str,
        metadata: dict[str, Any],
    ) -> tuple[list[sqlite3.Row], EdgeResolution]:
        clean = target.removesuffix("()")
        module_row = source_module(source_row)
        class_name = metadata.get("class")
        if metadata.get("receiverType") and metadata.get("method"):
            classes = resolve_type(str(metadata["receiverType"]), source_row, file_id)
            members = [member for cls in classes for member in children.get(str(cls["node_id"]), {}).get(str(metadata["method"]), [])]
            if members:
                return unique(members), EdgeResolution.RESOLVED
        if "." not in clean:
            if class_name:
                class_rows = by_qname.get(str(class_name), [])
                members = [member for cls in class_rows for member in children.get(str(cls["node_id"]), {}).get(clean, [])]
                if members:
                    return unique(members), EdgeResolution.RESOLVED
            if module_row is not None:
                members = children.get(str(module_row["node_id"]), {}).get(clean, [])
                if len(members) == 1:
                    return members, EdgeResolution.RESOLVED
            imported = imports_by_file.get(file_id, {}).get(clean)
            if imported and imported[0] in by_id:
                return [by_id[imported[0]]], EdgeResolution.RESOLVED
            candidates = [
                row
                for row in by_display.get(clean, [])
                if row["kind"] in {CodeNodeKind.FUNCTION.value, CodeNodeKind.CLASS.value}
            ]
            if len(candidates) == 1:
                return candidates, EdgeResolution.RESOLVED
            return unique(candidates[:8]), EdgeResolution.POSSIBLE
        parts = clean.split(".")
        member = parts[-1]
        base = ".".join(parts[:-1])
        if parts[0] in {"self", "cls"} and class_name:
            if len(parts) == 2:
                class_rows = by_qname.get(str(class_name), [])
                members = [value for cls in class_rows for value in children.get(str(cls["node_id"]), {}).get(member, [])]
                if members:
                    return unique(members), EdgeResolution.RESOLVED
            local_types = metadata.get("localTypes") or {}
            inferred = local_types.get(base)
            if inferred:
                classes = resolve_type(str(inferred), source_row, file_id)
                members = [value for cls in classes for value in children.get(str(cls["node_id"]), {}).get(member, [])]
                if members:
                    return unique(members), EdgeResolution.RESOLVED
        if base == "super()" and class_name:
            members = [
                value
                for base_id in class_bases.get(str(class_name), [])
                for value in children.get(base_id, {}).get(member, [])
            ]
            if members:
                return unique(members), EdgeResolution.RESOLVED
        local_types = metadata.get("localTypes") or {}
        if base in local_types:
            classes = resolve_type(str(local_types[base]), source_row, file_id)
            members = [value for cls in classes for value in children.get(str(cls["node_id"]), {}).get(member, [])]
            if members:
                return unique(members), EdgeResolution.RESOLVED
        imported = imports_by_file.get(file_id, {}).get(parts[0])
        if imported and imported[0] in by_id:
            imported_row = by_id[imported[0]]
            members = children.get(str(imported_row["node_id"]), {}).get(member, [])
            if members:
                return unique(members), EdgeResolution.RESOLVED
            if imported_row["kind"] == CodeNodeKind.CLASS.value:
                members = children.get(str(imported_row["node_id"]), {}).get(member, [])
                if members:
                    return unique(members), EdgeResolution.RESOLVED
        classes = resolve_type(base, source_row, file_id)
        members = [value for cls in classes for value in children.get(str(cls["node_id"]), {}).get(member, [])]
        if members:
            return unique(members), EdgeResolution.RESOLVED
        candidates = [
            row
            for row in by_display.get(member, [])
            if row["kind"] in {CodeNodeKind.METHOD.value, CodeNodeKind.FUNCTION.value}
        ]
        return unique(candidates[:8]), EdgeResolution.POSSIBLE

    reference_roles = {"base", "decorator", "call", "callback"}
    for occurrence in occurrences:
        role = str(occurrence["role"])
        if role not in reference_roles:
            continue
        affected = dirty_files is None or occurrence["file_id"] in dirty_files
        if not affected and role != "base":
            continue
        source_row = by_id.get(str(occurrence["source_node_id"]))
        if source_row is None:
            continue
        metadata = json.loads(occurrence["metadata_json"])
        target_text = str(metadata.get("target") or occurrence["text"])
        targets, resolution = resolve_reference(target_text, source_row, str(occurrence["file_id"]), metadata)
        span = CodeSpan(
            int(occurrence["start_line"]),
            int(occurrence["end_line"]),
            occurrence["start_column"],
            occurrence["end_column"],
        )
        if role == "base":
            edge_type = CodeEdgeType.INHERITS
        elif role == "decorator":
            edge_type = CodeEdgeType.DECORATES
        elif role == "callback":
            edge_type = CodeEdgeType.REGISTERS_CALLBACK
        else:
            edge_type = CodeEdgeType.CALLS
        if not targets and role in {"base", "decorator"}:
            external_id = _external_node(connection, workspace_id, target_text, CodeNodeKind.EXTERNAL_SYMBOL)
            targets = [connection.execute("SELECT * FROM nodes WHERE node_id = ?", (external_id,)).fetchone()]
            resolution = EdgeResolution.RESOLVED
        for target in targets:
            actual_type = edge_type
            if role == "call" and target["kind"] == CodeNodeKind.CLASS.value:
                actual_type = CodeEdgeType.CONSTRUCTS
            write_resolution(
                CodeEdge(
                    edge_id=edge_id(str(source_row["node_id"]), str(target["node_id"]), actual_type, occurrence["occurrence_id"]),
                    source_node_id=str(source_row["node_id"]),
                    target_node_id=str(target["node_id"]),
                    type=actual_type,
                    resolution=resolution,
                    resolver=CODE_GRAPH_ANALYZER_VERSION,
                    evidence_occurrence_id=str(occurrence["occurrence_id"]),
                    evidence_file_id=str(occurrence["file_id"]),
                    evidence_span=span,
                    evidence_hash=metadata.get("sourceDigest"),
                    confidence=1.0 if resolution == EdgeResolution.RESOLVED else 0.5,
                    metadata={"expression": target_text},
                ),
            )
            if role == "base" and target["kind"] == CodeNodeKind.CLASS.value:
                class_bases[str(source_row["qualified_name"])].append(str(target["node_id"]))
                if str(target["display_name"]).endswith(("Protocol", "ABC")):
                    write_resolution(
                        CodeEdge(
                            edge_id=edge_id(str(source_row["node_id"]), str(target["node_id"]), CodeEdgeType.IMPLEMENTS, occurrence["occurrence_id"]),
                            source_node_id=str(source_row["node_id"]),
                            target_node_id=str(target["node_id"]),
                            type=CodeEdgeType.IMPLEMENTS,
                            resolution=resolution,
                            resolver=CODE_GRAPH_ANALYZER_VERSION,
                            evidence_occurrence_id=str(occurrence["occurrence_id"]),
                            evidence_file_id=str(occurrence["file_id"]),
                            evidence_span=span,
                            evidence_hash=metadata.get("sourceDigest"),
                            confidence=1.0,
                        ),
                    )
        if affected and (not targets or resolution == EdgeResolution.POSSIBLE):
            code = "UNRESOLVED_CALL" if role in {"call", "callback"} else "UNRESOLVED_REFERENCE"
            _insert_blindspot(
                connection,
                {
                    "blindspot_id": stable_id("cb", str(occurrence["occurrence_id"]), code),
                    "file_id": str(occurrence["file_id"]),
                    "code": code,
                    "source_locator": metadata.get("sourceLocator") or files[str(occurrence["file_id"])]["relative_path"],
                    "span": {
                        "startLine": span.start_line,
                        "endLine": span.end_line,
                        "startColumn": span.start_column,
                        "endColumn": span.end_column,
                    },
                    "expression": target_text,
                    "metadata": {"candidateCount": len(targets)},
                },
            )
    _resolve_document_relationships(connection, dirty_files)
    if connection.execute("SELECT 1 FROM files WHERE language IN ('rust','cargo') LIMIT 1").fetchone():
        for edge in resolved_rust_edges(connection, root, imports_by_file):
            write_resolution(edge)
    _rebuild_metrics(connection)


def _document_link_targets(rows: list[sqlite3.Row], anchor: str) -> list[sqlite3.Row]:
    if not anchor:
        return [row for row in rows if row["kind"] == CodeNodeKind.FILE.value]
    line_anchor = re.fullmatch(r"L(\d+)(?:-L(\d+))?", anchor, flags=re.IGNORECASE)
    if line_anchor:
        start = int(line_anchor[1])
        end = int(line_anchor[2] or start)
        targets = [row for row in rows if row["start_line"] and row["start_line"] <= start <= end <= row["end_line"]]
        if targets:
            width = min(row["end_line"] - row["start_line"] for row in targets)
            return [row for row in targets if row["end_line"] - row["start_line"] == width]
        return []
    return [
        row for row in rows
        if anchor in {row["qualified_name"], row["display_name"],
                      re.sub(r"[^\w -]", "", row["display_name"].casefold()).replace(" ", "-")}
        and (row["kind"] != CodeNodeKind.DOCUMENT_BLOCK.value or json.loads(row["metadata_json"]).get("blockType") == "heading")
    ]


def _resolve_document_relationships(connection: sqlite3.Connection, dirty_files: set[str] | None) -> None:
    file_nodes: dict[str, list[sqlite3.Row]] = defaultdict(list)
    named_symbols: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in _node_rows(connection):
        if row["source_locator"]:
            file_nodes[row["source_locator"]].append(row)
        if row["kind"] in {"module", "class", "function", "method"} and row["file_id"]:
            for name in {row["display_name"], row["qualified_name"]}:
                named_symbols[name].append(row)
    for occurrence in connection.execute("SELECT * FROM occurrences WHERE role = 'document' ORDER BY occurrence_id"):
        file_id = occurrence["file_id"]
        if dirty_files is not None and file_id not in dirty_files:
            continue
        source_id = occurrence["source_node_id"]
        metadata = json.loads(occurrence["metadata_json"])
        path_target = metadata.get("targetPath")
        if path_target:
            candidates = _document_link_targets(file_nodes.get(path_target, []), metadata["anchor"])
        else:
            candidates = named_symbols.get(metadata["targetName"], [])
        candidates = [row for row in candidates if row["node_id"] != source_id]
        explicit = bool(path_target or "." in metadata.get("targetName", ""))
        resolution = EdgeResolution.EXACT if explicit and len(candidates) == 1 else EdgeResolution.POSSIBLE
        span = CodeSpan(occurrence["start_line"], occurrence["end_line"])
        for target in candidates[:8]:
            _insert_edge(connection, "edges", CodeEdge(
                edge_id(source_id, target["node_id"], CodeEdgeType.DOCUMENTS, occurrence["occurrence_id"]),
                source_id, target["node_id"], CodeEdgeType.DOCUMENTS, resolution, RepositoryDocumentAdapter.version,
                evidence_occurrence_id=occurrence["occurrence_id"], evidence_file_id=file_id,
                evidence_span=span if metadata["hasLineSpan"] else None, evidence_hash=metadata["sourceDigest"],
                metadata={"documentBlockId": metadata["indexedBlockId"], "reference": occurrence["text"]},
            ))
        if not candidates and explicit:
            _insert_blindspot(connection, {
                "blindspot_id": stable_id("cb", occurrence["occurrence_id"], "document"),
                "file_id": file_id, "code": "UNRESOLVED_DOCUMENT_REFERENCE",
                "source_locator": metadata["sourceLocator"],
                "span": {"startLine": span.start_line, "endLine": span.end_line} if metadata["hasLineSpan"] else {},
                "expression": occurrence["text"], "metadata": {"candidateCount": 0},
            })

    calls = list(connection.execute("""
        SELECT edges.* FROM edges JOIN nodes source ON source.node_id = edges.source_node_id
        WHERE source.role = 'test' AND source.kind IN ('function','method') AND source.display_name GLOB 'test_*'
          AND edges.type IN ('calls','constructs') AND edges.resolution IN ('EXACT','RESOLVED')
    """))
    for call in calls:
        if dirty_files is not None and call["file_id"] not in dirty_files:
            continue
        source_id, target_id = call["source_node_id"], call["target_node_id"]
        _insert_edge(connection, "edges", CodeEdge(
            edge_id(source_id, target_id, CodeEdgeType.TESTS, call["occurrence_id"]),
            source_id, target_id, CodeEdgeType.TESTS, EdgeResolution(call["resolution"]), CODE_GRAPH_ANALYZER_VERSION,
            evidence_occurrence_id=call["occurrence_id"], evidence_file_id=call["file_id"],
            evidence_span=CodeSpan(call["start_line"], call["end_line"], call["start_column"], call["end_column"]),
            evidence_hash=call["evidence_hash"], metadata={"callEdgeId": call["edge_id"]},
        ))


def _tarjan(adjacency: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(adjacency.get(node, set())):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while stack:
                value = stack.pop()
                on_stack.remove(value)
                component.append(value)
                if value == node:
                    break
            components.append(sorted(component))

    for node in sorted(adjacency):
        if node not in indices:
            visit(node)
    return components


def _rebuild_metrics(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM metrics")
    rows = list(connection.execute("SELECT node_id, kind FROM nodes"))
    kinds = {str(row["node_id"]): str(row["kind"]) for row in rows}
    inbound: dict[str, set[str]] = defaultdict(set)
    outbound: dict[str, set[str]] = defaultdict(set)
    module_adjacency: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute(
        "SELECT source_node_id,target_node_id,type,resolution FROM edges WHERE resolution IN ('EXACT','RESOLVED')"
    ):
        source = str(row["source_node_id"])
        target = str(row["target_node_id"])
        outbound[source].add(target)
        inbound[target].add(source)
        if row["type"] == CodeEdgeType.IMPORTS_MODULE.value and kinds.get(target) == CodeNodeKind.MODULE.value:
            module_adjacency[source].add(target)
            module_adjacency.setdefault(target, set())
    cycles: dict[str, tuple[str, int]] = {}
    for component in _tarjan(module_adjacency):
        is_self_cycle = len(component) == 1 and component[0] in module_adjacency.get(component[0], set())
        if len(component) <= 1 and not is_self_cycle:
            continue
        cycle_id = stable_id("cc", *component)
        for node in component:
            cycles[node] = (cycle_id, len(component))
    for node_id in kinds:
        fan_in = len(inbound.get(node_id, set()))
        fan_out = len(outbound.get(node_id, set()))
        if kinds[node_id] == CodeNodeKind.MODULE.value:
            ca = len({source for source in inbound.get(node_id, set()) if kinds.get(source) == CodeNodeKind.MODULE.value})
            ce = len({target for target in outbound.get(node_id, set()) if kinds.get(target) == CodeNodeKind.MODULE.value})
        else:
            ca, ce = fan_in, fan_out
        instability = ce / (ca + ce) if ca + ce else 0.0
        cycle_id, cycle_size = cycles.get(node_id, (None, 0))
        connection.execute(
            """
            INSERT INTO metrics(node_id,fan_in,fan_out,afferent_coupling,efferent_coupling,instability,cycle_id,cycle_size)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (node_id, fan_in, fan_out, ca, ce, instability, cycle_id, cycle_size),
        )


def _record_changes(
    connection: sqlite3.Connection,
    generation: str,
    old_nodes: dict[str, dict[str, Any]],
    old_edges: dict[str, dict[str, Any]],
) -> dict[str, int]:
    connection.execute("DELETE FROM changes")
    new_nodes = _snapshot_nodes(connection)
    new_edges = _snapshot_edges(connection)
    counts: dict[str, int] = defaultdict(int)
    for entity_type, before, after in (("node", old_nodes, new_nodes), ("edge", old_edges, new_edges)):
        for entity_id in sorted(set(before) | set(after)):
            if entity_id not in before:
                change_type = "added"
            elif entity_id not in after:
                change_type = "removed"
            elif before[entity_id] != after[entity_id]:
                change_type = "changed"
            else:
                continue
            connection.execute(
                "INSERT INTO changes VALUES (?,?,?,?,?,?)",
                (
                    generation,
                    change_type,
                    entity_type,
                    entity_id,
                    json.dumps(before.get(entity_id), ensure_ascii=False, separators=(",", ":")) if entity_id in before else None,
                    json.dumps(after.get(entity_id), ensure_ascii=False, separators=(",", ":")) if entity_id in after else None,
                ),
            )
            counts[f"{entity_type}_{change_type}"] += 1
    return dict(counts)


def _apply_summary_enrichment(connection: sqlite3.Connection, provider: Any) -> int:
    if provider is None:
        return 0
    name = str(getattr(provider, "name", "")).strip()
    version = str(getattr(provider, "version", "")).strip()
    summarize = getattr(provider, "summarize", None)
    if not name or not version or not callable(summarize):
        raise CodeGraphError(
            "CODE_ENRICHMENT_PROVIDER_INVALID",
            "A code summary enrichment provider requires name, version, and summarize(node).",
        )
    enriched = 0
    for row in connection.execute(
        """
        SELECT node_id,kind,qualified_name,display_name,signature,docstring,summary,content_hash,metadata_json
        FROM nodes
        WHERE content_hash IS NOT NULL AND kind IN ('class','function','method')
        ORDER BY qualified_name
        """
    ):
        source_hash = str(row["content_hash"])
        exists = connection.execute(
            "SELECT 1 FROM enrichments WHERE node_id=? AND provider=? AND provider_version=? AND source_hash=?",
            (row["node_id"], name, version, source_hash),
        ).fetchone()
        if exists:
            continue
        payload = {
            "nodeId": str(row["node_id"]),
            "kind": str(row["kind"]),
            "qualifiedName": str(row["qualified_name"]),
            "displayName": str(row["display_name"]),
            "signature": row["signature"],
            "docstring": row["docstring"],
            "deterministicSummary": row["summary"],
            "contentHash": source_hash,
            "metadata": json.loads(row["metadata_json"]),
        }
        summary = summarize(payload)
        if summary is None:
            continue
        if not isinstance(summary, str) or not summary.strip():
            raise CodeGraphError("CODE_ENRICHMENT_OUTPUT_INVALID", "Code summary enrichment must return text or None.")
        connection.execute(
            "INSERT INTO enrichments(node_id,provider,provider_version,source_hash,summary) VALUES (?,?,?,?,?)",
            (row["node_id"], name, version, source_hash, summary.strip()),
        )
        enriched += 1
    _set_metadata(
        connection,
        {"summary_enrichment_provider": name, "summary_enrichment_version": version},
    )
    return enriched


def sync_code_graph(
    root: str | Path,
    *,
    source_roots: Iterable[str | Path] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    analysis_profile: str = "hybrid",
    store_dir: str | Path = ".documa",
    lock_timeout: float = SYNC_LOCK_TIMEOUT_SECONDS,
    enrichment_provider: Any = None,
    document_paths: Iterable[str] | None = None,
    languages: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Incrementally synchronize one Python repository into its derived graph."""

    if analysis_profile not in {"hybrid", "syntax"}:
        raise CodeGraphError("CODE_ANALYSIS_PROFILE_INVALID", f"Unknown analysis profile: {analysis_profile}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise CodeGraphError("CODE_ROOT_INVALID", f"Repository root is missing: {root_path}")
    normalized_roots = _normalize_source_roots(root_path, source_roots)
    scope = _index_scope(root_path, normalized_roots, include, exclude, analysis_profile, document_paths, languages)
    scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    workspace_id = workspace_id_for_root(root_path)
    workspace_node_id = stable_id("cws", workspace_id)
    path = index_path(workspace_id, store_dir)
    lock = FileLock(str(path.with_suffix(".lock")), timeout=lock_timeout)
    started = time.monotonic()
    try:
        with lock:
            digests = _source_manifest(root_path, scope)
            source_signature = "\n".join(f"{name}\0{digests[name]}" for name in sorted(digests))
            generation = hashlib.sha256(
                f"{CODE_GRAPH_ANALYZER_VERSION}\0{scope_json}\0{source_signature}".encode("utf-8")
            ).hexdigest()[:24]
            connection = _connect(path)
            try:
                current_metadata = _metadata(connection)
                previous_files = {
                    str(row["relative_path"]): row
                    for row in connection.execute("SELECT relative_path,digest,source_root FROM files")
                }
                previous_digests = {name: row["digest"] for name, row in previous_files.items()}
                if current_metadata.get("active_generation") == generation and previous_digests == digests:
                    with connection:
                        enriched = _apply_summary_enrichment(connection, enrichment_provider)
                    counts = graph_counts(connection)
                    return {
                        "status": "ok",
                        "workspace_id": workspace_id,
                        "generation": generation,
                        "previous_generation": current_metadata.get("previous_generation"),
                        "index_path": str(path),
                        "changed": False,
                        "parsed": 0,
                        "unchanged": len(digests),
                        "deleted": 0,
                        "enriched": enriched,
                        "coverage": graph_coverage(connection),
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        **counts,
                    }
                previous_scope = json.loads(current_metadata.get("scope", "{}"))
                reparse = (
                    current_metadata.get("analyzer_version") != CODE_GRAPH_ANALYZER_VERSION
                    or previous_scope.get("adapters") != scope["adapters"]
                    or current_metadata.get("analysis_profile") != analysis_profile
                )
                deleted_paths = sorted(set(previous_files) - set(digests))
                adapter = PythonCodeAdapter()
                documents = RepositoryDocumentAdapter()
                rust_adapter = RustCodeAdapter() if "rust" in scope["languages"] else None
                changed_manifests = any(
                    name.endswith("Cargo.toml") and previous_digests.get(name) != digests.get(name)
                    for name in previous_digests.keys() | digests.keys()
                )
                parsed_files: list[tuple[ParsedCodeFile, Path]] = []
                warnings: list[dict[str, Any]] = []
                for relative, digest in digests.items():
                    candidate = root_path / relative
                    source_root = _source_root_for(candidate, normalized_roots)
                    previous = previous_files.get(relative)
                    if (
                        not reparse and not (changed_manifests and relative.endswith(".rs")) and previous is not None
                        and previous["digest"] == digest and previous["source_root"] == source_root.as_posix()
                    ):
                        continue
                    try:
                        if rust_adapter is not None and rust_adapter.supports(relative):
                            parsed = rust_adapter.parse(str(root_path), str(source_root), str(candidate), workspace_id)
                        elif "python" in scope["languages"] and adapter.supports(relative):
                            parsed = adapter.parse(str(root_path), str(source_root), str(candidate), workspace_id)
                        else:
                            parsed = documents.parse(root_path, candidate, workspace_id)
                    except FileNotFoundError as exc:
                        raise CodeGraphError(
                            "CODE_SOURCE_CHANGED_DURING_SYNC", f"Source disappeared during graph sync: {relative}"
                        ) from exc
                    except OSError as exc:
                        raise CodeGraphError("CODE_GRAPH_SOURCE_UNAVAILABLE", f"Could not read source: {relative}") from exc
                    if digest != parsed.digest:
                        raise CodeGraphError("CODE_SOURCE_CHANGED_DURING_SYNC", f"Source changed during graph sync: {relative}")
                    parsed_files.append((parsed, source_root))
                    if parsed.parse_status == ParseStatus.UNAVAILABLE:
                        warnings.append(
                            {"code": parsed.error_code, "source_locator": relative, "message": parsed.error_message}
                        )
                old_nodes = _snapshot_nodes(connection)
                old_edges = _snapshot_edges(connection)
                dirty_files = None
                resolution_reason = "initial_or_analyzer_changed"
                if not reparse:
                    if deleted_paths or digests.keys() - previous_files.keys():
                        resolution_reason = "source_membership_changed"
                    elif not parsed_files:
                        dirty_files = set()
                        resolution_reason = "bindings_unchanged"
                    elif any(
                        parsed.language in {"rust", "cargo"} or (rust_adapter and parsed.relative_path == "setup.py")
                        for parsed, _ in parsed_files
                    ):
                        resolution_reason = "rust_or_cargo_changed"
                    else:
                        dirty_files = _local_resolution_files(connection, [parsed for parsed, _ in parsed_files])
                        resolution_reason = "global_bindings_changed" if dirty_files is None else "reverse_dependencies"
                with connection:
                    _insert_node(
                        connection,
                        CodeNode(
                            node_id=workspace_node_id,
                            kind=CodeNodeKind.WORKSPACE,
                            qualified_name=workspace_id,
                            display_name=root_path.name,
                            source_locator=".",
                            content_hash=f"sha256:{hashlib.sha256(source_signature.encode('utf-8')).hexdigest()}",
                            summary=f"Python workspace {root_path.name}.",
                            metadata={"analysisProfile": analysis_profile},
                        ),
                    )
                    for relative in deleted_paths:
                        row = connection.execute("SELECT file_id FROM files WHERE relative_path = ?", (relative,)).fetchone()
                        if row:
                            file_id = str(row["file_id"])
                            connection.execute("DELETE FROM structural_edges WHERE file_id = ?", (file_id,))
                            connection.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
                    for parsed, source_root in parsed_files:
                        _insert_parsed(connection, parsed, source_root, workspace_node_id)
                    if dirty_files is None or dirty_files:
                        _resolve_graph(connection, root_path, workspace_id, workspace_node_id, dirty_files)
                    enriched = _apply_summary_enrichment(connection, enrichment_provider)
                    change_counts = _record_changes(connection, generation, old_nodes, old_edges)
                    if _source_manifest(root_path, scope) != digests:
                        raise CodeGraphError("CODE_SOURCE_CHANGED_DURING_SYNC", "Source manifest changed during graph sync.")
                    _set_metadata(
                        connection,
                        {
                            "schema_version": str(CODE_GRAPH_SCHEMA_VERSION),
                            "analyzer_version": CODE_GRAPH_ANALYZER_VERSION,
                            "workspace_id": workspace_id,
                            "workspace_root": str(root_path),
                            "analysis_profile": analysis_profile,
                            "scope": scope_json,
                            "previous_generation": current_metadata.get("active_generation", ""),
                            "active_generation": generation,
                            "source_tree_hash": f"sha256:{hashlib.sha256(source_signature.encode('utf-8')).hexdigest()}",
                            "updated_at": str(time.time()),
                        },
                    )
                counts = graph_counts(connection)
                return {
                    "status": "ok" if not warnings else "warning",
                    "workspace_id": workspace_id,
                    "generation": generation,
                    "previous_generation": current_metadata.get("active_generation") or None,
                    "source_tree_hash": _metadata(connection).get("source_tree_hash"),
                    "index_path": str(path),
                    "changed": True,
                    "parsed": len(parsed_files),
                    "unchanged": len(digests) - len(parsed_files),
                    "deleted": len(deleted_paths),
                    "warnings": warnings,
                    "enriched": enriched,
                    "changes": change_counts,
                    "resolution": {
                        "mode": "full" if dirty_files is None else "scoped" if dirty_files else "unchanged",
                        "files": len(digests) if dirty_files is None else len(dirty_files),
                        "reason": resolution_reason,
                    },
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "coverage": graph_coverage(connection),
                    **counts,
                }
            finally:
                connection.close()
    except Timeout as exc:
        raise CodeGraphError("CODE_GRAPH_LOCK_TIMEOUT", f"Timed out waiting for code graph lock: {path}") from exc


def graph_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "file_count": int(connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
        "node_count": int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]),
        "edge_count": int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]),
        "occurrence_count": int(connection.execute("SELECT COUNT(*) FROM occurrences").fetchone()[0]),
        "blindspot_count": int(connection.execute("SELECT COUNT(*) FROM blindspots").fetchone()[0]),
        "cycle_count": int(connection.execute("SELECT COUNT(DISTINCT cycle_id) FROM metrics WHERE cycle_id IS NOT NULL").fetchone()[0]),
    }


def graph_coverage(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    coverage = {
        str(row["language"]): {
            "discovered": row["discovered"], "parsed": row["parsed"],
            "skipped": row["discovered"] - row["parsed"], "unresolved": 0,
        }
        for row in connection.execute("""
            SELECT language,COUNT(*) AS discovered,SUM(parse_status = 'ok') AS parsed
            FROM files GROUP BY language ORDER BY language
        """)
    }
    for row in connection.execute("""
        SELECT files.language, COUNT(*) AS unresolved FROM blindspots JOIN files USING(file_id) GROUP BY files.language
    """):
        coverage[row["language"]]["unresolved"] = row["unresolved"]
    if "rust" in coverage:
        coverage["rust"]["unresolved"] += connection.execute("""
            SELECT COUNT(*) FROM occurrences o WHERE o.role IN ('rust_call','rust_export')
            AND NOT EXISTS (SELECT 1 FROM edges WHERE occurrence_id = o.occurrence_id)
        """).fetchone()[0]
    return coverage


def open_code_graph(workspace_id: str, store_dir: str | Path = ".documa") -> sqlite3.Connection:
    path = index_path(workspace_id, store_dir)
    if not path.is_file():
        raise CodeGraphError("CODE_GRAPH_NOT_FOUND", f"Code graph index not found for workspace: {workspace_id}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    if int(connection.execute("PRAGMA user_version").fetchone()[0]) != CODE_GRAPH_SCHEMA_VERSION:
        connection.close()
        raise CodeGraphError("CODE_GRAPH_VERSION_OUTDATED", "Code graph index version is not supported; rebuild it.")
    if not connection.execute("SELECT 1 FROM metadata WHERE key = 'active_generation'").fetchone():
        connection.close()
        raise CodeGraphError("CODE_GRAPH_NOT_FOUND", f"Code graph index is empty for workspace: {workspace_id}")
    return connection


def _freshness(connection: sqlite3.Connection, metadata: dict[str, str]) -> dict[str, Any]:
    if "scope" not in metadata:
        return {"state": "stale", "reason": "index_scope_missing"}
    root = Path(metadata["workspace_root"])
    scope = json.loads(metadata["scope"])
    try:
        roots = _normalize_source_roots(root, scope["source_roots"])
        current = _index_scope(
            root, roots, scope["include"], scope["exclude"], scope["analysis_profile"],
            scope.get("document_paths"), scope.get("languages"),
        )
        if current["adapters"] != scope["adapters"]:
            return {"state": "stale", "reason": "adapter_version_changed"}
        if current != scope:
            return {"state": "stale", "reason": "scope_changed"}
        observed = _source_manifest(root, current)
    except CodeGraphError as exc:
        return {"state": "stale", "reason": exc.code}
    indexed = {row["relative_path"]: row["digest"] for row in connection.execute("SELECT relative_path,digest FROM files")}
    if indexed == observed:
        return {"state": "fresh"}
    return {
        "state": "stale",
        "reason": "source_manifest_changed",
        "added": len(observed.keys() - indexed.keys()),
        "removed": len(indexed.keys() - observed.keys()),
        "modified": sum(indexed[name] != observed[name] for name in observed.keys() & indexed.keys()),
    }


def code_graph_status(workspace_id: str, store_dir: str | Path = ".documa") -> dict[str, Any]:
    connection = open_code_graph(workspace_id, store_dir)
    try:
        metadata = _metadata(connection)
        return {"status": "ok", **metadata, **graph_counts(connection),
                "coverage": graph_coverage(connection), "freshness": _freshness(connection, metadata)}
    finally:
        connection.close()


def clear_code_graph(workspace_id: str, store_dir: str | Path = ".documa") -> dict[str, Any]:
    """Atomically clear only a registered workspace's derived records."""
    path = index_path(workspace_id, Path(store_dir).resolve())
    if not path.is_file():
        return {"status": "ok", "workspace_id": workspace_id, "cleared": False}
    if path.resolve() != path:
        raise CodeGraphError("CODE_GRAPH_STORE_INVALID", "Code graph index path resolves outside its registered location.")
    try:
        with FileLock(str(path.with_suffix(".lock")), timeout=SYNC_LOCK_TIMEOUT_SECONDS):
            try:
                reader = open_code_graph(workspace_id, store_dir)
            except CodeGraphError as exc:
                if exc.code == "CODE_GRAPH_NOT_FOUND":
                    return {"status": "ok", "workspace_id": workspace_id, "cleared": False}
                raise
            try:
                if _metadata(reader).get("workspace_id") != workspace_id:
                    raise CodeGraphError("CODE_GRAPH_STORE_INVALID", "Index metadata does not match the requested workspace.")
            finally:
                reader.close()
            connection = _connect(path)
            try:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for table in (
                        "edges", "structural_edges", "metrics", "blindspots", "changes",
                        "enrichments", "occurrences", "nodes", "files", "metadata",
                    ):
                        connection.execute(f"DELETE FROM {table}")
                return {"status": "ok", "workspace_id": workspace_id, "cleared": True}
            finally:
                connection.close()
    except Timeout as exc:
        raise CodeGraphError("CODE_GRAPH_LOCK_TIMEOUT", f"Timed out waiting for code graph lock: {path}") from exc
