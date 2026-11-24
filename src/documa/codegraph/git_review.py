"""Map committed and working tree changes to graph definitions and evidence."""

from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from contextlib import ExitStack, closing
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from documa.codegraph.git_adapter import (
    GIT_SNAPSHOT_MAX_BYTES, GIT_SNAPSHOT_MAX_FILES, GitChange, git_blob_bytes, git_changes, git_commit, git_tree, require_git_root,
)
from documa.codegraph.models import sha256_bytes, sha256_text
from documa.codegraph.store import (
    CodeGraphError, _freshness, _is_selected, _metadata, open_code_graph, sync_code_graph,
)


_HISTORY_PREFIX = "git-proof:"
_HISTORICAL_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _in_scope(scope: dict[str, Any], relative: str) -> bool:
    return _is_selected(relative, scope["include"], scope["exclude"], set(scope["document_paths"]), set(scope["languages"]))


def _worktree_body(root: Path, relative: str | None) -> bytes:
    if not relative:
        return b""
    source = (root / relative).resolve()
    if source != root / relative or root not in source.parents or not source.is_file():
        return b""
    return source.read_bytes()


def _make_snapshot(
    root: Path, revision: str, scope: dict[str, Any], destination: Path, changed_paths: set[str | None],
) -> tuple[Path, str, dict[str, bytes], dict[str, Any]]:
    entries = git_tree(root, revision, lambda name: _in_scope(scope, name))
    selected = sorted(entries.values(), key=lambda blob: (blob.path not in changed_paths, blob.path))
    available = sum(blob.size for blob in selected)
    if len(selected) > GIT_SNAPSHOT_MAX_FILES or available > GIT_SNAPSHOT_MAX_BYTES:
        selected = [blob for blob in selected if blob.path in changed_paths or blob.path.endswith("Cargo.toml")]
        bounded = []
        used_bytes = 0
        for blob in selected:
            if len(bounded) >= GIT_SNAPSHOT_MAX_FILES:
                break
            if used_bytes + blob.size <= GIT_SNAPSHOT_MAX_BYTES:
                bounded.append(blob)
                used_bytes += blob.size
        selected = bounded
    blobs = git_blob_bytes(root, selected)
    files_root = destination / "files"
    files_root.mkdir()
    for relative, raw in blobs.items():
        path = files_root / relative
        resolved = path.resolve()
        if files_root not in resolved.parents or path.exists():
            raise CodeGraphError("CODE_GRAPH_GIT_PATH_INVALID", "Historical source paths cannot be materialized safely.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    store = destination / "index"
    sync = sync_code_graph(
        files_root, source_roots=[name for name in scope["source_roots"] if (files_root / name).is_dir()],
        include=scope["include"], exclude=scope["exclude"], analysis_profile=scope["analysis_profile"], store_dir=store,
        document_paths=scope["document_paths"], languages=scope["languages"],
    )
    return store, sync["workspace_id"], blobs, {
        "availableFiles": len(entries), "indexedFiles": len(blobs), "partial": len(blobs) != len(entries),
    }


def _hunks(before: bytes, after: bytes, renamed: bool) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    left = before.decode("utf-8", errors="replace").splitlines()
    right = after.decode("utf-8", errors="replace").splitlines()
    if renamed and before == after:
        return ([(1, len(left))], [(1, len(right))]) if left else ([], [])
    first, second = [], []
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(None, left, right).get_opcodes():
        if tag != "equal":
            if old_start < old_end:
                first.append((old_start + 1, old_end))
            if new_start < new_end:
                second.append((new_start + 1, new_end))
    return first, second


def _intersecting_symbols(connection, path: str | None, ranges: list[tuple[int, int]], limit: int) -> list[dict[str, Any]]:
    if not path or not ranges:
        return []
    rows = list(connection.execute("""
        SELECT node_id, qualified_name, kind, start_line, end_line
        FROM nodes WHERE source_locator = ? AND kind IN ('function','method','class','document_block','crate','rust_module','module')
        ORDER BY (end_line-start_line),start_line,node_id
    """, (path,)))
    selected = [row for row in rows if row["start_line"] and any(row["start_line"] <= end and start <= row["end_line"] for start, end in ranges)]
    concrete = [row for row in selected if row["kind"] not in {"module", "rust_module", "crate"}]
    return [{"nodeId": row["node_id"], "qualifiedName": row["qualified_name"], "sourceLocator": path,
             "sourceSpan": {"startLine": row["start_line"], "endLine": row["end_line"]}}
            for row in (concrete or selected)[:limit]]


def _historical_reference(revision: str, block: dict[str, Any]) -> str:
    locator = base64.urlsafe_b64encode(block["sourceLocator"].encode("utf-8")).decode("ascii")
    span = block["sourceSpan"]
    return f"{_HISTORY_PREFIX}{revision}:{locator}:{span['startLine']}:{span['endLine']}:{block['sourceHash'][7:]}"


def read_historical_evidence(root: Path, scope: dict[str, Any], reference: str) -> dict[str, Any]:
    fields = reference.split(":")
    if len(fields) != 6 or fields[0] != "git-proof" or not _HISTORICAL_OID.fullmatch(fields[1]):
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence reference is invalid.")
    _, revision, encoded, begin, finish, digest = fields
    try:
        locator = base64.b64decode(encoded, altchars=b"-_", validate=True).decode("utf-8")
        start, end = int(begin), int(finish)
    except (UnicodeDecodeError, ValueError) as exc:
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence location is invalid.") from exc
    if not 1 <= start <= end or end - start >= 500 or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence span is invalid.")
    require_git_root(root)
    if git_commit(root, revision) != revision:
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence revision is not a commit.")
    entries = git_tree(root, revision, lambda relative: relative == locator and _in_scope(scope, relative))
    if locator not in entries:
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence path is outside the indexed scope.")
    raw = git_blob_bytes(root, [entries[locator]])[locator]
    if sha256_bytes(raw) != f"sha256:{digest}":
        raise CodeGraphError("CODE_GRAPH_BODY_HASH_MISMATCH", "The historical Git blob hash does not match the reference.")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise CodeGraphError("CODE_GRAPH_BLOCK_UNREADABLE", "Historical evidence is not UTF-8 text.") from exc
    if end > len(lines):
        raise CodeGraphError("CODE_GRAPH_GIT_EVIDENCE_INVALID", "The historical evidence span is outside the source.")
    body = "\n".join(lines[start - 1:end])
    return {"blockId": reference, "body": body, "sourceLocator": locator, "sourceSpan": {"startLine": start, "endLine": end},
            "sourceHash": f"sha256:{digest}", "contentHash": sha256_text(body), "revision": revision,
            "sourceFreshness": "immutable", "evidenceKind": "git"}


def _rename_worktree(changes: list[GitChange], before: dict[str, bytes], root: Path) -> list[GitChange]:
    removed: dict[str, list[str]] = defaultdict(list)
    added: dict[str, list[str]] = defaultdict(list)
    for change in changes:
        if change.kind == "deleted" and change.before in before:
            removed[sha256_bytes(before[change.before])].append(change.before)
        if change.kind == "added" and change.after:
            raw = _worktree_body(root, change.after)
            if raw:
                added[sha256_bytes(raw)].append(change.after)
    renames = {values[0]: added[digest][0] for digest, values in removed.items() if len(values) == len(added.get(digest, [])) == 1}
    return [change for change in changes if change.before not in renames and change.after not in renames.values()] + [
        GitChange("renamed", old, new) for old, new in renames.items()
    ]


def git_diff_context(
    workspace_id: str, *, base_ref: str, head_ref: str, expected_generation: str | None,
    max_nodes: int, max_hops: int, store_dir: str | Path,
) -> dict[str, Any]:
    from documa.codegraph.service import query_code_graph, read_code_evidence

    with ExitStack() as stack:
        active = stack.enter_context(closing(open_code_graph(workspace_id, store_dir)))
        metadata = _metadata(active)
        generation = metadata["active_generation"]
        if expected_generation is not None and expected_generation != generation:
            raise CodeGraphError("CODE_GRAPH_STALE", "Code graph generation changed before Git comparison.")
        if "scope" not in metadata:
            raise CodeGraphError("CODE_GRAPH_STALE", "Synchronize the selected repository scope before Git comparison.")
        root = Path(metadata["workspace_root"])
        scope = json.loads(metadata["scope"])
        require_git_root(root)
        base = git_commit(root, base_ref)
        head = head_ref if head_ref == "worktree" else git_commit(root, head_ref)
        if head == "worktree" and _freshness(active, metadata)["state"] != "fresh":
            raise CodeGraphError("CODE_GRAPH_STALE", "Synchronize the working tree index before Git comparison.")
        changes = [change for change in git_changes(root, base, head) if any(
            name and _in_scope(scope, name) for name in (change.before, change.after)
        )]
        if not changes:
            return {"workspaceId": workspace_id, "generation": generation, "intent": "git_diff", "base": base, "head": head, "changes": []}

        base_temp = Path(stack.enter_context(TemporaryDirectory(prefix="documa-git-base-")))
        base_store, base_id, before, base_coverage = _make_snapshot(root, base, scope, base_temp, {change.before for change in changes})
        base_graph = stack.enter_context(closing(open_code_graph(base_id, base_store)))
        if head == "worktree":
            after = {change.after: _worktree_body(root, change.after) for change in changes if change.after and _in_scope(scope, change.after)}
            changes = _rename_worktree(changes, before, root)
            head_graph, head_id, head_store = active, workspace_id, store_dir
            head_coverage = {"partial": False, "index": "working_tree"}
        else:
            head_temp = Path(stack.enter_context(TemporaryDirectory(prefix="documa-git-head-")))
            head_store, head_id, after, head_coverage = _make_snapshot(root, head, scope, head_temp, {change.after for change in changes})
            head_graph = stack.enter_context(closing(open_code_graph(head_id, head_store)))

        reported = []
        for change in changes[:max_nodes]:
            first, second = _hunks(before.get(change.before, b""), after.get(change.after, b""), change.kind == "renamed")
            old = _intersecting_symbols(base_graph, change.before, first, max_nodes)
            new = _intersecting_symbols(head_graph, change.after, second, max_nodes)
            entry: dict[str, Any] = {
                "changeType": change.kind, "beforePath": change.before, "afterPath": change.after,
                "beforeLines": first, "afterLines": second, "beforeSymbols": old, "afterSymbols": new,
                "beforeSourceIndexed": change.before in before, "afterSourceIndexed": change.after in after,
            }
            for key, revision, endpoint_id, endpoint_store, candidates in (
                ("before", base, base_id, base_store, old), ("after", head, head_id, head_store, new),
            ):
                if not candidates:
                    continue
                node_ids = [node["nodeId"] for node in candidates]
                impact = query_code_graph(endpoint_id, intent="impact", symbols=node_ids, max_nodes=max_nodes,
                                          max_hops=max_hops, store_dir=endpoint_store)
                entry[f"{key}Impact"] = {field: impact[field] for field in ("nodes", "edges", "proofPaths", "impactReceipt")}
                ids = (impact.get("recommendedNext") or [{}])[0].get("arguments", {}).get("block_ids", [])
                if ids:
                    materialized = read_code_evidence(endpoint_id, ids, store_dir=endpoint_store, max_block_lines=24)["blocks"]
                    for block in materialized:
                        if revision != "worktree" and block["sourceSpan"]:
                            block["revision"] = revision
                            block["blockId"] = _historical_reference(revision, block)
                    entry[f"{key}Evidence"] = materialized
            reported.append(entry)
        if head == "worktree" and _freshness(active, metadata)["state"] != "fresh":
            raise CodeGraphError("CODE_GRAPH_STALE", "The working tree changed during the Git comparison.")
        return {"workspaceId": workspace_id, "generation": generation, "intent": "git_diff", "base": base, "head": head,
                "coverage": {"base": base_coverage, "head": head_coverage},
                "changeCount": len(changes), "truncated": len(changes) > len(reported), "changes": reported}
