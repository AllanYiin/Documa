"""Bounded local Git snapshots for code graph comparisons."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from documa.codegraph.store import CodeGraphError


_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_SNAPSHOT_MAX_BYTES = 48_000_000
GIT_SNAPSHOT_MAX_FILES = 4000


@dataclass(frozen=True, slots=True)
class GitBlob:
    path: str
    oid: str
    size: int


@dataclass(frozen=True, slots=True)
class GitChange:
    kind: str
    before: str | None
    after: str | None


def _git(root: Path, args: list[str], *, data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.fsmonitor=false", *args],
            input=data, capture_output=True, check=True, timeout=45,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodeGraphError("CODE_GRAPH_GIT_FAILED", "Could not read the requested local Git objects.") from exc
    return result.stdout


def git_commit(root: Path, ref: str) -> str:
    if not ref or "\0" in ref or "\n" in ref or ref == "worktree":
        raise CodeGraphError("CODE_GRAPH_GIT_REF_INVALID", "A local Git commit or ref is required.")
    try:
        oid = _git(root, ["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"]).decode("ascii").strip()
    except (CodeGraphError, UnicodeDecodeError) as exc:
        raise CodeGraphError("CODE_GRAPH_GIT_REF_INVALID", f"Could not resolve the requested local Git ref: {ref}") from exc
    if not _OID.fullmatch(oid):
        raise CodeGraphError("CODE_GRAPH_GIT_REF_INVALID", "Git did not return a commit object ID.")
    return oid


def require_git_root(root: Path) -> None:
    git_root = Path(os.fsdecode(_git(root, ["rev-parse", "--show-toplevel"]).rstrip(b"\r\n"))).resolve()
    if git_root != root:
        raise CodeGraphError("CODE_GRAPH_GIT_ROOT_REQUIRED", "Git comparison requires an index scoped to the repository root.")


def _relative(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CodeGraphError("CODE_GRAPH_GIT_PATH_INVALID", "Git path is not valid UTF-8.") from exc
    parts = text.split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise CodeGraphError("CODE_GRAPH_GIT_PATH_INVALID", "Git path is not a portable repository-relative path.")
    if os.name == "nt" and any(":" in part or part.endswith((".", " ")) or PureWindowsPath(part).is_reserved() for part in parts):
        raise CodeGraphError("CODE_GRAPH_GIT_PATH_INVALID", "A historical path cannot be represented safely on Windows.")
    if any(PurePosixPath(part).name.casefold() == ".git" for part in parts):
        raise CodeGraphError("CODE_GRAPH_GIT_PATH_INVALID", "Git metadata paths are outside the source scope.")
    return text


def git_changes(root: Path, base: str, head: str) -> list[GitChange]:
    args = ["diff", "--no-ext-diff", "--no-textconv", "--find-renames", "--name-status", "-z", base]
    if head != "worktree":
        args.append(head)
    fields = iter(_git(root, [*args, "--"]).rstrip(b"\0").split(b"\0"))
    changes = []
    for status in fields:
        if not status:
            continue
        old_path = _relative(next(fields))
        if status[:1] in {b"R", b"C"}:
            changes.append(GitChange("renamed" if status[:1] == b"R" else "copied", old_path, _relative(next(fields))))
        elif status == b"A":
            changes.append(GitChange("added", None, old_path))
        elif status == b"D":
            changes.append(GitChange("deleted", old_path, None))
        else:
            changes.append(GitChange("modified", old_path, old_path))
    if head == "worktree":
        for entry in _git(root, ["ls-files", "--others", "--exclude-standard", "-z"]).split(b"\0"):
            if entry:
                changes.append(GitChange("added", None, _relative(entry)))
    return changes


def git_tree(root: Path, revision: str, selected: Callable[[str], bool]) -> dict[str, GitBlob]:
    blobs = {}
    for entry in _git(root, ["ls-tree", "-r", "-l", "-z", revision]).split(b"\0"):
        if not entry:
            continue
        header, _, raw_path = entry.partition(b"\t")
        mode, kind, oid, size = header.decode("ascii").split()
        if kind == "blob" and mode in {"100644", "100755"}:
            relative = _relative(raw_path)
            if selected(relative):
                blobs[relative] = GitBlob(relative, oid, int(size))
    return blobs


def git_blob_bytes(root: Path, blobs: list[GitBlob]) -> dict[str, bytes]:
    if len(blobs) > GIT_SNAPSHOT_MAX_FILES or sum(blob.size for blob in blobs) > GIT_SNAPSHOT_MAX_BYTES:
        raise CodeGraphError("CODE_GRAPH_GIT_SNAPSHOT_LIMIT", "The selected Git source snapshot exceeds the local analysis budget.")
    if not blobs:
        return {}
    data = _git(root, ["cat-file", "--batch"], data="".join(f"{blob.oid}\n" for blob in blobs).encode("ascii"))
    output = {}
    cursor = 0
    for blob in blobs:
        newline = data.find(b"\n", cursor)
        if newline < 0 or data[cursor:newline] != f"{blob.oid} blob {blob.size}".encode("ascii"):
            raise CodeGraphError("CODE_GRAPH_GIT_BLOB_INVALID", "Git returned an unexpected historical blob header.")
        cursor = newline + 1
        output[blob.path] = data[cursor:cursor + blob.size]
        cursor += blob.size
        if data[cursor:cursor + 1] != b"\n":
            raise CodeGraphError("CODE_GRAPH_GIT_BLOB_INVALID", "Git returned a truncated historical blob.")
        cursor += 1
    return output
