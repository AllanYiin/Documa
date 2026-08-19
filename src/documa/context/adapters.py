"""Adapters from Documa source IRs into the shared ContextIR projection."""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from documa.context.models import (
    ContextAuthority,
    ContextBlock,
    ContextIR,
    ContextRelation,
    ContextSourceKind,
    RelationOrigin,
    SourceSpan,
    sha256_text,
)
from documa.core.ir import DocumentIR, RelationState
from documa.pipeline.base import PipelineContext
from documa.pipeline.block_tree import BlockTreeBuildingStage, block_text_by_id
from documa.search.sidecar import source_digest
from documa.skills.models import SkillEdgeType, SkillIR


def context_from_skill(skill: SkillIR) -> ContextIR:
    known = {block.id for block in skill.blocks}
    dependencies: dict[str, list[str]] = defaultdict(list)
    relations: list[ContextRelation] = []
    for edge in skill.edges:
        if edge.from_id not in known or edge.to_id not in known:
            continue
        relations.append(
            ContextRelation(
                source_block_id=edge.from_id,
                target_block_id=edge.to_id,
                type=edge.type.value,
                origin=RelationOrigin.EXTRACTED,
                evidence_block_ids=[edge.from_id, edge.to_id],
                metadata=dict(edge.metadata),
            )
        )
        if edge.type == SkillEdgeType.REQUIRES_BLOCK:
            dependencies[edge.from_id].append(edge.to_id)
    blocks = [
        ContextBlock(
            block_id=block.id,
            source_id=skill.skill_id,
            source_kind=ContextSourceKind.SKILL,
            title=block.title or block.role.value,
            body=block.text.raw_text,
            source_locator=f"{skill.qualified_name}/{block.resource_path}",
            content_hash=sha256_text(block.text.raw_text),
            authority=ContextAuthority.SKILL,
            parent_id=block.parent_id,
            depends_on=list(dict.fromkeys(dependencies.get(block.id, []))),
            route_tags=[block.role.value, *block.heading_path],
            source_span=SourceSpan(block.line_start, block.line_end) if block.line_start and block.line_end else None,
            pinned=bool(block.metadata.get("required")),
            metadata={"resourcePath": block.resource_path, "generation": skill.generation},
        )
        for block in skill.blocks
    ]
    projection_digest = sha256_text(
        "\n".join(
            [skill.source_digest, *[f"{block.block_id}\0{block.content_hash}" for block in sorted(blocks, key=lambda item: item.block_id)]]
        )
    )
    return ContextIR(
        context_id=skill.skill_id,
        source_kind=ContextSourceKind.SKILL,
        source_digest=projection_digest,
        blocks=blocks,
        relations=relations,
        metadata={
            "qualifiedName": skill.qualified_name,
            "generation": skill.generation,
            "adapter": "documa-skill-ir-v1",
            "sourceDigest": skill.source_digest,
        },
    )


def context_from_document(document: DocumentIR) -> ContextIR:
    if not document.document_blocks:
        BlockTreeBuildingStage().run(document, PipelineContext(settings={}))
    source_texts = block_text_by_id(document)
    bodies = {
        block.id: "\n\n".join(
            source_texts[source_id].strip()
            for source_id in block.source_block_ids
            if source_texts.get(source_id, "").strip()
        )
        for block in document.document_blocks
    }
    source_to_document: dict[str, str] = {}
    for block in document.document_blocks:
        for source_id in block.source_block_ids:
            source_to_document.setdefault(source_id, block.id)
    relations: list[ContextRelation] = []
    known = {block.id for block in document.document_blocks}
    ordered = sorted(
        document.document_blocks,
        key=lambda block: (block.order_index is None, block.order_index or 0, block.id),
    )
    for block in document.document_blocks:
        if block.parent_id in known:
            relations.append(
                ContextRelation(
                    source_block_id=block.parent_id,
                    target_block_id=block.id,
                    type="parent",
                    evidence_block_ids=[block.parent_id, block.id],
                )
            )
    for left, right in zip(ordered, ordered[1:]):
        relations.append(
            ContextRelation(
                source_block_id=left.id,
                target_block_id=right.id,
                type="next",
                evidence_block_ids=[left.id, right.id],
            )
        )
    for relation in document.relations:
        source_id = relation.from_id if relation.from_id in known else source_to_document.get(relation.from_id)
        target_id = relation.to_id if relation.to_id in known else source_to_document.get(relation.to_id or "")
        if not source_id or not target_id or source_id == target_id:
            continue
        origin = RelationOrigin.EXTRACTED if relation.state == RelationState.CONFIRMED else RelationOrigin.INFERRED
        relations.append(
            ContextRelation(
                source_block_id=source_id,
                target_block_id=target_id,
                type=relation.type.value,
                origin=origin,
                evidence_block_ids=[value for value in relation.evidence if value in known],
                metadata={"relationId": relation.id, **relation.metadata},
            )
        )
    blocks = [
        ContextBlock(
            block_id=block.id,
            source_id=document.id,
            source_kind=ContextSourceKind.DOCUMENT,
            title=block.title or block.type.value,
            body=bodies[block.id],
            source_locator=document.source_name,
            content_hash=sha256_text(bodies[block.id]),
            authority=ContextAuthority.TOOL,
            parent_id=block.parent_id,
            route_tags=[block.type.value, *[str(value) for value in block.metadata.get("keyword_terms", [])[:8]]],
            metadata={
                "pageRefs": list(block.page_refs),
                "bboxRefs": list(block.bbox_refs),
                "documentIrVersion": document.ir_version,
            },
        )
        for block in document.document_blocks
    ]
    projection_digest = sha256_text(
        "\n".join(
            [source_digest(document), *[f"{block.block_id}\0{block.content_hash}" for block in sorted(blocks, key=lambda item: item.block_id)]]
        )
    )
    return ContextIR(
        context_id=document.id,
        source_kind=ContextSourceKind.DOCUMENT,
        source_digest=projection_digest,
        blocks=blocks,
        relations=_dedupe_relations(relations),
        metadata={
            "sourceName": document.source_name,
            "documentSourceDigest": source_digest(document),
            "adapter": "documa-document-ir-0.2",
        },
    )


def _code_block_id(locator: str, kind: str, name: str, start: int, body: str) -> str:
    digest = hashlib.sha256(f"{locator}\0{kind}\0{name}\0{start}\0{body}".encode("utf-8")).hexdigest()[:16]
    return f"cb_{digest}"


def _python_blocks(path: Path, text: str, source_id: str) -> tuple[list[ContextBlock], list[ContextRelation]]:
    tree = ast.parse(text, filename=path.name)
    lines = text.splitlines()
    blocks: list[ContextBlock] = []
    relations: list[ContextRelation] = []
    node_to_id: dict[ast.AST, str] = {}
    definitions: dict[str, list[str]] = defaultdict(list)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = int(node.lineno)
        end = int(getattr(node, "end_lineno", node.lineno))
        body = "\n".join(lines[start - 1 : end])
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        block_id = _code_block_id(path.as_posix(), kind, node.name, start, body)
        node_to_id[node] = block_id
        definitions[node.name].append(block_id)
        blocks.append(
            ContextBlock(
                block_id=block_id,
                source_id=source_id,
                source_kind=ContextSourceKind.CODE,
                title=f"{kind} {node.name}",
                body=body,
                source_locator=path.as_posix(),
                content_hash=sha256_text(body),
                authority=ContextAuthority.DEVELOPER,
                route_tags=[kind, node.name],
                source_span=SourceSpan(start, end),
                metadata={"language": "python", "symbol": node.name, "symbolKind": kind},
            )
        )

    for node, source_block_id in node_to_id.items():
        containers = [
            candidate
            for candidate in node_to_id
            if candidate is not node
            and getattr(candidate, "lineno", 0) < getattr(node, "lineno", 0)
            and getattr(candidate, "end_lineno", 0) >= getattr(node, "end_lineno", 0)
        ]
        parent = min(
            containers,
            key=lambda candidate: getattr(candidate, "end_lineno", 0) - getattr(candidate, "lineno", 0),
            default=None,
        )
        if parent is not None:
            target = node_to_id[parent]
            block = next(item for item in blocks if item.block_id == source_block_id)
            block.parent_id = target
            relations.append(ContextRelation(target, source_block_id, "contains", evidence_block_ids=[target, source_block_id]))
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Name):
                continue
            targets = definitions.get(child.func.id, [])
            if len(targets) == 1 and targets[0] != source_block_id:
                relations.append(
                    ContextRelation(
                        source_block_id,
                        targets[0],
                        "calls",
                        evidence_block_ids=[source_block_id, targets[0]],
                        metadata={"method": "python-ast-resolver", "line": getattr(child, "lineno", None)},
                    )
                )
    return blocks, _dedupe_relations(relations)


def context_from_code(paths: Iterable[str | Path], *, context_id: str = "code-workspace") -> ContextIR:
    resolved = [Path(path).resolve() for path in paths]
    if not resolved:
        raise ValueError("At least one code path is required.")
    blocks: list[ContextBlock] = []
    relations: list[ContextRelation] = []
    source_hashes: list[str] = []
    for path in sorted(resolved, key=lambda item: item.as_posix().casefold()):
        if not path.is_file():
            raise ValueError(f"Code source is not a file: {path}")
        text = path.read_text(encoding="utf-8")
        source_hashes.append(f"{path.as_posix()}\0{sha256_text(text)}")
        if path.suffix.casefold() == ".py":
            path_blocks, path_relations = _python_blocks(path, text, context_id)
            blocks.extend(path_blocks)
            relations.extend(path_relations)
            continue
        block_id = _code_block_id(path.as_posix(), "file", path.name, 1, text)
        blocks.append(
            ContextBlock(
                block_id=block_id,
                source_id=context_id,
                source_kind=ContextSourceKind.CODE,
                title=path.name,
                body=text,
                source_locator=path.as_posix(),
                content_hash=sha256_text(text),
                authority=ContextAuthority.DEVELOPER,
                route_tags=["file", path.suffix.casefold().lstrip(".")],
                source_span=SourceSpan(1, max(1, len(text.splitlines()))),
                metadata={"language": path.suffix.casefold().lstrip(".") or "text", "parser": "plain-text"},
            )
        )
    digest = sha256_text("\n".join(source_hashes))
    return ContextIR(
        context_id=context_id,
        source_kind=ContextSourceKind.CODE,
        source_digest=digest,
        blocks=blocks,
        relations=_dedupe_relations(relations),
        metadata={"adapter": "documa-code-v1", "sourceCount": len(resolved)},
    )


def _dedupe_relations(relations: Iterable[ContextRelation]) -> list[ContextRelation]:
    output: list[ContextRelation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for relation in relations:
        key = (relation.source_block_id, relation.target_block_id, relation.type, relation.origin.value)
        if key in seen:
            continue
        seen.add(key)
        output.append(relation)
    return output
