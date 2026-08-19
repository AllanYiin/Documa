"""Persistent registry and incremental compilation for trusted skill roots."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from filelock import FileLock, Timeout

from documa.core.ir import to_plain_data
from documa.skills.models import SkillIR, SkillRoot, SkillSyncResult
from documa.skills.parser import SkillParseError, compile_skill_directory, discover_skill_directories, skill_ir_from_plain_data


SKILL_REGISTRY_VERSION = "1"
SKILL_CONFIG_VERSION = "1"
SKILL_LOCK_TIMEOUT_SECONDS = 5.0
_ROOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def skills_dir(store_dir: str | Path = ".documa") -> Path:
    return Path(store_dir) / "skills"


def config_path(store_dir: str | Path = ".documa") -> Path:
    return skills_dir(store_dir) / "config.json"


def registry_path(store_dir: str | Path = ".documa") -> Path:
    return skills_dir(store_dir) / "registry.json"


def index_path(store_dir: str | Path = ".documa") -> Path:
    return skills_dir(store_dir) / "skill.search.idx"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(to_plain_data(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _root(value: SkillRoot | dict[str, Any]) -> SkillRoot:
    if isinstance(value, SkillRoot):
        root = value
    else:
        root = SkillRoot(
            id=str(value["id"]),
            path=str(value["path"]),
            priority=int(value.get("priority", 0)),
            enabled=bool(value.get("enabled", True)),
            trusted=bool(value.get("trusted", True)),
            allow_native_scan_overlap=bool(value.get("allow_native_scan_overlap", False)),
        )
    if not _ROOT_ID_RE.fullmatch(root.id):
        raise ValueError("Skill root id must match [a-z0-9][a-z0-9_-]{0,63}.")
    unresolved = Path(root.path)
    if unresolved.is_symlink():
        raise ValueError("Skill root itself must not be a symlink.")
    resolved = unresolved.resolve()
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()
    native_roots = (codex_home / "skills", (Path.home() / ".agents" / "skills").resolve())
    if not root.allow_native_scan_overlap and any(
        resolved == native or resolved in native.parents or native in resolved.parents for native in native_roots
    ):
        raise ValueError("Managed skill roots must not overlap Codex or shared native skill scan paths.")
    return SkillRoot(
        id=root.id,
        path=str(resolved),
        priority=root.priority,
        enabled=root.enabled,
        trusted=root.trusted,
        allow_native_scan_overlap=root.allow_native_scan_overlap,
    )


def load_skill_config(store_dir: str | Path = ".documa") -> dict[str, Any]:
    path = config_path(store_dir)
    if not path.exists():
        return {"config_version": SKILL_CONFIG_VERSION, "roots": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("roots"), list):
        raise ValueError("Skill config must contain a roots array.")
    return payload


def save_skill_config(roots: Iterable[SkillRoot | dict[str, Any]], store_dir: str | Path = ".documa") -> dict[str, Any]:
    normalized = [_root(value) for value in roots]
    ids = [item.id for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("Skill root ids must be unique.")
    paths = [item.path.casefold() for item in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError("Skill root paths must be unique.")
    payload = {"config_version": SKILL_CONFIG_VERSION, "roots": [to_plain_data(item) for item in normalized]}
    _atomic_json(config_path(store_dir), payload)
    return payload


def add_skill_root(
    root_id: str,
    path: str,
    *,
    priority: int = 0,
    enabled: bool = True,
    trusted: bool = True,
    allow_native_scan_overlap: bool = False,
    store_dir: str | Path = ".documa",
) -> dict[str, Any]:
    config = load_skill_config(store_dir)
    roots = [item for item in config["roots"] if item.get("id") != root_id]
    roots.append(
        SkillRoot(
            id=root_id,
            path=path,
            priority=priority,
            enabled=enabled,
            trusted=trusted,
            allow_native_scan_overlap=allow_native_scan_overlap,
        )
    )
    return save_skill_config(roots, store_dir)


def load_skill_registry(store_dir: str | Path = ".documa") -> dict[str, Any]:
    path = registry_path(store_dir)
    if not path.exists():
        return {"registry_version": SKILL_REGISTRY_VERSION, "skills": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise ValueError("Skill registry must contain a skills array.")
    return payload


def active_skill_entries(store_dir: str | Path = ".documa") -> list[dict[str, Any]]:
    return [item for item in load_skill_registry(store_dir)["skills"] if item.get("status") == "active"]


def load_skill_ir(entry_or_path: dict[str, Any] | str | Path, store_dir: str | Path = ".documa") -> SkillIR:
    if isinstance(entry_or_path, dict):
        path = Path(store_dir) / Path(PurePosixPath(str(entry_or_path["ir_path"])))
    else:
        path = Path(entry_or_path)
    return skill_ir_from_plain_data(json.loads(path.read_text(encoding="utf-8")))


def _entry(skill: SkillIR, root: SkillRoot, ir_path: Path, store: Path) -> dict[str, Any]:
    return {
        "skill_id": skill.skill_id,
        "qualified_name": skill.qualified_name,
        "name": skill.name,
        "description": skill.description,
        "root_id": root.id,
        "priority": root.priority,
        "source_path": skill.source_path,
        "source_key": skill.metadata.get("source_key"),
        "generation": skill.generation,
        "source_digest": skill.source_digest,
        "ir_path": PurePosixPath(ir_path.relative_to(store)).as_posix(),
        "status": "active",
        "superseded_by": None,
        "updated_at": _now(),
    }


def sync_skill_roots(
    roots: Iterable[SkillRoot | dict[str, Any]] | None = None,
    *,
    store_dir: str | Path = ".documa",
    lock_timeout: float = SKILL_LOCK_TIMEOUT_SECONDS,
    enrichment_provider: Any = None,
) -> SkillSyncResult:
    """Compile changed skills, tombstone missing skills, and rebuild the derived index when needed."""

    if roots is not None:
        config = save_skill_config(roots, store_dir)
    else:
        config = load_skill_config(store_dir)
    normalized_roots = [_root(item) for item in config.get("roots", [])]
    store = Path(store_dir)
    root_store = skills_dir(store)
    root_store.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(root_store / "registry.lock"), timeout=lock_timeout)
    try:
        with lock:
            registry = load_skill_registry(store)
            previous_active = {item.get("source_path"): item for item in registry["skills"] if item.get("status") == "active"}
            previous_quarantined = {
                item.get("source_path"): item
                for item in registry["skills"]
                if item.get("status") == "quarantined" and not item.get("ir_path")
            }
            seen_paths: set[str] = set()
            new_entries: list[dict[str, Any]] = []
            quarantined_entries: list[dict[str, Any]] = []
            result = SkillSyncResult(status="ok")
            changed = False

            for root in sorted(normalized_roots, key=lambda item: (-item.priority, item.id)):
                if not root.enabled:
                    continue
                root_path = Path(root.path)
                if not root.trusted:
                    result.quarantined += 1
                    result.warnings.append({"code": "UNTRUSTED_ROOT", "root_id": root.id, "path": root.path})
                    for item in previous_active.values():
                        if item.get("root_id") == root.id:
                            item["status"] = "quarantined"
                            item["updated_at"] = _now()
                            changed = True
                    continue
                if not root_path.is_dir():
                    result.warnings.append({"code": "SKILL_ROOT_MISSING", "root_id": root.id, "path": root.path})
                    continue
                for skill_dir in discover_skill_directories(root_path):
                    result.discovered += 1
                    source_path = str(skill_dir.resolve())
                    seen_paths.add(source_path)
                    old = previous_active.get(source_path)
                    try:
                        skill = compile_skill_directory(skill_dir, configured_root=root_path, root_id=root.id)
                        from documa.skills.enrichment import apply_enrichment

                        cached = None
                        if (
                            old
                            and old.get("skill_id") == skill.skill_id
                            and enrichment_provider is not None
                            and old.get("source_digest") == skill.source_digest
                        ):
                            previous_skill = load_skill_ir(old, store)
                            previous_enrichment = previous_skill.metadata.get("enrichment") or {}
                            if (
                                previous_skill.metadata.get("compiler") == skill.metadata.get("compiler")
                                and
                                previous_enrichment.get("provider") == str(enrichment_provider.name)
                                and previous_enrichment.get("version") == str(enrichment_provider.version)
                            ):
                                cached = previous_skill
                        skill = cached or apply_enrichment(skill, enrichment_provider)
                    except (OSError, SkillParseError, ValueError) as exc:
                        result.quarantined += 1
                        result.warnings.append(
                            {
                                "code": getattr(exc, "code", "SKILL_PARSE_FAILED"),
                                "source_path": source_path,
                                "message": str(exc),
                            }
                        )
                        if old is not None:
                            old["status"] = "quarantined"
                            old["updated_at"] = _now()
                            changed = True
                        elif source_path in previous_quarantined:
                            previous_quarantined[source_path]["error"] = result.warnings[-1]
                            previous_quarantined[source_path]["updated_at"] = _now()
                        else:
                            quarantined_entries.append(
                                {
                                    "skill_id": f"quarantined:{root.id}:{hashlib_path(source_path)}",
                                    "qualified_name": f"{root.id}:{skill_dir.name}",
                                    "name": skill_dir.name,
                                    "root_id": root.id,
                                    "priority": root.priority,
                                    "source_path": source_path,
                                    "generation": None,
                                    "ir_path": None,
                                    "status": "quarantined",
                                    "error": result.warnings[-1],
                                    "updated_at": _now(),
                                }
                            )
                            changed = True
                        continue
                    if old and old.get("skill_id") == skill.skill_id and old.get("generation") == skill.generation:
                        if int(old.get("priority", 0)) != root.priority:
                            changed = True
                        old["priority"] = root.priority
                        old["status"] = "active"
                        new_entries.append(old)
                        result.unchanged += 1
                        continue
                    package = root_store / "packages" / hashlib_path(skill.skill_id) / skill.generation
                    ir_path = package / "skill.ir.json"
                    _atomic_json(ir_path, skill)
                    entry = _entry(skill, root, ir_path, store)
                    if old:
                        old["status"] = "superseded"
                        old["superseded_by"] = skill.generation
                        old["updated_at"] = _now()
                    new_entries.append(entry)
                    result.compiled += 1
                    changed = True

            retained: list[dict[str, Any]] = []
            active_source_paths = {item["source_path"] for item in new_entries}
            for item in registry["skills"]:
                if item.get("status") != "active":
                    retained.append(item)
                    continue
                source_path = item.get("source_path")
                if source_path in active_source_paths:
                    if item.get("generation") != next(
                        (new["generation"] for new in new_entries if new["source_path"] == source_path), None
                    ):
                        retained.append(item)
                    continue
                if source_path not in seen_paths:
                    item["status"] = "missing"
                    item["updated_at"] = _now()
                    retained.append(item)
                    result.missing += 1
                    changed = True
            registry = {
                "registry_version": SKILL_REGISTRY_VERSION,
                "skills": sorted(
                    retained + new_entries + quarantined_entries,
                    key=lambda item: (item.get("qualified_name") or "", item.get("generation") or ""),
                ),
                "updated_at": _now(),
            }
            _atomic_json(registry_path(store), registry)

            if changed or not index_path(store).exists():
                from documa.skills.index import build_skill_index

                build_skill_index(store_dir=store)
                result.index_rebuilt = True
            _last_sync[str(store.resolve())] = time.monotonic()
            return result
    except Timeout:
        return SkillSyncResult(
            status="error",
            warnings=[{"code": "LOCK_TIMEOUT", "message": "Could not acquire the skill registry lock."}],
        )


def hashlib_path(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


_last_sync: dict[str, float] = {}


def ensure_skill_store(
    *,
    store_dir: str | Path = ".documa",
    refresh: bool = False,
    stale_seconds: float = 60.0,
) -> SkillSyncResult:
    key = str(Path(store_dir).resolve())
    now = time.monotonic()
    if refresh or not index_path(store_dir).exists() or now - _last_sync.get(key, 0.0) >= stale_seconds:
        result = sync_skill_roots(store_dir=store_dir)
        _last_sync[key] = now
        return result
    return SkillSyncResult(status="ok", index_rebuilt=False)


def skill_store_status(store_dir: str | Path = ".documa") -> dict[str, Any]:
    try:
        config = load_skill_config(store_dir)
        registry = load_skill_registry(store_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"status": "error", "code": "SKILL_STORE_INVALID", "message": str(exc)}
    entries = registry["skills"]
    return {
        "status": "ok",
        "store_dir": str(Path(store_dir)),
        "root_count": len(config.get("roots", [])),
        "skill_count": len(entries),
        "active": sum(item.get("status") == "active" for item in entries),
        "superseded": sum(item.get("status") == "superseded" for item in entries),
        "missing": sum(item.get("status") == "missing" for item in entries),
        "quarantined": sum(item.get("status") == "quarantined" for item in entries),
        "index_path": str(index_path(store_dir)),
        "index_exists": index_path(store_dir).exists(),
    }
