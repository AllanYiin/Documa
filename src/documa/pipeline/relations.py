"""Shared helpers for relation-oriented pipeline stages."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from documa.core.ir import BlockIR, DocumentIR, RelationIR, RelationType


_SPACES = re.compile(r"\s+")


def block_text(block: BlockIR) -> str:
    if block.text is None:
        return ""
    return block.text.normalized_text or block.text.raw_text


def normalized_key(text: str) -> str:
    """Normalize labels for conservative exact-ish matching."""

    return _SPACES.sub(" ", text).strip().casefold()


def iter_blocks(document: DocumentIR) -> Iterable[BlockIR]:
    for page in document.pages:
        yield from page.blocks


def block_by_id(document: DocumentIR) -> dict[str, BlockIR]:
    return {block.id: block for block in iter_blocks(document)}


def has_relation(
    document: DocumentIR,
    relation_type: RelationType,
    from_id: str,
    to_id: str | None,
    metadata_subset: dict[str, Any] | None = None,
) -> bool:
    for relation in document.relations:
        if relation.type != relation_type or relation.from_id != from_id or relation.to_id != to_id:
            continue
        if metadata_subset is None:
            return True
        if all(relation.metadata.get(key) == value for key, value in metadata_subset.items()):
            return True
    return False


def append_relation_once(document: DocumentIR, relation: RelationIR) -> bool:
    metadata_keys = {
        key: relation.metadata[key]
        for key in ("marker", "toc_title", "target_page", "caption_kind", "source_block_id")
        if key in relation.metadata
    }
    if has_relation(document, relation.type, relation.from_id, relation.to_id, metadata_keys or None):
        return False
    document.relations.append(relation)
    return True

