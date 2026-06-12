"""Tool execution layer for CLI, MCP, and direct LLM tool calling."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.core.errors import DocumaError
from documa.core.ir import DocumentIR, to_plain_data
from documa.core.serialization import document_from_plain_data
from documa.exporters import BlockJsonExporter, ExportOptions, JsonExporter, MarkdownExporter, RagJsonExporter
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.pipeline import (
    BlockKeywordExtractionStage,
    BlockTreeBuildingStage,
    ChunkingStage,
    PipelineContext,
    ProvenanceLinkingStage,
    run_default_pipeline,
)
from documa.pipeline.block_tree import document_block_text
from documa.quality import BenchmarkOptions, DoctorOptions, run_doctor, run_fixture_benchmark


ToolPayload = dict[str, Any]
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"\S+")
_DEFAULT_SEARCH_FIELDS = ["title", "preview", "search_terms", "keywords", "new_words"]
_DEFAULT_SNIPPET_FIELDS = {"body", "title", "preview"}
_SEARCH_VERBOSITIES = {"compact", "standard", "debug"}
_SEARCH_FIELD_WEIGHTS = {
    "title": 4,
    "search_terms": 3,
    "keywords": 3,
    "new_words": 3,
    "preview": 1,
    "body": 1,
    "type": 1,
}


def _documa_error_payload(exc: DocumaError) -> ToolPayload:
    payload = exc.to_dict()
    payload["status"] = "error"
    return payload


def load_document(path: str | Path) -> DocumentIR:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return document_from_plain_data(payload)


def write_payload(path: str | Path, payload: Any) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        temp_path.replace(output_path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def inspect_document(document: DocumentIR) -> ToolPayload:
    image_count = sum(len(page.images) for page in document.pages)
    block_count = sum(len(page.blocks) for page in document.pages)
    return {
        "status": "ok",
        "document_id": document.id,
        "source_name": document.source_name,
        "parser": document.parser,
        "page_count": document.page_count,
        "block_count": block_count,
        "table_count": len(document.tables),
        "image_count": image_count,
        "relation_count": len(document.relations),
        "document_block_count": len(document.document_blocks),
        "chunk_count": len(document.chunks),
    }


def parse_document_tool(
    source: str,
    out: str | None = None,
    lang: str = "auto",
    progress: str = "text",
) -> ToolPayload:
    output_dir = Path(out) if out else None
    asset_dir = output_dir / "assets" if output_dir else None
    languages = [part.strip() for part in lang.split(",") if part.strip()]

    try:
        document = adapter_for_source(source).parse(
            source,
            ParseOptions(
                languages=languages or ["auto"],
                asset_dir=asset_dir,
                metadata={"progress": progress},
            ),
        )
    except DocumaError as exc:
        return _documa_error_payload(exc)

    payload = to_plain_data(document)
    output_path = None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "documa.ir.json"
        write_payload(output_path, payload)

    return {
        "status": "ok",
        "document_id": document.id,
        "page_count": document.page_count,
        "parser": document.parser,
        "output_path": str(output_path) if output_path else None,
        "document": None if output_path else payload,
    }


def process_document_tool(
    source: str,
    out: str | None = None,
    lang: str = "auto",
    max_chars: int = 1200,
    export_formats: list[str] | str | None = None,
) -> ToolPayload:
    output_dir = Path(out) if out else None
    asset_dir = output_dir / "assets" if output_dir else None
    languages = [part.strip() for part in lang.split(",") if part.strip()]
    if isinstance(export_formats, str):
        export_formats = [export_formats]
    export_formats = export_formats or []

    try:
        document = adapter_for_source(source).parse(
            source,
            ParseOptions(languages=languages or ["auto"], asset_dir=asset_dir),
        )
    except DocumaError as exc:
        return _documa_error_payload(exc)

    context = PipelineContext(settings={"max_chars": max_chars})
    pipeline_run = run_default_pipeline(document, context, include_chunking=True)
    payload = to_plain_data(pipeline_run.document)
    output_path = None
    export_paths: dict[str, str] = {}

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "documa.ir.json"
        write_payload(output_path, payload)
        exporters = {
            "json": JsonExporter(),
            "markdown": MarkdownExporter(),
            "rag-json": RagJsonExporter(),
            "block-json": BlockJsonExporter(),
        }
        export_names = export_formats or ["rag-json"]
        for export_format in export_names:
            if export_format not in exporters:
                return {"status": "error", "message": f"Unsupported export format: {export_format}"}
            suffix = {"json": "json", "markdown": "md", "rag-json": "rag.json", "block-json": "blocks.json"}[
                export_format
            ]
            export_path = output_dir / f"documa.{suffix}"
            write_payload(export_path, exporters[export_format].export(pipeline_run.document, ExportOptions()))
            export_paths[export_format] = str(export_path)

    return {
        "status": "ok",
        "document_id": pipeline_run.document.id,
        "page_count": pipeline_run.document.page_count,
        "parser": pipeline_run.document.parser,
        "chunk_count": len(pipeline_run.document.chunks),
        "relation_count": len(pipeline_run.document.relations),
        "output_path": str(output_path) if output_path else None,
        "export_paths": export_paths,
        "pipeline": pipeline_run.report(),
        "document": None if output_path else payload,
    }


def export_document_tool(
    ir_path: str,
    format: str = "json",
    out: str | None = None,
    max_chars: int = 1200,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    if format == "rag-json" and not document.chunks:
        context = PipelineContext(settings={"max_chars": max_chars})
        if not document.document_blocks:
            BlockTreeBuildingStage().run(document, context)
            BlockKeywordExtractionStage().run(document, context)
        ChunkingStage().run(document, context)
        ProvenanceLinkingStage().run(document, context)
    if format == "block-json" and not document.document_blocks:
        context = PipelineContext(settings={})
        BlockTreeBuildingStage().run(document, context)
        BlockKeywordExtractionStage().run(document, context)

    exporters = {
        "json": JsonExporter(),
        "markdown": MarkdownExporter(),
        "rag-json": RagJsonExporter(),
        "block-json": BlockJsonExporter(),
    }
    if format not in exporters:
        return {"status": "error", "message": f"Unsupported export format: {format}"}

    payload = exporters[format].export(document, ExportOptions())
    output_path = None
    if out:
        write_payload(out, payload)
        output_path = out

    return {
        "status": "ok",
        "format": format,
        "document_id": document.id,
        "output_path": output_path,
        "content": None if output_path else payload,
    }


def inspect_document_tool(ir_path: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    return inspect_document(document)


def _ensure_document_blocks(document: DocumentIR) -> None:
    if not document.document_blocks:
        context = PipelineContext(settings={})
        BlockTreeBuildingStage().run(document, context)
        BlockKeywordExtractionStage().run(document, context)


def _block_index(document: DocumentIR) -> dict[str, Any]:
    return {block.id: block for block in document.document_blocks}


def _block_path(block_id: str, by_id: dict[str, Any]) -> list[str]:
    path = []
    current = by_id.get(block_id)
    while current is not None:
        if current.title:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _children_by_parent(document: DocumentIR) -> dict[str | None, list[Any]]:
    children: dict[str | None, list[Any]] = {}
    for block in document.document_blocks:
        children.setdefault(block.parent_id, []).append(block)
    for items in children.values():
        items.sort(key=lambda item: (item.order_index is None, item.order_index or 0))
    return children


def _keyword_is_cjk(keyword: str) -> bool:
    return bool(_CJK_RE.search(keyword))


def _find_hits(text: str, keyword: str) -> list[tuple[int, int]]:
    if not text or not keyword:
        return []
    haystack = text.casefold()
    needle = keyword.casefold()
    output: list[tuple[int, int]] = []
    position = 0
    while True:
        index = haystack.find(needle, position)
        if index < 0:
            return output
        output.append((index, index + len(needle)))
        position = index + max(1, len(needle))


def _make_snippet(text: str, start: int, end: int, keyword: str, *, chars: int = 24, words: int = 8) -> str:
    if _keyword_is_cjk(keyword):
        left = max(0, start - chars)
        right = min(len(text), end + chars)
    else:
        prefix_matches = list(_WORD_RE.finditer(text[:start]))
        suffix_matches = list(_WORD_RE.finditer(text[end:]))
        left = prefix_matches[-words].start() if len(prefix_matches) > words else 0
        right = end + suffix_matches[words - 1].end() if len(suffix_matches) > words else len(text)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def _search_terms(query: str | None, any_of: list[str] | None = None) -> list[str]:
    raw_terms = [term for term in str(query or "").split() if term.strip()]
    if any_of:
        raw_terms.extend(str(term).strip() for term in any_of if str(term).strip())
    if not raw_terms and str(query or "").strip():
        raw_terms = [str(query).strip()]

    output: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        folded = term.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        output.append(term)
    return output


def _normalized_verbosity(value: str | None) -> str:
    verbosity = (value or "compact").casefold()
    return verbosity if verbosity in _SEARCH_VERBOSITIES else "compact"


def list_blocks_tool(
    ir_path: str,
    depth: int | None = None,
    parent_id: str | None = None,
    include_metadata_summary: bool = True,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)

    blocks = []
    for block in sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0)):
        if depth is not None and block.depth > depth:
            continue
        if parent_id is not None and block.parent_id != parent_id:
            continue
        item = {
            "id": block.id,
            "type": block.type.value,
            "title": block.title,
            "parent_id": block.parent_id,
            "depth": block.depth,
            "children_count": len(block.child_ids),
            "page_refs": block.page_refs,
            "text_preview": block.text_preview,
            "source_range": block.metadata.get("source_range"),
        }
        if include_metadata_summary:
            item["metadata_summary"] = {
                "keywords": block.metadata.get("keyword_terms", [])[:8],
                "new_words": [entry.get("term") for entry in block.metadata.get("new_word_terms", [])[:8]],
                "confidence": block.confidence.value,
                "role": block.metadata.get("role"),
            }
        blocks.append(item)

    return {"status": "ok", "document_id": document.id, "block_count": len(blocks), "blocks": blocks}


def inspect_block_tool(ir_path: str, block_id: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_id = _block_index(document)
    block = by_id.get(block_id)
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}
    return {
        "status": "ok",
        "document_id": document.id,
        "block": to_plain_data(block),
        "block_path": _block_path(block.id, by_id),
    }


def read_block_tool(
    ir_path: str,
    block_id: str,
    include_children: bool = False,
    max_chars: int | None = None,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_id = _block_index(document)
    block = by_id.get(block_id)
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}

    selected = [block]
    if include_children:
        pending = list(block.child_ids)
        while pending:
            child_id = pending.pop(0)
            child = by_id.get(child_id)
            if child is None:
                continue
            selected.append(child)
            pending.extend(child.child_ids)
    text = "\n\n".join(document_block_text(document, item) for item in selected if document_block_text(document, item)).strip()
    truncated = False
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return {
        "status": "ok",
        "document_id": document.id,
        "block_id": block.id,
        "block_path": _block_path(block.id, by_id),
        "content": text,
        "truncated": truncated,
        "source_block_ids": [source_id for item in selected for source_id in item.source_block_ids],
        "page_refs": sorted({page for item in selected for page in item.page_refs}),
    }


def search_blocks_tool(
    ir_path: str,
    query: str = "",
    limit: int = 10,
    any_of: list[str] | None = None,
    fields: list[str] | None = None,
    snippet_fields: list[str] | None = None,
    verbosity: str = "compact",
    include_snippets: bool = True,
    max_snippets_per_block: int = 5,
    search_body: bool = True,
    context_chars: int = 24,
    context_words: int = 8,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_id = _block_index(document)

    raw_terms = _search_terms(query, any_of)
    output_verbosity = _normalized_verbosity(verbosity)
    selected_fields = list(fields or _DEFAULT_SEARCH_FIELDS)
    if search_body and "body" not in selected_fields:
        selected_fields.append("body")
    if not search_body:
        selected_fields = [field for field in selected_fields if field != "body"]
    snippet_field_set = set(snippet_fields) if snippet_fields is not None else set(_DEFAULT_SNIPPET_FIELDS)

    if not raw_terms:
        return {
            "status": "ok",
            "document_id": document.id,
            "query": query,
            "terms": [],
            "searched_fields": selected_fields,
            "snippet_policy": {
                "include_snippets": include_snippets,
                "max_snippets_per_block": max_snippets_per_block,
                "search_body": search_body,
                "snippet_fields": sorted(snippet_field_set),
                "context_chars": context_chars,
                "context_words": context_words,
            },
            "verbosity": output_verbosity,
            "results": [],
        }

    query_terms = [term.casefold() for term in raw_terms]
    max_snippets = max(0, int(max_snippets_per_block))
    results = []
    for block in document.document_blocks:
        body: str | None = None
        field_values = {
            "title": block.title or "",
            "preview": block.text_preview or "",
            "type": block.type.value,
            "search_terms": " ".join(str(item) for item in block.metadata.get("search_terms", [])),
            "keywords": " ".join(str(item) for item in block.metadata.get("keyword_terms", [])),
            "new_words": " ".join(str(item.get("term")) for item in block.metadata.get("new_word_terms", [])),
        }
        field_texts = []
        for field_name in selected_fields:
            if field_name == "body":
                if body is None:
                    body = document_block_text(document, block)
                field_text = body
            else:
                field_text = field_values.get(field_name, "")
            if field_text:
                field_texts.append((field_name, field_text))
        score = 0
        matched: list[str] = []
        matches: dict[str, list[str]] = {}
        snippets: list[dict[str, str]] = []
        for raw_term, term in zip(raw_terms, query_terms):
            term_hit = False
            for field_name, field_text in field_texts:
                hits = _find_hits(field_text, term)
                if not hits:
                    continue
                term_hit = True
                matches.setdefault(field_name, []).append(raw_term)
                multiplier = _SEARCH_FIELD_WEIGHTS.get(field_name, 1)
                score += len(hits) * multiplier
                for start, end in hits:
                    if not include_snippets or field_name not in snippet_field_set or len(snippets) >= max_snippets:
                        break
                    snippets.append(
                        {
                            "field": field_name,
                            "keyword": raw_term,
                            "snippet": _make_snippet(
                                field_text,
                                start,
                                end,
                                raw_term,
                                chars=context_chars,
                                words=context_words,
                            ),
                        }
                    )
            if term_hit and raw_term not in matched:
                matched.append(raw_term)
        if query and query.casefold() in " ".join(text for _, text in field_texts).casefold():
            score += 2
        if score <= 0:
            continue
        row = {
            "id": block.id,
            "type": block.type.value,
            "title": block.title,
            "score": score,
            "page_refs": block.page_refs,
            "children_count": len(block.child_ids),
            "matched": matched,
            "snippets": snippets,
        }
        if output_verbosity in {"standard", "debug"}:
            row.update(
                {
                    "block_path": _block_path(block.id, by_id),
                    "text_preview": block.text_preview,
                    "keywords": block.metadata.get("keyword_terms", [])[:5],
                    "source_block_ids": block.source_block_ids,
                }
            )
        if output_verbosity == "debug":
            row.update(
                {
                    "searched_fields": selected_fields,
                    "matches": matches,
                    "new_words": [entry.get("term") for entry in block.metadata.get("new_word_terms", [])[:8]],
                }
            )
        results.append(row)
    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "status": "ok",
        "document_id": document.id,
        "query": query,
        "terms": raw_terms,
        "searched_fields": selected_fields,
        "snippet_policy": {
            "include_snippets": include_snippets,
            "max_snippets_per_block": max_snippets,
            "search_body": search_body,
            "snippet_fields": sorted(snippet_field_set),
            "context_chars": context_chars,
            "context_words": context_words,
        },
        "verbosity": output_verbosity,
        "results": results[:limit],
    }


def block_tree_tool(ir_path: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_parent = _children_by_parent(document)

    def node(block: Any) -> dict[str, Any]:
        return {
            "id": block.id,
            "type": block.type.value,
            "title": block.title,
            "depth": block.depth,
            "page_refs": block.page_refs,
            "source_range": block.metadata.get("source_range"),
            "children": [node(child) for child in by_parent.get(block.id, [])],
        }

    roots = [node(block) for block in by_parent.get(None, [])]
    return {"status": "ok", "document_id": document.id, "tree": roots}


def block_xref_tool(ir_path: str, block_id: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_id = _block_index(document)
    block = by_id.get(block_id)
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}

    def ref(target_id: str | None) -> dict[str, Any] | None:
        if not target_id:
            return None
        target = by_id.get(target_id)
        if target is None:
            return {"id": target_id, "found": False}
        return {
            "id": target.id,
            "found": True,
            "type": target.type.value,
            "title": target.title,
            "page_refs": target.page_refs,
            "source_range": target.metadata.get("source_range"),
        }

    relation_refs = [
        {
            "id": relation.id,
            "type": relation.type.value,
            "from_id": relation.from_id,
            "to_id": relation.to_id,
            "state": relation.state.value,
        }
        for relation in document.relations
        if relation.from_id == block_id or relation.to_id == block_id
    ]
    return {
        "status": "ok",
        "document_id": document.id,
        "id": block.id,
        "parent": ref(block.parent_id),
        "children": [ref(child_id) for child_id in block.child_ids],
        "source_block_ids": block.source_block_ids,
        "source_chunk_ids": block.source_chunk_ids,
        "relations": relation_refs,
    }


def benchmark_tool(
    manifest_path: str = "fixtures/pdf/manifest.json",
    fixtures_dir: str = "fixtures/pdf",
    out: str | None = None,
    require_files: bool = False,
) -> ToolPayload:
    try:
        payload = run_fixture_benchmark(
            BenchmarkOptions(
                manifest_path=Path(manifest_path),
                fixtures_dir=Path(fixtures_dir),
                require_files=require_files,
            )
        )
    except (OSError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    if out:
        write_payload(out, payload)
        payload["output_path"] = out
    else:
        payload["output_path"] = None
    return payload


def doctor_tool(project_root: str = ".", include_benchmark: bool = True) -> ToolPayload:
    try:
        return run_doctor(DoctorOptions(project_root=Path(project_root), include_benchmark=include_benchmark))
    except (OSError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}


def list_documa_tools() -> list[dict[str, Any]]:
    return documa_tool_schemas()


def _tool_registry() -> dict[str, Callable[..., ToolPayload]]:
    return {
        "documa_parse": parse_document_tool,
        "documa_process": process_document_tool,
        "documa_export": export_document_tool,
        "documa_inspect": inspect_document_tool,
        "documa_list_blocks": list_blocks_tool,
        "documa_inspect_block": inspect_block_tool,
        "documa_read_block": read_block_tool,
        "documa_search_blocks": search_blocks_tool,
        "documa_block_tree": block_tree_tool,
        "documa_block_xref": block_xref_tool,
        "documa_benchmark": benchmark_tool,
        "documa_doctor": doctor_tool,
    }


def call_documa_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call a Documa tool and return an MCP-compatible tool result shape."""

    arguments = arguments or {}
    registry = _tool_registry()
    if name not in registry:
        payload = {"status": "error", "message": f"Unknown Documa tool: {name}"}
        return _tool_result(payload, is_error=True)

    try:
        payload = registry[name](**arguments)
    except TypeError as exc:
        payload = {"status": "error", "message": str(exc)}
        return _tool_result(payload, is_error=True)
    except Exception as exc:  # pragma: no cover - last-resort tool boundary guard
        payload = {"status": "error", "message": f"Tool execution failed: {exc}"}
        return _tool_result(payload, is_error=True)

    return _tool_result(payload, is_error=payload.get("status") == "error" or "error" in payload)


def _tool_result(payload: ToolPayload, *, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }
