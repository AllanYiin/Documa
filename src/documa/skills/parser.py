"""Parser-neutral, deterministic compiler from an Agent Skill folder to Skill IR."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from documa.core.ir import TextContent
from documa.skills.models import (
    SkillBlockIR,
    SkillBlockRole,
    SkillEdgeIR,
    SkillEdgeType,
    SkillIR,
    SkillResourceIR,
)


MAX_SKILL_FILE_BYTES = 2 * 1024 * 1024
MAX_SKILL_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_DISCOVERY_DEPTH = 4
SKILL_COMPILER_VERSION = "documa-skill-v1.1"
_TEXT_SUFFIXES = {".md", ".markdown", ".mdp", ".txt", ".yaml", ".yml", ".json"}
_INDEXED_PREFIXES = {"references", "reference"}
_HIDDEN_SECRET_NAMES = {".env", ".npmrc", ".pypirc", ".netrc", "credentials", "secrets"}
_RESOURCE_RE = re.compile(
    r"(?:\[[^\]]*\]\((?P<link>[^)#]+)(?:#[^)]*)?\)|`(?P<code>(?:references?|scripts?|assets?)/[^`]+)`)",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+\s*)?$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_XML_TAG_RE = re.compile(r"^\s*</?[A-Za-z][^>]*>\s*$")
_STEP_RE = re.compile(r"^(?:step|stage|phase|步驟|階段)\s*\d+", re.IGNORECASE)
_REQUIRED_RE = re.compile(r"\b(?:must|required|always|before|read first)\b|必須|務必|一律|先讀|前置", re.IGNORECASE)

_ROLE_TERMS: list[tuple[SkillBlockRole, tuple[str, ...]]] = [
    (SkillBlockRole.GUARDRAIL, ("decision boundary", "guardrail", "global rules", "safety", "do not", "negative trigger", "禁止", "不得", "安全", "邊界", "全域規則")),
    (SkillBlockRole.SCOPE, ("scope", "in scope", "out of scope", "when to use", "purpose", "適用", "範圍", "目的")),
    (SkillBlockRole.TROUBLESHOOTING, ("troubleshooting", "failure", "error handling", "fallback", "疑難排解", "錯誤", "回退")),
    (SkillBlockRole.TEST, ("testing", "tests", "validation", "eval", "驗證", "測試", "評估")),
    (SkillBlockRole.EXAMPLE, ("example", "examples", "worked example", "範例", "示例")),
    (SkillBlockRole.REFERENCE, ("reference", "references", "resource", "resources", "參考", "資源")),
    (SkillBlockRole.WORKFLOW, ("workflow", "instructions", "process", "procedure", "流程", "指令", "步驟")),
    (SkillBlockRole.IDENTITY, ("role", "persona", "about", "overview", "角色", "定位", "概覽")),
]


class SkillParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def discover_skill_directories(root: Path) -> list[Path]:
    """Discover SKILL.md parents without following hidden or deep paths."""

    resolved = root.resolve()
    if not resolved.is_dir():
        return []
    output: list[Path] = []
    for path in sorted(resolved.rglob("SKILL.md")):
        relative = path.relative_to(resolved)
        if len(relative.parts) - 1 > MAX_DISCOVERY_DEPTH:
            continue
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        if _has_symlink_component(path, resolved):
            continue
        output.append(path.parent)
    return output


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SkillParseError("SKILL_RESOURCE_OUTSIDE_ROOT", f"Resource escapes configured root: {path}") from exc
    if _has_symlink_component(path, root):
        raise SkillParseError("SKILL_RESOURCE_OUTSIDE_ROOT", f"Symlink resources are not allowed: {path}")
    return PurePosixPath(relative).as_posix()


def _has_symlink_component(path: Path, root: Path) -> bool:
    current = path
    boundary = root.resolve()
    while True:
        if current.is_symlink():
            return True
        if current == boundary or current.parent == current:
            return False
        current = current.parent


def _read_utf8(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_SKILL_FILE_BYTES:
        raise SkillParseError("SKILL_FILE_TOO_LARGE", f"Skill text file exceeds {MAX_SKILL_FILE_BYTES} bytes: {path}")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise SkillParseError("SKILL_BINARY_TEXT", f"Text resource contains NUL bytes: {path}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillParseError("SKILL_NOT_UTF8", f"Skill text must be UTF-8: {path}") from exc


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _frontmatter(text: str) -> tuple[dict[str, Any], str, str, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("SKILL_FRONTMATTER_MISSING", "SKILL.md must start with YAML frontmatter.")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise SkillParseError("SKILL_FRONTMATTER_INVALID", "SKILL.md frontmatter is not closed.")
    yaml_text = "".join(lines[1:closing])
    try:
        payload = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError("SKILL_FRONTMATTER_INVALID", f"Invalid safe YAML frontmatter: {exc}") from exc
    if not isinstance(payload, dict):
        raise SkillParseError("SKILL_FRONTMATTER_INVALID", "Skill frontmatter must be a mapping.")
    payload = _json_safe(payload)
    if not isinstance(payload.get("name"), str) or not payload["name"].strip():
        raise SkillParseError("SKILL_NAME_MISSING", "Skill frontmatter requires a non-empty name.")
    if not isinstance(payload.get("description"), str) or not payload["description"].strip():
        raise SkillParseError("SKILL_DESCRIPTION_MISSING", "Skill frontmatter requires a non-empty description.")
    name = payload["name"].strip()
    if len(name) > 64 or not _SKILL_NAME_RE.fullmatch(name):
        raise SkillParseError(
            "SKILL_NAME_INVALID",
            "Skill name must be at most 64 lowercase letters, digits, or single hyphen-separated segments.",
        )
    if len(payload["description"]) > 1024:
        raise SkillParseError("SKILL_DESCRIPTION_INVALID", "Skill description must not exceed 1024 characters.")
    raw = "".join(lines[: closing + 1]).rstrip("\r\n")
    body = "".join(lines[closing + 1 :])
    return payload, raw, body, closing + 2


def _role(title: str | None, raw: str, *, kind: str) -> SkillBlockRole:
    if kind == "frontmatter":
        return SkillBlockRole.IDENTITY
    probe = " ".join(part for part in (title or "", raw[:240]) if part).casefold()
    if title and _STEP_RE.match(title.strip()):
        return SkillBlockRole.STEP
    if kind in {"content", "list_item"} and _STEP_RE.match(raw.strip().lstrip("-*0123456789. ")):
        return SkillBlockRole.STEP
    for role, terms in _ROLE_TERMS:
        if any(term in probe for term in terms):
            return role
    return SkillBlockRole.CONTENT


def _block_id(
    skill_id: str,
    resource_path: str,
    kind: str,
    heading_path: list[str],
    raw: str,
    duplicate: int,
) -> str:
    seed = "\0".join((skill_id, resource_path, kind, " > ".join(heading_path), raw, str(duplicate)))
    return "sb_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _parse_markdown_blocks(
    text: str,
    *,
    skill_id: str,
    resource_path: str,
    line_offset: int = 0,
    frontmatter_raw: str | None = None,
) -> list[SkillBlockIR]:
    blocks: list[SkillBlockIR] = []
    duplicate_counts: Counter[str] = Counter()
    heading_stack: list[tuple[int, str, str]] = []
    order = 0

    def append(raw: str, kind: str, start: int, end: int, title: str | None = None, depth: int | None = None) -> None:
        nonlocal order
        raw = raw.rstrip("\r\n")
        if not raw.strip():
            return
        order += 1
        path = [item[1] for item in heading_stack]
        parent_id = heading_stack[-1][2] if heading_stack else None
        if kind == "heading" and heading_stack and heading_stack[-1][1] == title:
            path = [item[1] for item in heading_stack[:-1]] + ([title] if title else [])
            parent_id = heading_stack[-2][2] if len(heading_stack) > 1 else None
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        duplicate_counts[content_hash] += 1
        block_id = _block_id(skill_id, resource_path, kind, path, raw, duplicate_counts[content_hash])
        inherited_title = " > ".join(path) if kind not in {"heading", "frontmatter"} and path else title
        block = SkillBlockIR(
            id=block_id,
            resource_path=resource_path,
            kind=kind,
            role=_role(inherited_title, raw, kind=kind),
            text=TextContent(raw),
            title=title,
            parent_id=parent_id,
            heading_path=path,
            depth=len(heading_stack) if depth is None else depth,
            order_index=order,
            line_start=start,
            line_end=end,
            content_hash=content_hash,
            metadata={"required": False},
        )
        blocks.append(block)

    if frontmatter_raw is not None:
        append(frontmatter_raw, "frontmatter", 1, max(1, line_offset - 1), title="frontmatter", depth=0)

    lines = text.splitlines(keepends=True)
    paragraph: list[tuple[int, str]] = []
    in_fence = False
    fence = ""
    fence_lines: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            append(
                "".join(item[1] for item in paragraph),
                "content",
                paragraph[0][0],
                paragraph[-1][0],
            )
        paragraph = []

    for local_line, raw in enumerate(lines, start=line_offset):
        stripped = raw.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                flush()
                in_fence = True
                fence = marker
                fence_lines = [(local_line, raw)]
            elif stripped.startswith(fence):
                fence_lines.append((local_line, raw))
                in_fence = False
                fence = ""
                append(
                    "".join(item[1] for item in fence_lines),
                    "code_fence",
                    fence_lines[0][0],
                    fence_lines[-1][0],
                )
                fence_lines = []
            else:
                fence_lines.append((local_line, raw))
            continue
        if in_fence:
            fence_lines.append((local_line, raw))
            continue
        if not in_fence:
            heading = _HEADING_RE.match(raw.rstrip("\r\n"))
            if heading:
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                parent_id = heading_stack[-1][2] if heading_stack else None
                path = [item[1] for item in heading_stack] + [title]
                content_hash = hashlib.sha256(raw.rstrip("\r\n").encode("utf-8")).hexdigest()
                duplicate_counts[content_hash] += 1
                block_id = _block_id(skill_id, resource_path, "heading", path, raw.rstrip("\r\n"), duplicate_counts[content_hash])
                order += 1
                block = SkillBlockIR(
                    id=block_id,
                    resource_path=resource_path,
                    kind="heading",
                    role=_role(title, raw, kind="heading"),
                    text=TextContent(raw.rstrip("\r\n")),
                    title=title,
                    parent_id=parent_id,
                    heading_path=path,
                    depth=len(heading_stack),
                    order_index=order,
                    line_start=local_line,
                    line_end=local_line,
                    content_hash=content_hash,
                    metadata={"heading_level": level, "required": False},
                )
                blocks.append(block)
                heading_stack.append((level, title, block_id))
                continue
            if _XML_TAG_RE.match(raw):
                flush()
                append(raw, "xml_tag", local_line, local_line)
                continue
            if _LIST_RE.match(raw):
                flush()
                append(raw, "list_item", local_line, local_line)
                continue
            if not raw.strip():
                flush()
                continue
        paragraph.append((local_line, raw))
    if fence_lines:
        append(
            "".join(item[1] for item in fence_lines),
            "code_fence",
            fence_lines[0][0],
            fence_lines[-1][0],
        )
    flush()
    return blocks


def _resource_kind(relative_path: str) -> str:
    first = PurePosixPath(relative_path).parts[0].casefold()
    if first in {"scripts", "script"}:
        return "script"
    if first in {"assets", "asset"}:
        return "asset"
    if first in _INDEXED_PREFIXES:
        return "reference"
    if PurePosixPath(relative_path).name.casefold() == "skill.md":
        return "skill"
    return "resource"


def _resource_files(skill_dir: Path, configured_root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    total = 0
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(skill_dir).parts
        if any(part.startswith(".") for part in relative_parts):
            if path.name.casefold() in _HIDDEN_SECRET_NAMES:
                raise SkillParseError("SKILL_HIDDEN_SECRET", f"Hidden secret file is not allowed: {path}")
            continue
        relative_to_skill = _safe_relative(path, skill_dir)
        _safe_relative(path, configured_root)
        size = path.stat().st_size
        if size > MAX_SKILL_FILE_BYTES:
            raise SkillParseError("SKILL_FILE_TOO_LARGE", f"Skill file exceeds {MAX_SKILL_FILE_BYTES} bytes: {path}")
        total += size
        if total > MAX_SKILL_PACKAGE_BYTES:
            raise SkillParseError("SKILL_PACKAGE_TOO_LARGE", f"Skill package exceeds {MAX_SKILL_PACKAGE_BYTES} bytes: {skill_dir}")
        files.append((path, relative_to_skill))
    return files


def _package_digest(files: list[tuple[Path, str]]) -> str:
    digest = hashlib.sha256()
    for path, relative in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _lifecycle_dependencies(skill_dir: Path) -> list[str]:
    path = skill_dir / "skill_lifecycle.yaml"
    if not path.exists():
        return []
    try:
        payload = yaml.safe_load(_read_utf8(path)) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError("SKILL_LIFECYCLE_INVALID", f"Invalid skill_lifecycle.yaml: {exc}") from exc
    if not isinstance(payload, dict):
        return []
    dependencies = payload.get("dependencies") or {}
    if not isinstance(dependencies, dict):
        return []
    skills = dependencies.get("skills") or []
    if isinstance(skills, str):
        skills = [skills]
    return [str(item).strip() for item in skills if str(item).strip()]


def compile_skill_directory(skill_dir: Path, *, configured_root: Path, root_id: str) -> SkillIR:
    """Compile a trusted local Agent Skill directory without executing resources."""

    skill_dir = skill_dir.resolve()
    configured_root = configured_root.resolve()
    _safe_relative(skill_dir, configured_root)
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.is_file():
        raise SkillParseError("SKILL_FILE_MISSING", f"SKILL.md not found: {skill_dir}")
    files = _resource_files(skill_dir, configured_root)
    source_digest = _package_digest(files)
    text = _read_utf8(skill_path)
    frontmatter, frontmatter_raw, body, body_start = _frontmatter(text)
    name = str(frontmatter["name"]).strip()
    description = str(frontmatter["description"]).strip()
    source_key = _safe_relative(skill_dir, configured_root)
    path_hash = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:8]
    skill_id = f"{root_id}:{name}:{path_hash}"
    qualified_name = f"{root_id}:{name}"

    blocks = _parse_markdown_blocks(
        body,
        skill_id=skill_id,
        resource_path="SKILL.md",
        line_offset=body_start,
        frontmatter_raw=frontmatter_raw,
    )
    resources: list[SkillResourceIR] = []
    for path, relative in files:
        raw = path.read_bytes()
        kind = _resource_kind(relative)
        media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        text_indexed = kind == "reference" and path.suffix.casefold() in _TEXT_SUFFIXES
        resource_blocks: list[SkillBlockIR] = []
        if text_indexed:
            resource_text = _read_utf8(path)
            resource_blocks = _parse_markdown_blocks(
                resource_text,
                skill_id=skill_id,
                resource_path=relative,
                line_offset=1,
            )
            blocks.extend(resource_blocks)
        resources.append(
            SkillResourceIR(
                path=relative,
                kind=kind,
                media_type=media_type,
                sha256=hashlib.sha256(raw).hexdigest(),
                size=len(raw),
                text_indexed=text_indexed,
                block_ids=[block.id for block in resource_blocks],
            )
        )

    edges: list[SkillEdgeIR] = []
    by_resource: dict[str, list[SkillBlockIR]] = {}
    for block in blocks:
        by_resource.setdefault(block.resource_path, []).append(block)
        if block.parent_id:
            edges.append(SkillEdgeIR(SkillEdgeType.PARENT, block.parent_id, block.id))
    for resource_blocks in by_resource.values():
        ordered = sorted(resource_blocks, key=lambda item: item.order_index)
        for left, right in zip(ordered, ordered[1:]):
            edges.append(SkillEdgeIR(SkillEdgeType.NEXT, left.id, right.id))
        previous_step: SkillBlockIR | None = None
        for block in ordered:
            if block.role == SkillBlockRole.STEP:
                if previous_step is not None:
                    edges.append(
                        SkillEdgeIR(
                            SkillEdgeType.REQUIRES_BLOCK,
                            block.id,
                            previous_step.id,
                            {"reason": "workflow_predecessor"},
                        )
                    )
                previous_step = block

    resource_by_path = {resource.path.casefold(): resource for resource in resources}
    block_by_resource = {path.casefold(): items for path, items in by_resource.items()}
    for block in blocks:
        if block.resource_path != "SKILL.md":
            continue
        for match in _RESOURCE_RE.finditer(block.text.raw_text):
            target = (match.group("link") or match.group("code") or "").strip().replace("\\", "/")
            target = target.split("#", 1)[0]
            if "://" in target:
                continue
            relative_target = PurePosixPath(target)
            if relative_target.is_absolute() or re.match(r"^[A-Za-z]:/", target) or ".." in relative_target.parts:
                raise SkillParseError(
                    "SKILL_RESOURCE_OUTSIDE_ROOT",
                    f"Resource reference escapes the skill directory: {target}",
                )
            normalized = relative_target.as_posix()
            resource = resource_by_path.get(normalized.casefold())
            if resource is None:
                block.metadata.setdefault("broken_resource_refs", []).append(normalized)
                continue
            resource_node = f"resource:{resource.path}"
            edges.append(SkillEdgeIR(SkillEdgeType.REFERENCES_RESOURCE, block.id, resource_node))
            if _REQUIRED_RE.search(block.text.raw_text):
                targets = block_by_resource.get(resource.path.casefold()) or []
                if targets:
                    edges.append(
                        SkillEdgeIR(
                            SkillEdgeType.REQUIRES_BLOCK,
                            block.id,
                            targets[0].id,
                            {"reason": "required_resource", "resource_path": resource.path},
                        )
                    )

    for dependency in _lifecycle_dependencies(skill_dir):
        edges.append(
            SkillEdgeIR(
                SkillEdgeType.REQUIRES_SKILL,
                skill_id,
                dependency,
                {"source": "skill_lifecycle.yaml"},
            )
        )

    mandatory_roles = {SkillBlockRole.IDENTITY, SkillBlockRole.SCOPE, SkillBlockRole.GUARDRAIL}
    for block in blocks:
        block.metadata["required"] = block.resource_path == "SKILL.md" and block.role in mandatory_roles

    return SkillIR(
        skill_id=skill_id,
        qualified_name=qualified_name,
        name=name,
        description=description,
        generation=source_digest[:16],
        source_digest=source_digest,
        source_root_id=root_id,
        source_path=str(skill_dir),
        frontmatter=frontmatter,
        blocks=blocks,
        resources=resources,
        edges=edges,
        metadata={
            "compiler": SKILL_COMPILER_VERSION,
            "source_key": source_key,
            "file_count": len(files),
            "no_llm": True,
            "scripts_executable": False,
        },
    )


def skill_ir_from_plain_data(data: dict[str, Any]) -> SkillIR:
    blocks = [
        SkillBlockIR(
            id=str(item["id"]),
            resource_path=str(item["resource_path"]),
            kind=str(item["kind"]),
            role=SkillBlockRole(item.get("role", "content")),
            text=TextContent(**item.get("text", {"raw_text": ""})),
            title=item.get("title"),
            parent_id=item.get("parent_id"),
            heading_path=[str(value) for value in item.get("heading_path", [])],
            depth=int(item.get("depth", 0)),
            order_index=int(item.get("order_index", 0)),
            line_start=int(item.get("line_start", 0)),
            line_end=int(item.get("line_end", 0)),
            content_hash=str(item.get("content_hash", "")),
            metadata=dict(item.get("metadata", {})),
        )
        for item in data.get("blocks", [])
    ]
    resources = [SkillResourceIR(**item) for item in data.get("resources", [])]
    edges = [
        SkillEdgeIR(
            type=SkillEdgeType(item["type"]),
            from_id=str(item["from_id"]),
            to_id=str(item["to_id"]),
            metadata=dict(item.get("metadata", {})),
        )
        for item in data.get("edges", [])
    ]
    return SkillIR(
        skill_id=str(data["skill_id"]),
        qualified_name=str(data["qualified_name"]),
        name=str(data["name"]),
        description=str(data["description"]),
        generation=str(data["generation"]),
        source_digest=str(data["source_digest"]),
        source_root_id=str(data["source_root_id"]),
        source_path=str(data["source_path"]),
        ir_version=str(data.get("ir_version", "1.0")),
        frontmatter=dict(data.get("frontmatter", {})),
        blocks=blocks,
        resources=resources,
        edges=edges,
        metadata=dict(data.get("metadata", {})),
    )


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
