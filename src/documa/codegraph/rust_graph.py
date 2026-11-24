"""Resolve explicit crate, Rust call, and Python/PyO3 binding relationships."""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path, PurePosixPath
from collections.abc import Iterator

from documa.codegraph.models import CodeEdge, CodeEdgeType, CodeSpan, EdgeResolution, edge_id, sha256_bytes
from documa.codegraph.rust_adapter import RustCodeAdapter


def _extension_modules(root: Path, setup_digest: str | None) -> dict[str, tuple[str, CodeSpan]]:
    """Read setuptools-rust declarations as data and keep their provenance."""
    if not setup_digest:
        return {}
    source = (root / "setup.py").resolve()
    if root not in source.parents or not source.is_file():
        return {}
    raw = source.read_bytes()
    if sha256_bytes(raw) != setup_digest:
        return {}
    try:
        tree = ast.parse(raw, filename="setup.py")
    except SyntaxError:
        return {}
    constructors = {
        named.asname or named.name
        for statement in ast.walk(tree) if isinstance(statement, ast.ImportFrom) and statement.module == "setuptools_rust"
        for named in statement.names if named.name == "RustExtension"
    }
    extensions = {}
    ambiguous = set()
    for item in ast.walk(tree):
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Name) or item.func.id not in constructors:
            continue
        if not item.args or not isinstance(item.args[0], ast.Constant) or not isinstance(item.args[0].value, str):
            continue
        manifest_node = next((kw.value for kw in item.keywords if kw.arg == "path"), None)
        if not isinstance(manifest_node, ast.Constant) or not isinstance(manifest_node.value, str):
            continue
        manifest = (root / manifest_node.value).resolve()
        if root not in manifest.parents:
            continue
        name = item.args[0].value
        if name in extensions:
            ambiguous.add(name)
        extensions[name] = (manifest.relative_to(root).as_posix(), CodeSpan(item.lineno, item.end_lineno))
    return {name: value for name, value in extensions.items() if name not in ambiguous}


def _rust_qualified(raw: str, module: str, aliases: dict[str, str]) -> str:
    first, _, rest = raw.partition("::")
    if first == "crate":
        base = module.split("::", 1)[0]
    elif first == "self":
        base = module
    elif first == "super":
        base = module.rsplit("::", 1)[0]
    elif first in aliases:
        base = aliases[first]
    elif rest:
        return raw
    else:
        return f"{module}::{raw}"
    return f"{base}::{rest}" if rest else base


def _edge(source: str, target: str, relation: CodeEdgeType, occurrence: sqlite3.Row, meta: dict) -> CodeEdge:
    return CodeEdge(
        edge_id(source, target, relation, occurrence["occurrence_id"]), source, target, relation,
        EdgeResolution.RESOLVED, RustCodeAdapter.version,
        evidence_occurrence_id=occurrence["occurrence_id"], evidence_file_id=occurrence["file_id"],
        evidence_span=CodeSpan(occurrence["start_line"], occurrence["end_line"], occurrence["start_column"], occurrence["end_column"]),
        evidence_hash=json.loads(occurrence["metadata_json"]).get("sourceDigest"), metadata=meta,
    )


def resolved_rust_edges(
    connection: sqlite3.Connection,
    root: Path,
    python_imports: dict[str, dict[str, tuple[str, str | None]]],
) -> Iterator[CodeEdge]:
    nodes: dict[str, sqlite3.Row] = {}
    metadata = {}
    symbols: dict[str, list[str]] = defaultdict(list)
    manifests: dict[str, str] = {}
    for node in connection.execute("SELECT * FROM nodes ORDER BY node_id"):
        node_id = node["node_id"]
        nodes[node_id] = node
        info = json.loads(node["metadata_json"])
        metadata[node_id] = info
        if info.get("language") == "rust":
            symbols[node["qualified_name"]].append(node_id)
        elif node["kind"] == "crate":
            manifests[info["manifest"]] = node_id

    all_occurrences: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for occurrence in connection.execute("""
        SELECT * FROM occurrences
        WHERE role IN ('cargo_dependency','rust_use','rust_call','rust_export','call','callback','import')
        ORDER BY file_id,start_line,occurrence_id
    """):
        all_occurrences[occurrence["role"]].append(occurrence)

    for locator, crate_id in manifests.items():
        members = metadata[crate_id]["workspaceMembers"]
        if members:
            workspace_root = PurePosixPath(locator).parent
            for member_locator, member_id in manifests.items():
                candidate = PurePosixPath(member_locator).parent
                if crate_id == member_id or workspace_root not in candidate.parents:
                    continue
                relative = candidate.relative_to(workspace_root).as_posix()
                if any(fnmatch.fnmatchcase(relative, str(pattern).rstrip("/")) for pattern in members):
                    workspace_node = nodes[crate_id]
                    yield CodeEdge(
                        edge_id(crate_id, member_id, CodeEdgeType.CONTAINS), crate_id, member_id,
                        CodeEdgeType.CONTAINS, EdgeResolution.EXACT, RustCodeAdapter.version,
                        evidence_file_id=workspace_node["file_id"],
                        evidence_span=CodeSpan(workspace_node["start_line"], workspace_node["end_line"]),
                    )

    workspaces: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
    for dependency in all_occurrences["cargo_dependency"]:
        info = json.loads(dependency["metadata_json"])
        if info["condition"] == "workspace":
            workspaces[info["sourceLocator"]][info["alias"]] = dependency
    for dependency in all_occurrences["cargo_dependency"]:
        info = json.loads(dependency["metadata_json"])
        source = dependency["source_node_id"]
        declared_at = info["sourceLocator"]
        path = info["path"]
        if info["inherits"]:
            for folder in PurePosixPath(declared_at).parents:
                inherited = workspaces.get((folder / "Cargo.toml").as_posix(), {}).get(info["alias"])
                if inherited:
                    declaration = json.loads(inherited["metadata_json"])
                    path, declared_at = declaration["path"], declaration["sourceLocator"]
                    break
        if path:
            destination = (root / declared_at).parent.joinpath(path, "Cargo.toml").resolve()
            if root in destination.parents and (target := manifests.get(destination.relative_to(root).as_posix())):
                yield _edge(source, target, CodeEdgeType.DEPENDS_ON_CRATE, dependency, {
                    "alias": info["alias"], "dependencyGroup": info["group"], "declaredAt": declared_at,
                })

    module_aliases: dict[str, dict[str, str]] = defaultdict(dict)
    for occurrence in all_occurrences["rust_use"]:
        match = re.fullmatch(r"([\w]+(?:::[\w]+)+)(?:\s+as\s+(\w+))?", occurrence["text"])
        if match:
            module = json.loads(occurrence["metadata_json"])["module"]
            module_aliases[module][match[2] or match[1].split("::")[-1]] = _rust_qualified(match[1], module, {})
    for occurrence in all_occurrences["rust_call"]:
        module = json.loads(occurrence["metadata_json"])["module"]
        target = _rust_qualified(occurrence["text"], module, module_aliases[module])
        candidates = [node_id for node_id in symbols.get(target, ()) if nodes[node_id]["kind"] in {"function", "method"}]
        if len(candidates) == 1:
            yield _edge(occurrence["source_node_id"], candidates[0], CodeEdgeType.CALLS, occurrence, {"language": "rust"})

    setup = connection.execute("SELECT digest FROM files WHERE relative_path = 'setup.py'").fetchone()
    extensions = _extension_modules(root, setup["digest"] if setup else None)
    exports: dict[str, list[tuple[str, sqlite3.Row, CodeSpan]]] = defaultdict(list)
    for occurrence in all_occurrences["rust_export"]:
        origin = occurrence["source_node_id"]
        info = json.loads(occurrence["metadata_json"])
        module = info["module"]
        target = _rust_qualified(occurrence["text"], module, module_aliases[module])
        # An unprefixed scoped expression in a registration can name a local module.
        if target not in symbols and not target.startswith(module + "::"):
            target = f"{module}::{target}"
        candidates = [node_id for node_id in symbols.get(target, ()) if metadata[node_id].get("pyRole") == info["pyRole"]]
        if len(candidates) != 1:
            continue
        export = candidates[0]
        yield _edge(origin, export, CodeEdgeType.EXPORTS, occurrence, {"pyName": metadata[export]["pyName"]})
        for extension, (manifest, setup_span) in extensions.items():
            if manifest == metadata[origin].get("manifest") and extension.rsplit(".", 1)[-1] == info["pyModule"]:
                exports[f"{extension}.{metadata[export]['pyName']}"].append((export, occurrence, setup_span))

    for role in ("call", "callback", "import"):
        for occurrence in all_occurrences[role]:
            info = json.loads(occurrence["metadata_json"])
            if role == "import":
                first = info.get("alias") or info.get("name") or info.get("module", "").split(".", 1)[0]
                rest = ""
            else:
                first, _, rest = str(info.get("target") or occurrence["text"]).partition(".")
            imported = python_imports.get(occurrence["file_id"], {}).get(first)
            source = occurrence["source_node_id"]
            if not imported or imported[0] not in nodes or source not in nodes:
                continue
            # Bindings require an actual import of the extension or exported object.
            name = nodes[imported[0]]["qualified_name"] + (f".{rest}" if rest else "")
            candidates = exports.get(name, ())
            if len(candidates) != 1:
                continue
            target, registration, setup_span = candidates[0]
            yield _edge(source, target, CodeEdgeType.BINDS, occurrence, {
                "pythonName": name, "registrationId": registration["occurrence_id"],
                "registrationLocator": json.loads(registration["metadata_json"])["sourceLocator"],
                "registrationSourceHash": json.loads(registration["metadata_json"])["sourceDigest"],
                "setupLocator": "setup.py", "setupSourceHash": setup["digest"],
                "setupSpan": {"startLine": setup_span.start_line, "endLine": setup_span.end_line},
            })
