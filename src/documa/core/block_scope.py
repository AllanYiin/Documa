"""Hierarchy helpers shared by scoped and branch-aware retrieval."""

from __future__ import annotations

from typing import Any


def descendant_ids(block_id: str, by_id: dict[str, Any], *, include_self: bool = True) -> set[str]:
    output = {block_id} if include_self else set()
    pending = list(by_id.get(block_id).child_ids) if block_id in by_id else []
    while pending:
        child_id = pending.pop(0)
        if child_id in output:
            continue
        output.add(child_id)
        child = by_id.get(child_id)
        if child is not None:
            pending.extend(child.child_ids)
    return output


def is_ancestor(ancestor_id: str, descendant_id: str, by_id: dict[str, Any]) -> bool:
    current = by_id.get(descendant_id)
    seen: set[str] = set()
    while current is not None and current.parent_id and current.parent_id not in seen:
        if current.parent_id == ancestor_id:
            return True
        seen.add(current.parent_id)
        current = by_id.get(current.parent_id)
    return False


def top_branch_id(block_id: str, by_id: dict[str, Any]) -> str:
    current = by_id.get(block_id)
    branch_id = block_id
    while current is not None and current.parent_id:
        parent = by_id.get(current.parent_id)
        if parent is None:
            break
        branch_id = current.id
        current = parent
    return branch_id
