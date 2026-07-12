"""Tool execution layer for CLI, MCP, and direct LLM tool calling."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.collections import registry as registry_store
from documa.collections import sqlite_index as collection_index
from documa.collections.email_collection import MailboxIngestionOptions, ingest_mailbox_collection
from documa.core.errors import DocumaError
from documa.core.ir import DocumentBlockIR, DocumentBlockType, DocumentIR, repair_surrogate_text, to_plain_data
from documa.core.serialization import document_from_plain_data
from documa.exporters import BlockJsonExporter, ExportOptions, JsonExporter, MarkdownExporter, RagJsonExporter
from documa.interfaces import citation, search_ranking
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
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata
from documa.viewer import VIEWER_FORMATS, ViewerOptions, build_universal_viewer, render_viewer
from documa.quality import BenchmarkOptions, DoctorOptions, run_doctor, run_fixture_benchmark


ToolPayload = dict[str, Any]
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"\S+")
_DEFAULT_SEARCH_FIELDS = ["title", "preview", "search_terms", "keywords", "new_words"]
_DEFAULT_SNIPPET_FIELDS = {"body", "title", "preview"}
_SEARCH_VERBOSITIES = {"compact", "standard", "debug"}
_DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|percent|percentage|pp|bps|x|times)?", re.IGNORECASE)
_TREND_PATTERN = re.compile(
    r"\b(increase|decrease|decline|declined|rise|rose|rising|fall|fell|growth|grew|drop|dropped|higher|lower|trend)\b",
    re.IGNORECASE,
)
_DEFINITION_PATTERN = re.compile(r"\b(is defined as|refers to|means|definition|defined as)\b", re.IGNORECASE)
_CAUSE_PATTERN = re.compile(r"\b(because|due to|driven by|caused by|reason|therefore|as a result)\b", re.IGNORECASE)
_COMPARISON_PATTERN = re.compile(r"\b(compared with|compared to|versus|vs\.?|relative to|more than|less than)\b", re.IGNORECASE)
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


_ADAPTER_DISTRIBUTIONS = {
    "pymupdf": "PyMuPDF",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "html": "beautifulsoup4",
    "msg": "extract-msg",
    "ipynb": "nbformat",
}


def _stamp_provenance(document: DocumentIR, pipeline_profile: str | None = None) -> None:
    """Record producer/adapter versions and the pipeline profile on the IR."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        document.producer_version = version("documa")
    except PackageNotFoundError:
        document.producer_version = None
    distribution = _ADAPTER_DISTRIBUTIONS.get(document.parser or "")
    if distribution:
        try:
            document.adapter_version = f"{document.parser}/{version(distribution)}"
        except PackageNotFoundError:
            document.adapter_version = None
    document.pipeline_profile = pipeline_profile


# Parsed-document cache keyed by (resolved path, mtime_ns, size): the key
# self-invalidates whenever the IR file is rewritten. Cached DocumentIR objects
# are shared across tool calls, so tools may only apply idempotent in-place
# enrichment (block tree build, page-citation map) — never destructive mutation.
_DOCUMENT_CACHE: OrderedDict[tuple[str, int, int], DocumentIR] = OrderedDict()
_DOCUMENT_CACHE_MAX_ENTRIES = 8


def clear_document_cache() -> None:
    """Drop all cached parsed documents (used by tests and after bulk rewrites)."""
    _DOCUMENT_CACHE.clear()


def _resolve_document_path(path: str | Path) -> Path:
    """Resolve a file path or ``doc-`` registry reference to an IR file path.

    Resolution rule: an existing file path always wins; otherwise a ``doc-``
    prefixed reference is looked up in the local registry (``./.documa``).
    """
    resolved = Path(path)
    if not resolved.exists():
        ref = str(path)
        if ref.startswith(registry_store.DOCUMENT_ID_PREFIX):
            registry_path = registry_store.resolve_ir_path(registry_store.DEFAULT_STORE_DIR, ref)
            if registry_path is None or not registry_path.exists():
                raise FileNotFoundError(
                    f"DOCUMENT_ID_NOT_FOUND: no file at {ref!r} and no registry entry in ./{registry_store.DEFAULT_STORE_DIR}"
                )
            resolved = registry_path
    return resolved


def _load_document_uncached(path: str | Path) -> DocumentIR:
    """Parse an IR document fresh from disk, bypassing the shared cache.

    Use this when the caller applies parameterized mutation (e.g. chunking with
    a caller-supplied max_chars) that must not leak into the shared cache.
    """
    resolved = _resolve_document_path(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return document_from_plain_data(payload)


def load_document(path: str | Path) -> DocumentIR:
    """Load an IR document from a file path or a registry document_id."""
    resolved = _resolve_document_path(path)
    stat = resolved.stat()
    cache_key = (str(resolved.resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _DOCUMENT_CACHE.get(cache_key)
    if cached is not None:
        _DOCUMENT_CACHE.move_to_end(cache_key)
        return cached
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    document = document_from_plain_data(payload)
    _DOCUMENT_CACHE[cache_key] = document
    while len(_DOCUMENT_CACHE) > _DOCUMENT_CACHE_MAX_ENTRIES:
        _DOCUMENT_CACHE.popitem(last=False)
    return document


def write_payload(path: str | Path, payload: Any) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = repair_surrogate_text(payload) if isinstance(payload, str) else json.dumps(to_plain_data(payload), ensure_ascii=False, indent=2)
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

    _stamp_provenance(document)
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
    ocr: bool = False,
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

    context = PipelineContext(
        settings={"max_chars": max_chars, "ocr": ocr, "source_path": str(Path(source).resolve())}
    )
    pipeline_run = run_default_pipeline(document, context, include_chunking=True)
    _stamp_provenance(pipeline_run.document, pipeline_profile="ocr" if ocr else "default")
    payload = to_plain_data(pipeline_run.document)

    warnings = []
    for stage in pipeline_run.stage_results:
        reason = stage.report.get("skipped_reason")
        if stage.stage_name == "ocr" and ocr and reason:
            warnings.append(f"ocr: {reason}")
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
        "warnings": warnings,
        "pipeline": pipeline_run.report(),
        "document": None if output_path else payload,
    }


def ingest_mailbox_tool(
    source: str,
    out: str,
    lang: str = "auto",
    max_chars: int = 1200,
    export_formats: list[str] | str | None = None,
    recursive: bool = False,
    continue_on_error: bool = True,
    progress: str = "text",
) -> ToolPayload:
    if isinstance(export_formats, str):
        export_formats = [export_formats]
    export_formats = export_formats or ["rag-json", "block-json"]

    def process_message(
        message_source: str,
        message_out: str,
        message_lang: str,
        message_max_chars: int,
        message_export_formats: list[str] | None,
    ) -> ToolPayload:
        return process_document_tool(
            source=message_source,
            out=message_out,
            lang=message_lang,
            max_chars=message_max_chars,
            export_formats=message_export_formats,
        )

    payload = ingest_mailbox_collection(
        MailboxIngestionOptions(
            source=Path(source),
            out=Path(out),
            lang=lang,
            max_chars=max_chars,
            export_formats=export_formats,
            recursive=recursive,
            continue_on_error=continue_on_error,
            progress=progress,
        ),
        process_message=process_message,
    )
    if payload.get("status") in {"ok", "partial"}:
        manifest = payload.pop("manifest")
        write_payload(payload["manifest_path"], manifest)
        if progress == "jsonl":
            progress_path = Path(out) / "documa.mailbox.progress.jsonl"
            lines = "\n".join(json.dumps(to_plain_data(event), ensure_ascii=False) for event in payload.get("progress_events", []))
            write_payload(progress_path, f"{lines}\n" if lines else "")
            payload["progress_path"] = str(progress_path)
    return payload


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
        # Chunking depends on the caller-supplied max_chars, so it must run on a
        # private copy — never on the shared cached document.
        document = _load_document_uncached(ir_path)
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


def view_document_tool(
    source: str | None = None,
    ir_path: str | None = None,
    out: str | None = None,
    format: str = "json",
    query: str = "",
    lang: str = "auto",
    max_chars: int = 1200,
    max_depth: int | None = None,
    include_body: bool = False,
    body_chars: int = 1200,
    result_limit: int = 10,
) -> ToolPayload:
    if bool(source) == bool(ir_path):
        return {"status": "error", "message": "Provide exactly one of source or ir_path."}
    if format not in VIEWER_FORMATS:
        return {"status": "error", "message": f"Unsupported viewer format: {format}"}

    output_path = Path(out) if out else None
    try:
        if source:
            output_dir = output_path.parent if output_path else None
            asset_dir = output_dir / "assets" if output_dir else None
            languages = [part.strip() for part in lang.split(",") if part.strip()]
            document = adapter_for_source(source).parse(
                source,
                ParseOptions(languages=languages or ["auto"], asset_dir=asset_dir),
            )
            pipeline_run = run_default_pipeline(
                document,
                PipelineContext(settings={"max_chars": max_chars}),
                include_chunking=True,
            )
            document = pipeline_run.document
        else:
            document = load_document(str(ir_path))
    except DocumaError as exc:
        return _documa_error_payload(exc)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    viewer = build_universal_viewer(
        document,
        ViewerOptions(
            query=query,
            max_depth=max_depth,
            include_body=include_body,
            body_chars=body_chars,
            result_limit=result_limit,
        ),
    )
    content = render_viewer(viewer, format)
    if output_path:
        write_payload(output_path, content)

    payload: ToolPayload = {
        "status": "ok",
        "document_id": document.id,
        "format": format,
        "output_path": str(output_path) if output_path else None,
        "query": query,
    }
    if output_path:
        payload["viewer"] = None
        payload["content"] = None
    elif format == "json":
        payload["viewer"] = content
        payload["content"] = None
    else:
        payload["viewer"] = viewer
        payload["content"] = content
    return payload


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


def _estimate_tokens(text: str) -> int:
    """CJK-aware token estimate: ~0.8 token per CJK char, ~4 chars per token otherwise.

    The naive chars/4 heuristic under-reports CJK text roughly threefold,
    which silently blows agent context budgets on Chinese documents.
    """
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    return max(1, math.ceil(cjk_count * 0.8 + (len(text) - cjk_count) / 4))


def _truncate_to_token_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    """Cut text at the character where the running token estimate exceeds the budget."""
    budget = float(max_tokens)
    cost = 0.0
    for index, char in enumerate(text):
        cost += 0.8 if _CJK_RE.match(char) else 0.25
        if cost > budget:
            return text[:index], True
    return text, False


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


def _block_doc_region(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> str:
    title = " ".join(_block_path(block.id, by_id) + [block.title or ""]).casefold()
    source_type = str(block.metadata.get("source_block_type") or "").casefold()
    role = str(block.metadata.get("role") or "").casefold()
    if block.type == DocumentBlockType.TOC or "table of contents" in title or "contents" == title.strip():
        return "toc"
    if block.type == DocumentBlockType.METADATA:
        return "metadata"
    if "page_header" in {source_type, role} or "page_footer" in {source_type, role}:
        return "header_footer"
    if any(term in title for term in ("references", "bibliography", "works cited")):
        return "references"
    if "appendix" in title or "annex" in title:
        return "appendix"
    return "body"


def _ordered_block_positions(document: DocumentIR) -> tuple[list[DocumentBlockIR], dict[str, int]]:
    """Reading-order block list plus id->position map, computed once per tool call."""
    ordered = sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0))
    positions = {item.id: index for index, item in enumerate(ordered)}
    return ordered, positions


def _block_neighbor_metadata(
    block: DocumentBlockIR,
    ordered: list[DocumentBlockIR],
    positions: dict[str, int],
) -> dict[str, Any]:
    index = positions.get(block.id)
    prev_id = ordered[index - 1].id if index is not None and index > 0 else None
    next_id = ordered[index + 1].id if index is not None and index + 1 < len(ordered) else None
    text = (block.text_preview or "").strip()
    needs_next = block.type == DocumentBlockType.TABLE or (bool(text) and text[-1:] not in {".", "!", "?", "。", "！", "？"})
    return {"prev": prev_id, "next": next_id, "needs_next": needs_next}


def _block_answer_tags(text: str, block: DocumentBlockIR) -> list[str]:
    tags: list[str] = []
    if _DEFINITION_PATTERN.search(text):
        tags.append("definition")
    if _TREND_PATTERN.search(text):
        tags.append("trend")
    if _COMPARISON_PATTERN.search(text):
        tags.append("comparison")
    if _CAUSE_PATTERN.search(text):
        tags.append("cause")
    if _NUMBER_PATTERN.search(text):
        tags.append("numeric")
    if _DATE_PATTERN.search(text):
        tags.append("date")
    if block.type == DocumentBlockType.TABLE:
        tags.append("table")
    return list(dict.fromkeys(tags))


def _selection_metadata(
    block: DocumentBlockIR,
    document: DocumentIR,
    by_id: dict[str, DocumentBlockIR],
    ordered: list[DocumentBlockIR],
    positions: dict[str, int],
    doc_region: str | None = None,
) -> dict[str, Any]:
    content = document_block_text(document, block)
    selection_text = " ".join(part for part in [block.title or "", block.text_preview or "", content[:1200]] if part)
    char_count = len(content)
    token_estimate = _estimate_tokens(content)
    doc_region = doc_region if doc_region is not None else _block_doc_region(block, by_id)
    return {
        "block_id": block.id,
        "block_type": block.type.value,
        "heading_path": _block_path(block.id, by_id),
        "doc_region": doc_region,
        "answer_tags": _block_answer_tags(selection_text, block),
        "char_count": char_count,
        "token_estimate": token_estimate,
        "recommended_read_chars": min(3000, max(800, char_count if char_count else len(block.text_preview or ""))),
        "neighbors": _block_neighbor_metadata(block, ordered, positions),
        "flags": {
            "has_numeric": bool(_NUMBER_PATTERN.search(selection_text)),
            "has_date": bool(_DATE_PATTERN.search(selection_text)),
            "has_table": block.type == DocumentBlockType.TABLE,
            "is_reference": doc_region == "references",
            "is_header_footer": doc_region == "header_footer",
        },
        "dedupe_key": (block.content_hash or hashlib.sha1(selection_text.encode("utf-8", errors="ignore")).hexdigest())[:16],
    }


def list_blocks_tool(
    ir_path: str,
    depth: int | None = None,
    parent_id: str | None = None,
    include_metadata_summary: bool = True,
    limit: int | None = None,
    offset: int = 0,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)

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
            **page_citation_metadata(block.page_refs, page_citations),
        }
        if include_metadata_summary:
            item["metadata_summary"] = {
                "keywords": block.metadata.get("keyword_terms", [])[:8],
                "new_words": [entry.get("term") for entry in block.metadata.get("new_word_terms", [])[:8]],
                "confidence": block.confidence.value,
                "role": block.metadata.get("role"),
            }
        blocks.append(item)

    total_blocks = len(blocks)
    offset = max(0, int(offset))
    if offset or limit is not None:
        blocks = blocks[offset : offset + limit] if limit is not None else blocks[offset:]
    return {
        "status": "ok",
        "document_id": document.id,
        "block_count": len(blocks),
        "total_blocks": total_blocks,
        "offset": offset,
        "has_more": offset + len(blocks) < total_blocks,
        "blocks": blocks,
    }


def inspect_block_tool(ir_path: str, block_id: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_id = _block_index(document)
    block = by_id.get(block_id)
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}
    block_payload = to_plain_data(block)
    block_payload.setdefault("metadata", {}).update(page_citation_metadata(block.page_refs, page_citations))
    return {
        "status": "ok",
        "document_id": document.id,
        "block": block_payload,
        **page_citation_metadata(block.page_refs, page_citations),
        "block_path": _block_path(block.id, by_id),
    }


def read_block_tool(
    ir_path: str,
    block_id: str,
    include_children: bool = False,
    max_chars: int | None = None,
    max_tokens: int | None = None,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
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
    if max_tokens is not None:
        text, token_truncated = _truncate_to_token_budget(text, max_tokens)
        truncated = truncated or token_truncated
    page_refs = sorted({page for item in selected for page in item.page_refs})
    return {
        "status": "ok",
        "document_id": document.id,
        "block_id": block.id,
        "block_path": _block_path(block.id, by_id),
        "content": text,
        "truncated": truncated,
        "token_estimate": _estimate_tokens(text),
        "source_block_ids": [source_id for item in selected for source_id in item.source_block_ids],
        "page_refs": page_refs,
        **page_citation_metadata(page_refs, page_citations),
    }


def search_blocks_tool(
    ir_path: str,
    query: str = "",
    limit: int = 10,
    offset: int = 0,
    any_of: list[str] | None = None,
    fields: list[str] | None = None,
    snippet_fields: list[str] | None = None,
    verbosity: str = "compact",
    include_snippets: bool = True,
    max_snippets_per_block: int = 2,
    search_body: bool = True,
    context_chars: int = 24,
    context_words: int = 8,
    max_response_tokens: int | None = None,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
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
            "total_matches": 0,
            "offset": 0,
            "results": [],
        }

    query_terms = [term.casefold() for term in raw_terms]
    max_snippets = max(0, int(max_snippets_per_block))
    ordered_blocks, block_positions = _ordered_block_positions(document)

    # Pass 1: collect per-term/per-field hit counts, block frequencies for IDF,
    # and body-length statistics; snippets are extracted here so field text is
    # only materialized once per block.
    candidates = []
    term_block_frequency = [0] * len(query_terms)
    body_length_total = 0
    body_length_count = 0
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
        body_length = len(body) if body is not None else 0
        if body is not None:
            body_length_total += body_length
            body_length_count += 1
        matched: list[str] = []
        matches: dict[str, list[str]] = {}
        snippets: list[dict[str, str]] = []
        seen_snippet_texts: set[str] = set()
        term_field_hits: list[list[tuple[str, int]]] = []
        for term_index, (raw_term, term) in enumerate(zip(raw_terms, query_terms)):
            field_hits: list[tuple[str, int]] = []
            for field_name, field_text in field_texts:
                hits = _find_hits(field_text, term)
                if not hits:
                    continue
                matches.setdefault(field_name, []).append(raw_term)
                field_hits.append((field_name, len(hits)))
                for start, end in hits:
                    if not include_snippets or field_name not in snippet_field_set or len(snippets) >= max_snippets:
                        break
                    snippet_text = _make_snippet(
                        field_text,
                        start,
                        end,
                        raw_term,
                        chars=context_chars,
                        words=context_words,
                    )
                    # Preview is usually a prefix of body, so different fields
                    # often yield the same snippet — sending it twice is waste.
                    if snippet_text in seen_snippet_texts:
                        continue
                    seen_snippet_texts.add(snippet_text)
                    snippets.append({"field": field_name, "keyword": raw_term, "snippet": snippet_text})
            if field_hits:
                term_block_frequency[term_index] += 1
                if raw_term not in matched:
                    matched.append(raw_term)
            term_field_hits.append(field_hits)
        exact_phrase = bool(query) and query.casefold() in " ".join(text for _, text in field_texts).casefold()
        if not matched and not exact_phrase:
            continue
        candidates.append((block, term_field_hits, matched, matches, snippets, body_length, exact_phrase))

    # Pass 2: BM25-lite scoring — IDF over blocks, saturated term frequency,
    # existing field weights as multipliers, mild length normalization on body
    # hits, and doc-region demotion (TOC/furniture never outrank body evidence).
    block_count = len(document.document_blocks)
    average_body_length = body_length_total / body_length_count if body_length_count else 0.0
    results = []
    for block, term_field_hits, matched, matches, snippets, body_length, exact_phrase in candidates:
        score = 0.0
        for term_index, field_hits in enumerate(term_field_hits):
            if not field_hits:
                continue
            idf = search_ranking.inverse_block_frequency(block_count, term_block_frequency[term_index])
            for field_name, hit_count in field_hits:
                field_score = _SEARCH_FIELD_WEIGHTS.get(field_name, 1) * search_ranking.saturated_term_frequency(hit_count)
                if field_name == "body":
                    field_score *= search_ranking.body_length_normalization(body_length, average_body_length)
                score += idf * field_score
        if exact_phrase:
            score += 2.0
        doc_region = _block_doc_region(block, by_id)
        score *= search_ranking.doc_region_multiplier(doc_region)
        if score <= 0:
            continue
        row = {
            "id": block.id,
            "block_id": block.id,
            "type": block.type.value,
            "block_type": block.type.value,
            "title": block.title,
            "heading_path": _block_path(block.id, by_id),
            "score": round(score, 4),
            "page_refs": block.page_refs,
            **page_citation_metadata(block.page_refs, page_citations),
            "children_count": len(block.child_ids),
            "matched": matched,
            "matched_terms": matched,
            "matched_terms_count": len(set(matched)),
            "snippets": snippets,
        }
        selection = _selection_metadata(block, document, by_id, ordered_blocks, block_positions, doc_region)
        row.update(
            {
                "doc_region": selection["doc_region"],
                "answer_tags": selection["answer_tags"],
                "token_estimate": selection["token_estimate"],
                "recommended_read_chars": selection["recommended_read_chars"],
                "neighbors": selection["neighbors"],
                "flags": selection["flags"],
                "dedupe_key": selection["dedupe_key"],
            }
        )
        if output_verbosity in {"standard", "debug"}:
            row.update(
                {
                    "block_path": _block_path(block.id, by_id),
                    "selection_metadata": selection,
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
    offset = max(0, int(offset))
    payload: ToolPayload = {
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
        "total_matches": len(results),
        "offset": offset,
    }
    page = results[offset : offset + limit]
    if max_response_tokens is not None:
        # Greedily keep ranked rows while the serialized-response estimate fits
        # the caller's hard ceiling; report what was dropped so the caller can
        # page instead of re-querying blindly.
        kept: list[dict[str, Any]] = []
        spent = _estimate_tokens(json.dumps(payload, ensure_ascii=False))
        for row in page:
            row_cost = _estimate_tokens(json.dumps(row, ensure_ascii=False))
            if kept and spent + row_cost > max_response_tokens:
                break
            kept.append(row)
            spent += row_cost
        payload["budget"] = {
            "max_response_tokens": max_response_tokens,
            "spent_estimate": spent,
            "dropped_results": len(page) - len(kept),
        }
        page = kept
    payload["results"] = page
    return payload


def block_tree_tool(ir_path: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_parent = _children_by_parent(document)

    def node(block: Any) -> dict[str, Any]:
        return {
            "id": block.id,
            "type": block.type.value,
            "title": block.title,
            "depth": block.depth,
            "page_refs": block.page_refs,
            **page_citation_metadata(block.page_refs, page_citations),
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
    page_citations = ensure_page_citation_map(document)
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
            **page_citation_metadata(target.page_refs, page_citations),
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
        "page_refs": block.page_refs,
        **page_citation_metadata(block.page_refs, page_citations),
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
    mode: str = "readiness",
    gold_dir: str = "fixtures/pdf/gold",
    quality_threshold: float = 0.85,
) -> ToolPayload:
    try:
        payload = run_fixture_benchmark(
            BenchmarkOptions(
                manifest_path=Path(manifest_path),
                fixtures_dir=Path(fixtures_dir),
                require_files=require_files,
                mode=mode,
                gold_dir=Path(gold_dir),
                quality_threshold=quality_threshold,
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


def doctor_tool(
    project_root: str = ".",
    include_benchmark: bool = True,
    store_dir: str | None = None,
) -> ToolPayload:
    try:
        payload = run_doctor(DoctorOptions(project_root=Path(project_root), include_benchmark=include_benchmark))
    except (OSError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    if store_dir is not None:
        payload["store_health"] = registry_store.store_health(store_dir=store_dir)
        payload["collection_health"] = collection_index.store_collection_health(store_dir=store_dir)
        if (
            payload["store_health"]["status"] != "ok"
            or payload["collection_health"]["status"] != "ok"
        ) and payload.get("status") == "ok":
            payload["status"] = "warning"
    return payload


def _load_document_or_error(ir_path: str) -> DocumentIR | ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "code": "IR_LOAD_FAILED", "message": str(exc), "ir_path": str(ir_path)}
    _ensure_document_blocks(document)
    return document


def cite_block_tool(ir_path: str, block_id: str, style: str = "page-bbox") -> ToolPayload:
    document = _load_document_or_error(ir_path)
    if isinstance(document, dict):
        return document
    return citation.cite_block(document, block_id, style)


def cite_chunk_tool(ir_path: str, chunk_id: str, style: str = "page-bbox") -> ToolPayload:
    document = _load_document_or_error(ir_path)
    if isinstance(document, dict):
        return document
    return citation.cite_chunk(document, chunk_id, style)


def render_citation_tool(ir_path: str, ref_id: str, style: str = "page-bbox") -> ToolPayload:
    document = _load_document_or_error(ir_path)
    if isinstance(document, dict):
        return document
    return citation.render_citation(document, ref_id, style)


def source_window_tool(ir_path: str, block_id: str, before: int = 1, after: int = 1) -> ToolPayload:
    document = _load_document_or_error(ir_path)
    if isinstance(document, dict):
        return document
    return citation.source_window(document, block_id, before=before, after=after)


def verify_citations_tool(ir_path: str, block_ids: list[str] | str) -> ToolPayload:
    if isinstance(block_ids, str):
        block_ids = [part.strip() for part in block_ids.split(",") if part.strip()]
    document = _load_document_or_error(ir_path)
    if isinstance(document, dict):
        return document
    return citation.verify_citations(document, block_ids)


def ingest_document_tool(
    source: str,
    store_dir: str = registry_store.DEFAULT_STORE_DIR,
    lang: str = "auto",
    max_chars: int = 1200,
    ocr: bool = False,
) -> ToolPayload:
    return registry_store.ingest_document(source, store_dir=store_dir, lang=lang, max_chars=max_chars, ocr=ocr)


def index_collection_tool(
    store_dir: str = registry_store.DEFAULT_STORE_DIR,
    collection_id: str = collection_index.DEFAULT_COLLECTION_ID,
) -> ToolPayload:
    return collection_index.build_collection_index(store_dir=store_dir, collection_id=collection_id)


def search_collection_tool(
    query: str,
    store_dir: str = registry_store.DEFAULT_STORE_DIR,
    collection_id: str = collection_index.DEFAULT_COLLECTION_ID,
    limit: int = 20,
    offset: int = 0,
    per_document_limit: int | None = None,
) -> ToolPayload:
    return collection_index.search_collection(
        store_dir=store_dir,
        query=query,
        collection_id=collection_id,
        limit=limit,
        offset=offset,
        per_document_limit=per_document_limit,
    )


def list_documents_tool(store_dir: str = registry_store.DEFAULT_STORE_DIR) -> ToolPayload:
    return registry_store.list_documents(store_dir=store_dir)


def delete_document_tool(
    document_id: str,
    store_dir: str = registry_store.DEFAULT_STORE_DIR,
    yes: bool = False,
) -> ToolPayload:
    return registry_store.delete_document(document_id, store_dir=store_dir, yes=yes)


def inspect_store_tool(store_dir: str = registry_store.DEFAULT_STORE_DIR) -> ToolPayload:
    return registry_store.store_health(store_dir=store_dir)


def validate_ir_tool(ir_path: str) -> ToolPayload:
    from documa.core.schema_validation import validate_document_payload

    try:
        payload = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "code": "IR_LOAD_FAILED", "message": str(exc), "ir_path": str(ir_path)}
    result = validate_document_payload(payload)
    result.update({"status": "ok" if result["valid"] else "invalid", "ir_path": str(ir_path)})
    return result


def list_documa_tools() -> list[dict[str, Any]]:
    return documa_tool_schemas()


def _tool_registry() -> dict[str, Callable[..., ToolPayload]]:
    return {
        "documa_parse": parse_document_tool,
        "documa_process": process_document_tool,
        "documa_ingest_mailbox": ingest_mailbox_tool,
        "documa_export": export_document_tool,
        "documa_inspect": inspect_document_tool,
        "documa_view": view_document_tool,
        "documa_list_blocks": list_blocks_tool,
        "documa_inspect_block": inspect_block_tool,
        "documa_read_block": read_block_tool,
        "documa_search_blocks": search_blocks_tool,
        "documa_block_tree": block_tree_tool,
        "documa_block_xref": block_xref_tool,
        "documa_benchmark": benchmark_tool,
        "documa_doctor": doctor_tool,
        "documa_cite_block": cite_block_tool,
        "documa_cite_chunk": cite_chunk_tool,
        "documa_render_citation": render_citation_tool,
        "documa_source_window": source_window_tool,
        "documa_verify_citations": verify_citations_tool,
        "documa_validate_ir": validate_ir_tool,
        "documa_ingest": ingest_document_tool,
        "documa_index_collection": index_collection_tool,
        "documa_search_collection": search_collection_tool,
        "documa_list_documents": list_documents_tool,
        "documa_inspect_store": inspect_store_tool,
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
    safe_payload = to_plain_data(payload)
    text = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
        "isError": is_error,
    }
