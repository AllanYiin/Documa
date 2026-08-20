"""Two-level deterministic skill retrieval, graph closure, and rendering."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from documa.core.ir import to_plain_data
from documa.interfaces import token_counting
from documa.skills.index import block_scores, inspect_index, query_skill_candidates
from documa.skills.models import SkillBlockIR, SkillBlockRole, SkillBundle, SkillEdgeType, SkillIR
from documa.skills.store import active_skill_entries, ensure_skill_store, load_skill_ir


MIN_BUNDLE_TOKENS = 256
MAX_BUNDLE_TOKENS = 8000
DEFAULT_RESOURCE_TOKENS = 1200
MAX_RECOMMENDED_RESOURCES_PER_SKILL = 3


def _entry_maps(store_dir: str | Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    entries = active_skill_entries(store_dir)
    by_id = {entry["skill_id"]: entry for entry in entries}
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        by_name[str(entry["name"]).casefold()].append(entry)
        by_name[str(entry["qualified_name"]).casefold()].append(entry)
    return by_id, by_name


def _resolve_dependency(
    name: str,
    by_id: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str | None]:
    if name in by_id:
        return by_id[name], None
    matches = by_name.get(name.casefold(), [])
    if not matches:
        return None, "SKILL_DEPENDENCY_MISSING"
    highest = max(int(item.get("priority", 0)) for item in matches)
    matches = [item for item in matches if int(item.get("priority", 0)) == highest]
    if len({item["skill_id"] for item in matches}) > 1:
        return None, "SKILL_AMBIGUOUS"
    return matches[0], None


def _dependency_order(
    seed_ids: list[str],
    *,
    store_dir: str | Path,
) -> tuple[list[SkillIR], list[dict[str, Any]], list[dict[str, Any]], str | None]:
    by_id, by_name = _entry_maps(store_dir)
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[SkillIR] = []
    trace: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def visit(skill_id: str) -> str | None:
        if skill_id in visited:
            return None
        if skill_id in visiting:
            return "SKILL_DEPENDENCY_CYCLE"
        entry = by_id.get(skill_id)
        if entry is None:
            return "SKILL_DEPENDENCY_MISSING"
        visiting.add(skill_id)
        skill = load_skill_ir(entry, store_dir)
        for edge in skill.edges:
            if edge.type != SkillEdgeType.REQUIRES_SKILL:
                continue
            dependency, code = _resolve_dependency(edge.to_id, by_id, by_name)
            if code:
                warnings.append({"code": code, "skill_id": skill_id, "dependency": edge.to_id})
                return code
            assert dependency is not None
            trace.append(
                {
                    "type": edge.type.value,
                    "from_id": skill_id,
                    "to_id": dependency["skill_id"],
                    "metadata": edge.metadata,
                }
            )
            cycle = visit(dependency["skill_id"])
            if cycle:
                return cycle
        visiting.remove(skill_id)
        visited.add(skill_id)
        order.append(skill)
        return None

    for seed_id in seed_ids:
        code = visit(seed_id)
        if code:
            return [], trace, warnings, code
    return order, trace, warnings, None


def _block_closure(
    skill: SkillIR,
    task: str,
    *,
    store_dir: str | Path,
) -> tuple[set[str], set[str], list[dict[str, Any]], dict[str, float]]:
    by_id = {block.id: block for block in skill.blocks}
    scores = block_scores(skill.skill_id, task, store_dir=store_dir)
    mandatory = {block.id for block in skill.blocks if block.metadata.get("required")}
    ranked = sorted(scores, key=lambda block_id: (-scores[block_id], by_id[block_id].order_index))[:8]
    if not ranked:
        ranked = [
            block.id
            for block in skill.blocks
            if block.resource_path == "SKILL.md" and block.role in {SkillBlockRole.WORKFLOW, SkillBlockRole.STEP}
        ][:3]
    selected = set(mandatory).union(ranked)
    trace: list[dict[str, Any]] = []

    def add_ancestors(block_id: str, *, required: bool = False) -> None:
        current = by_id.get(block_id)
        seen: set[str] = set()
        while current and current.parent_id and current.parent_id not in seen:
            seen.add(current.parent_id)
            selected.add(current.parent_id)
            if required:
                mandatory.add(current.parent_id)
            trace.append({"type": "parent", "from_id": current.parent_id, "to_id": current.id})
            current = by_id.get(current.parent_id)

    for block_id in list(selected):
        add_ancestors(block_id, required=True)

    changed = True
    while changed:
        changed = False
        for edge in skill.edges:
            if edge.type != SkillEdgeType.REQUIRES_BLOCK or edge.from_id not in selected:
                continue
            if edge.metadata.get("reason") == "required_resource":
                # Supporting resources stay outside the main bundle budget and are
                # surfaced as explicit read actions instead of being materialized here.
                continue
            if edge.to_id not in selected and edge.to_id in by_id:
                selected.add(edge.to_id)
                mandatory.add(edge.to_id)
                add_ancestors(edge.to_id, required=True)
                trace.append(
                    {"type": edge.type.value, "from_id": edge.from_id, "to_id": edge.to_id, "metadata": edge.metadata}
                )
                changed = True
            elif edge.to_id in selected and edge.to_id in by_id:
                mandatory.add(edge.to_id)
                add_ancestors(edge.to_id, required=True)
    return mandatory, selected, trace, scores


def _render(skills: list[SkillIR], blocks: list[tuple[SkillIR, SkillBlockIR]]) -> str:
    lines = [
        "---",
        "name: documa-dynamic-skill-bundle",
        "description: Dynamically materialized, source-preserving skill context.",
        "---",
        "",
        "<!-- DOCUMA SYNTHETIC WRAPPER: instruction bodies below are verbatim source blocks. -->",
    ]
    current: str | None = None
    for skill, block in blocks:
        if current != skill.skill_id:
            current = skill.skill_id
            lines.extend(
                [
                    "",
                    f"## Skill: {skill.qualified_name}",
                    f"<!-- source={skill.source_path}; generation={skill.generation} -->",
                ]
            )
        lines.extend(
            [
                "",
                f"<!-- block={block.id}; resource={block.resource_path}; lines={block.line_start}-{block.line_end} -->",
                block.text.raw_text,
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def load_skill_bundle(
    task: str,
    skill_names: list[str] | None = None,
    *,
    max_tokens: int | None = None,
    max_skills: int = 3,
    store_dir: str | Path = ".documa",
    refresh: bool = False,
    render: bool = True,
) -> SkillBundle:
    if max_tokens is not None and not MIN_BUNDLE_TOKENS <= max_tokens <= MAX_BUNDLE_TOKENS:
        return SkillBundle(
            status="error",
            code="SKILL_BUDGET_INVALID",
            task=task,
            budget={"min_tokens": MIN_BUNDLE_TOKENS, "max_tokens": MAX_BUNDLE_TOKENS, "requested": max_tokens},
        )
    budget_mode = "automatic" if max_tokens is None else "explicit"
    effective_max_tokens = MAX_BUNDLE_TOKENS if max_tokens is None else max_tokens
    counter = token_counting.get_token_counter()
    if counter is None:
        return SkillBundle(status="error", code="TOKEN_COUNTER_REQUIRED", task=task)
    try:
        sync = ensure_skill_store(store_dir=store_dir, refresh=refresh)
    except (OSError, ValueError) as exc:
        return SkillBundle(status="error", code="SKILL_STORE_INVALID", task=task, warnings=[{"message": str(exc)}])
    if sync.status == "error":
        return SkillBundle(status="error", code="SKILL_STORE_INVALID", task=task, warnings=sync.warnings)
    candidate_result = query_skill_candidates(
        task,
        skill_names=skill_names,
        max_skills=max(1, min(max_skills, 3)),
        store_dir=store_dir,
    )
    candidates = candidate_result.get("candidates", [])
    if candidate_result.get("status") != "ok":
        return SkillBundle(
            status="needs_narrowing",
            code=candidate_result.get("code", "SKILL_LOW_CONFIDENCE"),
            task=task,
            candidates=candidates,
            next_actions=[
                {
                    "tool": "documa_load_skill",
                    "arguments": {
                        "task": task,
                        "skill_names": [item["qualified_name"]],
                        **({"max_tokens": max_tokens} if max_tokens is not None else {}),
                    },
                }
                for item in candidates[:3]
            ],
        )

    skills, dependency_trace, warnings, dependency_error = _dependency_order(
        [item["skill_id"] for item in candidates], store_dir=store_dir
    )
    if dependency_error:
        return SkillBundle(
            status="needs_narrowing",
            code=dependency_error,
            task=task,
            candidates=candidates,
            graph_trace=dependency_trace,
            warnings=warnings,
        )

    mandatory_pairs: list[tuple[SkillIR, SkillBlockIR]] = []
    optional_pairs: list[tuple[float, SkillIR, SkillBlockIR]] = []
    score_maps: dict[str, dict[str, float]] = {}
    graph_trace = list(dependency_trace)
    for skill in skills:
        mandatory, selected, trace, scores = _block_closure(skill, task, store_dir=store_dir)
        score_maps[skill.skill_id] = scores
        graph_trace.extend(trace)
        for block in skill.blocks:
            if block.id not in selected:
                continue
            for missing_path in block.metadata.get("broken_resource_refs", []):
                warnings.append(
                    {
                        "code": "SKILL_RESOURCE_MISSING",
                        "skill_id": skill.skill_id,
                        "block_id": block.id,
                        "resource_path": missing_path,
                    }
                )
            if block.id in mandatory:
                mandatory_pairs.append((skill, block))
            else:
                optional_pairs.append((scores.get(block.id, 0.0), skill, block))

    skill_position = {skill.skill_id: index for index, skill in enumerate(skills)}

    def source_order(pair: tuple[SkillIR, SkillBlockIR]) -> tuple[int, int, int]:
        skill, block = pair
        resource_rank = 0 if block.resource_path == "SKILL.md" else 1
        return skill_position[skill.skill_id], resource_rank, block.order_index

    mandatory_pairs = sorted(dict(((skill.skill_id, block.id), (skill, block)) for skill, block in mandatory_pairs).values(), key=source_order)
    mandatory_render = _render(skills, mandatory_pairs)
    minimum = counter.count(mandatory_render)
    if minimum > effective_max_tokens:
        return SkillBundle(
            status="needs_narrowing",
            code="SKILL_BUDGET_TOO_SMALL",
            task=task,
            selected_skills=[_selected_skill(skill, candidates) for skill in skills],
            graph_trace=graph_trace,
            candidates=candidates,
            budget={
                "mode": budget_mode,
                "requested_max_tokens": max_tokens,
                "max_tokens": effective_max_tokens,
                "minimum_required_tokens": minimum,
                "token_counter": counter.name,
            },
            warnings=warnings,
        )

    chosen = list(mandatory_pairs)
    optional_pairs.sort(key=lambda item: (-item[0], skill_position[item[1].skill_id], item[2].order_index))
    chosen_keys = {(skill.skill_id, block.id) for skill, block in chosen}
    chosen_hashes = {block.content_hash for _, block in chosen if block.content_hash}
    for _, skill, block in optional_pairs:
        if (skill.skill_id, block.id) in chosen_keys:
            continue
        if block.content_hash and block.content_hash in chosen_hashes:
            continue
        trial = sorted(chosen + [(skill, block)], key=source_order)
        if counter.count(_render(skills, trial)) <= effective_max_tokens:
            chosen = trial
            chosen_keys.add((skill.skill_id, block.id))
            if block.content_hash:
                chosen_hashes.add(block.content_hash)
    rendered = _render(skills, chosen)
    spent = counter.count(rendered)
    next_actions = _resource_actions(skills, chosen, score_maps)
    resource_summary = _resource_summary(skills, chosen, next_actions)
    resource_summary_by_skill = {item["skill_id"]: item for item in resource_summary["by_skill"]}
    block_payloads = []
    provenance = []
    for skill, block in chosen:
        block_payloads.append(
            {
                "skill_id": skill.skill_id,
                "block_id": block.id,
                "role": block.role.value,
                "resource_path": block.resource_path,
                "title": block.title,
                "text": block.text.raw_text,
                "required": bool(block.metadata.get("required")) or (skill.skill_id, block.id) in {
                    (item_skill.skill_id, item_block.id) for item_skill, item_block in mandatory_pairs
                },
            }
        )
        provenance.append(
            {
                "skill_id": skill.skill_id,
                "block_id": block.id,
                "source_path": skill.source_path,
                "resource_path": block.resource_path,
                "line_start": block.line_start,
                "line_end": block.line_end,
                "content_hash": block.content_hash,
                "generation": skill.generation,
            }
        )
    return SkillBundle(
        status="ok",
        task=task,
        selected_skills=[
            _selected_skill(skill, candidates, resource_summary=resource_summary_by_skill[skill.skill_id])
            for skill in skills
        ],
        blocks=block_payloads,
        graph_trace=graph_trace,
        provenance=provenance,
        budget={
            "mode": budget_mode,
            "requested_max_tokens": max_tokens,
            "max_tokens": effective_max_tokens,
            "spent_tokens": spent,
            "remaining_tokens": effective_max_tokens - spent,
            "minimum_required_tokens": minimum,
            "token_counter": counter.name,
        },
        warnings=warnings,
        resource_summary=resource_summary,
        next_actions=next_actions,
        candidates=candidates,
        rendered_skill_md=rendered if render else None,
    )


def _selected_skill(
    skill: SkillIR,
    candidates: list[dict[str, Any]],
    *,
    resource_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = next((item for item in candidates if item["skill_id"] == skill.skill_id), None)
    payload = {
        "skill_id": skill.skill_id,
        "qualified_name": skill.qualified_name,
        "name": skill.name,
        "description": skill.description,
        "generation": skill.generation,
        "dependency": candidate is None,
        "score": candidate.get("score") if candidate else None,
        "route_source": candidate.get("route_source") if candidate else "dependency",
    }
    if resource_summary is not None:
        payload["resource_summary"] = resource_summary
    return payload


def _resource_actions(
    skills: list[SkillIR],
    chosen: list[tuple[SkillIR, SkillBlockIR]],
    score_maps: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    chosen_ids = {(skill.skill_id, block.id) for skill, block in chosen}
    actions: list[dict[str, Any]] = []
    for skill in skills:
        resources = {resource.path: resource for resource in skill.resources if resource.text_indexed}
        referenced_by_path: dict[str, list[str]] = defaultdict(list)
        required_paths: set[str] = set()
        for edge in skill.edges:
            if edge.type == SkillEdgeType.REFERENCES_RESOURCE:
                referenced_by_path[edge.to_id.removeprefix("resource:")].append(edge.from_id)
            elif edge.type == SkillEdgeType.REQUIRES_BLOCK and edge.metadata.get("reason") == "required_resource":
                resource_path = edge.metadata.get("resource_path")
                if resource_path:
                    required_paths.add(str(resource_path))

        scores = score_maps.get(skill.skill_id, {})
        candidates: list[tuple[bool, bool, float, str]] = []
        for path, source_ids in referenced_by_path.items():
            resource = resources.get(path)
            if resource is None:
                continue
            materialized_ids = {
                block_id for block_id in resource.block_ids if (skill.skill_id, block_id) in chosen_ids
            }
            if resource.block_ids and len(materialized_ids) == len(resource.block_ids):
                continue
            source_selected = any((skill.skill_id, block_id) in chosen_ids for block_id in source_ids)
            relevance = max(
                [scores.get(block_id, 0.0) for block_id in [*resource.block_ids, *source_ids]] or [0.0]
            )
            required = path in required_paths
            if required or source_selected or relevance > 0:
                candidates.append((required, source_selected, relevance, path))

        candidates.sort(key=lambda item: (-int(item[0]), -int(item[1]), -item[2], item[3]))
        selected_candidates = [item for item in candidates if item[0]]
        optional_slots = max(0, MAX_RECOMMENDED_RESOURCES_PER_SKILL - len(selected_candidates))
        selected_candidates.extend([item for item in candidates if not item[0]][:optional_slots])
        for _, _, _, path in selected_candidates:
            action = {
                "tool": "documa_read_skill_resource",
                "arguments": {"skill_id": skill.skill_id, "resource_path": path, "max_tokens": DEFAULT_RESOURCE_TOKENS},
            }
            if action not in actions:
                actions.append(action)
    return actions


def _resource_summary(
    skills: list[SkillIR],
    chosen: list[tuple[SkillIR, SkillBlockIR]],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    chosen_ids = {(skill.skill_id, block.id) for skill, block in chosen}
    recommended = {
        (action["arguments"]["skill_id"], action["arguments"]["resource_path"])
        for action in actions
        if action.get("tool") == "documa_read_skill_resource"
    }
    by_skill: list[dict[str, Any]] = []
    for skill in skills:
        resources = [resource for resource in skill.resources if resource.text_indexed]
        materialized = 0
        fully_materialized = 0
        for resource in resources:
            selected_count = sum((skill.skill_id, block_id) in chosen_ids for block_id in resource.block_ids)
            if selected_count:
                materialized += 1
            if resource.block_ids and selected_count == len(resource.block_ids):
                fully_materialized += 1
        by_skill.append(
            {
                "skill_id": skill.skill_id,
                "available_text_resources": len(resources),
                "materialized_text_resources": materialized,
                "partially_materialized_text_resources": materialized - fully_materialized,
                "fully_materialized_text_resources": fully_materialized,
                "recommended_resource_reads": sum(
                    (skill.skill_id, resource.path) in recommended for resource in resources
                ),
            }
        )
    count_fields = (
        "available_text_resources",
        "materialized_text_resources",
        "partially_materialized_text_resources",
        "fully_materialized_text_resources",
        "recommended_resource_reads",
    )
    return {
        **{field: sum(item[field] for item in by_skill) for field in count_fields},
        "by_skill": by_skill,
    }


def read_skill_resource(
    skill_id: str,
    resource_path: str,
    *,
    block_ids: list[str] | None = None,
    max_tokens: int = DEFAULT_RESOURCE_TOKENS,
    cursor: int = 0,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    if not 1 <= max_tokens <= MAX_BUNDLE_TOKENS:
        return {
            "status": "error",
            "code": "SKILL_BUDGET_INVALID",
            "budget": {"min_tokens": 1, "max_tokens": MAX_BUNDLE_TOKENS, "requested": max_tokens},
        }
    counter = token_counting.get_token_counter()
    if counter is None:
        return {"status": "error", "code": "TOKEN_COUNTER_REQUIRED"}
    try:
        by_id, _ = _entry_maps(store_dir)
    except (OSError, ValueError) as exc:
        return {"status": "error", "code": "SKILL_STORE_INVALID", "message": str(exc)}
    entry = by_id.get(skill_id)
    if entry is None:
        return {"status": "error", "code": "SKILL_NOT_FOUND", "skill_id": skill_id}
    skill = load_skill_ir(entry, store_dir)
    requested = PurePosixPath(resource_path.replace("\\", "/"))
    if requested.is_absolute() or ".." in requested.parts:
        return {"status": "error", "code": "SKILL_RESOURCE_OUTSIDE_ROOT", "resource_path": resource_path}
    normalized = requested.as_posix()
    resource = next((item for item in skill.resources if item.path.casefold() == normalized.casefold()), None)
    if resource is None or resource.kind in {"script", "asset"} or not resource.text_indexed:
        return {"status": "error", "code": "SKILL_RESOURCE_NOT_READABLE", "resource_path": normalized}
    source = Path(skill.source_path) / Path(PurePosixPath(resource.path))
    try:
        source.resolve().relative_to(Path(skill.source_path).resolve())
    except ValueError:
        return {"status": "error", "code": "SKILL_RESOURCE_OUTSIDE_ROOT", "resource_path": normalized}
    if source.is_symlink():
        return {"status": "error", "code": "SKILL_RESOURCE_OUTSIDE_ROOT", "resource_path": normalized}
    try:
        current_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        return {"status": "error", "code": "SKILL_RESOURCE_MISSING", "resource_path": normalized, "message": str(exc)}
    if current_digest != resource.sha256:
        return {"status": "error", "code": "SKILL_RESOURCE_CHANGED", "resource_path": normalized, "refresh_required": True}
    if block_ids:
        selected = [block for block in skill.blocks if block.id in set(block_ids) and block.resource_path == resource.path]
        text = "\n\n".join(block.text.raw_text for block in sorted(selected, key=lambda item: item.order_index))
    else:
        text = source.read_text(encoding="utf-8")
    cursor = max(0, min(cursor, len(text)))
    truncated, has_more = counter.truncate(text[cursor:], max_tokens)
    next_cursor = cursor + len(truncated)
    return {
        "status": "ok",
        "skill_id": skill_id,
        "resource_path": resource.path,
        "content": truncated,
        "token_estimate": counter.count(truncated),
        "token_counter": counter.name,
        "continuation": {"start": next_cursor} if has_more else None,
        "provenance": {"sha256": resource.sha256, "generation": skill.generation},
    }


def inspect_skill_graph(
    skill_id: str | None = None,
    *,
    limit: int = 100,
    cursor: int = 0,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    try:
        ensure_skill_store(store_dir=store_dir)
    except (OSError, ValueError) as exc:
        return {"status": "error", "code": "SKILL_STORE_INVALID", "message": str(exc)}
    if skill_id is None:
        entries = sorted(active_skill_entries(store_dir), key=lambda item: (item["qualified_name"], item["skill_id"]))
        cursor = max(0, min(cursor, len(entries)))
        page = entries[cursor : cursor + max(1, min(limit, 500))]
        skills = [load_skill_ir(entry, store_dir) for entry in page]
        payload = inspect_index(store_dir)
        payload.update(
            {
                "nodes": [
                    {
                        "skill_id": skill.skill_id,
                        "qualified_name": skill.qualified_name,
                        "generation": skill.generation,
                    }
                    for skill in skills
                ],
                "dependency_edges": [
                    {
                        "type": edge.type.value,
                        "from_id": skill.skill_id,
                        "to_id": edge.to_id,
                        "metadata": edge.metadata,
                    }
                    for skill in skills
                    for edge in skill.edges
                    if edge.type == SkillEdgeType.REQUIRES_SKILL
                ],
                "continuation": {"start": cursor + len(page)} if cursor + len(page) < len(entries) else None,
            }
        )
        return payload
    by_id, _ = _entry_maps(store_dir)
    entry = by_id.get(skill_id)
    if entry is None:
        return {"status": "error", "code": "SKILL_NOT_FOUND", "skill_id": skill_id}
    skill = load_skill_ir(entry, store_dir)
    return {
        "status": "ok",
        "skill_id": skill.skill_id,
        "qualified_name": skill.qualified_name,
        "generation": skill.generation,
        "blocks": [
            {
                "id": block.id,
                "role": block.role.value,
                "title": block.title,
                "resource_path": block.resource_path,
                "parent_id": block.parent_id,
                "order_index": block.order_index,
                "required": bool(block.metadata.get("required")),
            }
            for block in skill.blocks
        ],
        "edges": [to_plain_data(edge) for edge in skill.edges],
        "resources": [to_plain_data(resource) for resource in skill.resources],
    }
