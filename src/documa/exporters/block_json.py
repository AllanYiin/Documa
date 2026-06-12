"""Block tree JSON exporter for progressive disclosure workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from documa.core.ir import DocumentIR, to_plain_data
from documa.exporters.base import ExportOptions, Exporter
from documa.pipeline.page_refs import ensure_page_citation_map


_SPACES = re.compile(r"\s+")
_FURNITURE_TYPES = {"page_header", "page_footer"}


def _compact_text(value: str) -> str:
    return _SPACES.sub(" ", value).strip()


def _cjk_len(value: str) -> int:
    return sum(1 for char in value if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff")


def _collect_furniture_texts(blocks: list[dict[str, Any]]) -> set[str]:
    output: set[str] = set()
    for block in blocks:
        metadata = block.get("metadata")
        if not isinstance(metadata, dict):
            continue
        furniture = metadata.get("furniture")
        if not isinstance(furniture, list):
            continue
        for item in furniture:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in _FURNITURE_TYPES:
                continue
            text = item.get("text_preview")
            if isinstance(text, str):
                clean = _compact_text(text)
                if len(clean) >= 8 or _cjk_len(clean) >= 4:
                    output.add(clean)
    return output


def _strip_furniture_text(value: str, furniture_texts: set[str]) -> str:
    clean = value
    for furniture in sorted(furniture_texts, key=len, reverse=True):
        clean = clean.replace(furniture, " ")
    return _compact_text(clean)


def _is_furniture_term(value: str, furniture_texts: set[str]) -> bool:
    clean = _compact_text(value)
    if not clean:
        return True
    if clean in furniture_texts:
        return True
    if _cjk_len(clean) < 4:
        return False
    return any(clean in furniture for furniture in furniture_texts)


def _sanitize_value(value: Any, furniture_texts: set[str]) -> Any:
    if isinstance(value, str):
        return _strip_furniture_text(value, furniture_texts)
    if isinstance(value, list):
        output = []
        for item in value:
            if isinstance(item, str) and _is_furniture_term(item, furniture_texts):
                continue
            if isinstance(item, dict):
                term = item.get("term")
                if isinstance(term, str) and _is_furniture_term(term, furniture_texts):
                    continue
            sanitized = _sanitize_value(item, furniture_texts)
            if sanitized in ("", [], {}):
                continue
            output.append(sanitized)
        return output
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key == "furniture":
                continue
            if isinstance(key, str) and _is_furniture_term(key, furniture_texts):
                continue
            sanitized = _sanitize_value(item, furniture_texts)
            if sanitized in ("", [], {}):
                continue
            output[key] = sanitized
        return output
    return value


def _is_furniture_block(block: dict[str, Any]) -> bool:
    metadata = block.get("metadata")
    source_type = metadata.get("source_block_type") if isinstance(metadata, dict) else None
    return block.get("type") in _FURNITURE_TYPES or source_type in _FURNITURE_TYPES


@dataclass(slots=True)
class BlockJsonExporter(Exporter):
    name: str = "block-json"

    def export(self, document: DocumentIR, options: ExportOptions | None = None) -> dict[str, Any]:
        page_citations = ensure_page_citation_map(document)
        blocks = [to_plain_data(block) for block in document.document_blocks]
        furniture_texts = _collect_furniture_texts(blocks)
        sanitized_blocks = [_sanitize_value(block, furniture_texts) for block in blocks if not _is_furniture_block(block)]
        block_ids = {block.get("id") for block in sanitized_blocks if isinstance(block, dict)}
        for block in sanitized_blocks:
            child_ids = block.get("child_ids")
            if isinstance(child_ids, list):
                block["child_ids"] = [child_id for child_id in child_ids if child_id in block_ids]
        return {
            "document_id": document.id,
            "source_name": document.source_name,
            "block_count": len(sanitized_blocks),
            "page_ref_kind": document.metadata.get("page_ref_kind"),
            "page_citations": page_citations,
            "blocks": sanitized_blocks,
        }
