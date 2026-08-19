"""Optional offline enrichment boundary for skill discovery metadata.

The loader never calls an LLM at request time.  Applications may provide a
bounded, cacheable enrichment provider during sync; its output is validated
and remains derived routing metadata, never instruction text or graph truth.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from documa.skills.models import SkillIR


MAX_ENRICHMENT_ITEMS = 32
MAX_ENRICHMENT_ITEM_CHARS = 120


class SkillEnrichmentProvider(Protocol):
    name: str
    version: str

    def enrich(self, catalog: dict[str, Any]) -> dict[str, Any]: ...


def apply_enrichment(skill: SkillIR, provider: SkillEnrichmentProvider | None) -> SkillIR:
    if provider is None:
        skill.metadata["enrichment"] = {"provider": "none", "derived": True}
        compiler = str(skill.metadata.get("compiler") or "unknown")
        skill.generation = hashlib.sha256(f"{skill.source_digest}\0{compiler}\0none".encode("utf-8")).hexdigest()[:16]
        return skill
    catalog = {
        "name": skill.name,
        "description": skill.description,
        "headings": [block.title for block in skill.blocks if block.title][:64],
    }
    raw = provider.enrich(catalog) or {}

    def values(key: str) -> list[str]:
        items = raw.get(key) or []
        if isinstance(items, str):
            items = [items]
        output = []
        for item in items[:MAX_ENRICHMENT_ITEMS]:
            value = " ".join(str(item).split())[:MAX_ENRICHMENT_ITEM_CHARS]
            if value and value not in output:
                output.append(value)
        return output

    skill.metadata["enrichment"] = {
        "provider": str(provider.name),
        "version": str(provider.version),
        "derived": True,
        "synonyms": values("synonyms"),
        "positive_triggers": values("positive_triggers"),
        "negative_triggers": values("negative_triggers"),
        "topic_tags": values("topic_tags"),
    }
    signature = json.dumps(skill.metadata["enrichment"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    compiler = str(skill.metadata.get("compiler") or "unknown")
    skill.generation = hashlib.sha256(
        f"{skill.source_digest}\0{compiler}\0{signature}".encode("utf-8")
    ).hexdigest()[:16]
    return skill
