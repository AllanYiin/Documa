"""Deterministic, graph-aware Agent Skill compilation and loading."""

from documa.skills.enrichment import SkillEnrichmentProvider, apply_enrichment
from documa.skills.loader import inspect_skill_graph, load_skill_bundle, read_skill_resource
from documa.skills.models import (
    SkillBlockIR,
    SkillBlockRole,
    SkillBundle,
    SkillEdgeIR,
    SkillEdgeType,
    SkillIR,
    SkillResourceIR,
    SkillRoot,
    SkillSyncResult,
)
from documa.skills.store import add_skill_root, skill_store_status, sync_skill_roots

__all__ = [
    "SkillBlockIR",
    "SkillBlockRole",
    "SkillBundle",
    "SkillEdgeIR",
    "SkillEdgeType",
    "SkillEnrichmentProvider",
    "SkillIR",
    "SkillResourceIR",
    "SkillRoot",
    "SkillSyncResult",
    "add_skill_root",
    "apply_enrichment",
    "inspect_skill_graph",
    "load_skill_bundle",
    "read_skill_resource",
    "skill_store_status",
    "sync_skill_roots",
]
