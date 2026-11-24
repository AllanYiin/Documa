"""Foreground polling of an explicitly selected code graph workspace."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from documa.codegraph.store import CodeGraphError, code_graph_status, sync_code_graph, workspace_id_for_root


def watch_code_graph(
    root: str | Path,
    *,
    source_roots: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    analysis_profile: str = "hybrid",
    store_dir: str | Path = ".documa",
    document_paths: list[str] | None = None,
    languages: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Coalesce changes between polls and report committed generations."""
    root = Path(root).resolve()
    workspace_id = workspace_id_for_root(root)
    retries = 0
    synchronize = True
    while True:
        try:
            if not synchronize:
                try:
                    status = code_graph_status(workspace_id, store_dir)
                    synchronize = status["freshness"]["state"] != "fresh"
                except CodeGraphError as exc:
                    if exc.code != "CODE_GRAPH_NOT_FOUND":
                        raise
                    synchronize = True
            if synchronize:
                yield sync_code_graph(
                    root,
                    source_roots=source_roots,
                    include=include,
                    exclude=exclude,
                    analysis_profile=analysis_profile,
                    store_dir=store_dir,
                    lock_timeout=0.5,
                    document_paths=document_paths,
                    languages=languages,
                )
                synchronize = False
            retries = 0
        except CodeGraphError as exc:
            if exc.code not in {
                "CODE_GRAPH_LOCK_TIMEOUT", "CODE_SOURCE_CHANGED_DURING_SYNC", "CODE_GRAPH_SOURCE_UNAVAILABLE",
            } or retries == 3:
                raise
            retries += 1
        time.sleep(0.5 * 2**retries)
