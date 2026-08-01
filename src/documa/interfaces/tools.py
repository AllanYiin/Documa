"""Tool execution layer for CLI, MCP, and direct LLM tool calling."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.collections import registry as registry_store
from documa.collections import sqlite_index as collection_index
from documa.collections.email_collection import MailboxIngestionOptions, ingest_mailbox_collection
from documa.core import snippet_windows
from documa.core.errors import DocumaError
from documa.core.block_scope import descendant_ids, is_ancestor, top_branch_id
from documa.core.ir import DocumentBlockIR, DocumentBlockType, DocumentIR, repair_surrogate_text, to_plain_data
from documa.core.reading import bounded_window, complete_unit_for_kind
from documa.core.query import parse_query
from documa.core.serialization import document_from_plain_data
from documa.exporters import BlockJsonExporter, ExportOptions, JsonExporter, MarkdownExporter, RagJsonExporter
from documa.interfaces import citation, retrieval_policy, search_ranking, token_counting
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
from documa.pipeline.page_refs import PAGE_REF_KIND, ensure_page_citation_map, page_citation_metadata
from documa.viewer import VIEWER_FORMATS, ViewerOptions, build_universal_viewer, render_viewer
from documa.quality import BenchmarkOptions, DoctorOptions, run_doctor, run_fixture_benchmark
from documa.search.sidecar import build_search_sidecar, route_sections, section_sketches, sidecar_path, source_digest


ToolPayload = dict[str, Any]
# Applied to search responses when the caller sets no explicit ceiling and a
# real token counter is configured; oversized responses degrade gracefully
# instead of flooding the calling agent's context.
_AUTO_RESPONSE_TOKEN_BUDGET = 2000
_DEFAULT_SEARCH_FIELDS = ["title", "preview", "search_terms", "keywords", "new_words"]
_DEFAULT_SNIPPET_FIELDS = {"body", "title", "preview"}
_SEARCH_VERBOSITIES = {"compact", "standard", "debug"}
_RESPONSE_PROFILES = {"nav", "evidence", "debug"}
_DATE_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_NUMBER_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|percent|percentage|pp|bps|x|times)?", re.IGNORECASE)
_TREND_PATTERN = re.compile(
    r"\b(increase|decrease|decline|declined|rise|rose|rising|fall|fell|growth|grew|drop|dropped|higher|lower|trend)\b",
    re.IGNORECASE,
)
_DEFINITION_PATTERN = re.compile(r"\b(is defined as|refers to|means|definition|defined as)\b|(?:定義為|是指|意指|稱為)", re.IGNORECASE)
_CAUSE_PATTERN = re.compile(r"\b(because|due to|driven by|caused by|reason|therefore|as a result)\b|(?:因為|由於|原因|導致|因此)", re.IGNORECASE)
_COMPARISON_PATTERN = re.compile(r"\b(compared with|compared to|versus|vs\.?|relative to|more than|less than)\b|(?:比較|相較|高於|低於|差異)", re.IGNORECASE)
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


def _env_cache_size(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


# Parsed-document cache keyed by (resolved path, mtime_ns, size): the key
# self-invalidates whenever the IR file is rewritten. Cached DocumentIR objects
# are shared across tool calls, so tools may only apply idempotent in-place
# enrichment (block tree build, page-citation map) — never destructive mutation.
# Sized for cross-document read_ref chains; override via DOCUMA_DOCUMENT_CACHE_SIZE.
_DOCUMENT_CACHE: OrderedDict[tuple[str, int, int], DocumentIR] = OrderedDict()
_DOCUMENT_CACHE_MAX_ENTRIES = _env_cache_size("DOCUMA_DOCUMENT_CACHE_SIZE", default=16)


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
    temp_path = output_path.with_name(f"{output_path.name}.{uuid.uuid4().hex}.tmp")
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
    pdf_provider: str = "auto",
) -> ToolPayload:
    output_dir = Path(out) if out else None
    asset_dir = output_dir / "assets" if output_dir else None
    languages = [part.strip() for part in lang.split(",") if part.strip()]

    try:
        document = adapter_for_source(source, pdf_provider=pdf_provider).parse(
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
    pdf_provider: str = "auto",
    keyword_provider: str = "lingxi",
) -> ToolPayload:
    output_dir = Path(out) if out else None
    asset_dir = output_dir / "assets" if output_dir else None
    languages = [part.strip() for part in lang.split(",") if part.strip()]
    if isinstance(export_formats, str):
        export_formats = [export_formats]
    export_formats = export_formats or []

    source_is_pdf = Path(source).suffix.casefold() == ".pdf"
    effective_pdf_provider = "pymupdf" if ocr and source_is_pdf and pdf_provider == "auto" else pdf_provider
    try:
        document = adapter_for_source(source, pdf_provider=effective_pdf_provider).parse(
            source,
            ParseOptions(languages=languages or ["auto"], asset_dir=asset_dir),
        )
    except DocumaError as exc:
        return _documa_error_payload(exc)

    if effective_pdf_provider != pdf_provider:
        document.metadata["pdf_provider"] = {
            "requested": pdf_provider,
            "actual": effective_pdf_provider,
            "fallback": True,
            "reason_code": "OCR_REQUIRES_PYMUPDF_RENDERER",
        }

    context = PipelineContext(
        settings={
            "max_chars": max_chars,
            "ocr": ocr,
            "source_path": str(Path(source).resolve()),
            "keyword_provider": keyword_provider,
        }
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
    pipeline_report_path = None
    search_index_path = None
    export_paths: dict[str, str] = {}

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "documa.ir.json"
        write_payload(output_path, payload)
        pipeline_report_path = output_dir / "pipeline_report.json"
        write_payload(pipeline_report_path, pipeline_run.report())
        search_index_path = output_dir / "documa.search.idx"
        build_search_sidecar(pipeline_run.document, search_index_path)
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
        "pipeline": pipeline_run.summary(),
        "pipeline_report_path": str(pipeline_report_path) if pipeline_report_path else None,
        "search_index_path": str(search_index_path) if search_index_path else None,
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
    pdf_provider: str = "auto",
    keyword_provider: str = "lingxi",
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
            document = adapter_for_source(source, pdf_provider=pdf_provider).parse(
                source,
                ParseOptions(languages=languages or ["auto"], asset_dir=asset_dir),
            )
            pipeline_run = run_default_pipeline(
                document,
                PipelineContext(settings={"max_chars": max_chars, "keyword_provider": keyword_provider}),
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
        # The document root title is the source name (often a filesystem
        # path); the envelope already identifies the document, so repeating
        # it at the head of every heading path is waste.
        if current.title and current.type != DocumentBlockType.DOCUMENT:
            path.append(current.title)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return list(reversed(path))


def _block_id_prefix(document: DocumentIR) -> str:
    """Document-scoped prefix every generated document-block id carries.

    Responses declare it once as ``block_id_prefix`` and emit prefix-stripped
    short ids per item; tool inputs accept both forms.
    """
    return f"db_{document.id}_"


def _short_block_id(block_id: str | None, prefix: str) -> str | None:
    if isinstance(block_id, str) and block_id.startswith(prefix) and len(block_id) > len(prefix):
        return block_id[len(prefix) :]
    return block_id


def _emitted_block_id_prefix(document: DocumentIR) -> str | None:
    """The prefix to declare in the envelope, or None when no id carries it.

    IR files written by external fixtures may use bare block ids; declaring an
    unused prefix would only add envelope tokens.
    """
    prefix = _block_id_prefix(document)
    for block in document.document_blocks:
        if block.id.startswith(prefix):
            return prefix
    return None


def _canonical_block_id(block_id: str, prefix: str, by_id: dict[str, Any]) -> str:
    """Accept canonical or short (prefix-stripped) block ids on input."""
    if block_id in by_id:
        return block_id
    prefixed = prefix + block_id
    return prefixed if prefixed in by_id else block_id


def _drop_empty_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Omit null/empty-string/empty-collection values; absent means empty."""
    return {key: value for key, value in item.items() if value is not None and value != "" and value != [] and value != {}}


def _children_by_parent(document: DocumentIR) -> dict[str | None, list[Any]]:
    children: dict[str | None, list[Any]] = {}
    for block in document.document_blocks:
        children.setdefault(block.parent_id, []).append(block)
    for items in children.values():
        items.sort(key=lambda item: (item.order_index is None, item.order_index or 0))
    return children


def _count_tokens(text: str) -> int | None:
    """Real token count via the configured counter, or None when unavailable.

    Token numbers are never approximated from character ratios; without a
    configured counter the fields stay null and budget parameters error.
    """
    counter = token_counting.get_token_counter()
    if counter is None:
        return None
    return counter.count(text)


def _token_counter_unavailable_payload() -> ToolPayload:
    return {"status": "error", "code": "TOKEN_COUNTER_UNAVAILABLE", "message": token_counting.UNAVAILABLE_MESSAGE}


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _result_budget_ref(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the smallest actionable identity when one result exceeds budget."""
    keys = (
        "block_id",
        "registry_document_id",
        "document_id",
        "source_name",
        "kind",
        "score",
        "page",
        "read_chars",
    )
    compact = {key: row[key] for key in keys if row.get(key) is not None}
    if not compact and row.get("top_blocks"):
        top = row["top_blocks"][0]
        compact = {
            "registry_document_id": row.get("registry_document_id"),
            "source_name": row.get("source_name"),
            "top_block_id": top.get("block_id"),
        }
    compact["overflow"] = True
    return compact


def _strip_optional_result_metadata(row: dict[str, Any]) -> None:
    for key in (
        "selection_metadata",
        "text_preview",
        "keywords",
        "source_block_ids",
        "searched_fields",
        "matches",
        "new_words",
        "bbox_refs",
        "dedupe_key",
        "citation_string",
        "physical_page_numbers",
        "printed_page_labels",
        "pdf_page_labels",
        "page_ref_kind",
    ):
        row.pop(key, None)
    snippets = row.get("snippets")
    if isinstance(snippets, list) and len(snippets) > 1:
        row["snippets"] = snippets[:1]


def _prune_next_actions(payload: ToolPayload) -> None:
    next_action = payload.get("recommended_next")
    if not isinstance(next_action, dict):
        return
    allowed: set[tuple[str | None, str]] = set()
    for row in payload.get("results") or []:
        row_doc = row.get("registry_document_id") or row.get("document_id")
        if row.get("block_id"):
            allowed.add((None, row["block_id"]))
            if row_doc:
                allowed.add((row_doc, row["block_id"]))
        for top in row.get("top_blocks") or []:
            if top.get("block_id"):
                allowed.add((row_doc, top["block_id"]))
        for top in row.get("top_refs") or []:
            if top.get("block_id"):
                allowed.add((None, top["block_id"]))
                if row_doc:
                    allowed.add((row_doc, top["block_id"]))
    actions = []
    for action in next_action.get("actions") or []:
        arguments = action.get("arguments") or {}
        candidate = (arguments.get("ir_path"), arguments.get("block_id"))
        if candidate in allowed or (None, candidate[1]) in allowed:
            actions.append(action)
    if actions:
        next_action["actions"] = actions
    else:
        payload.pop("recommended_next", None)


def _count_budgeted_payload(counter: Any, payload: ToolPayload) -> int:
    """Count the deterministic, final structured payload and stabilize its spent field."""
    budget = payload["budget"]
    previous = -1
    for _ in range(8):
        spent = counter.count(_compact_json(payload))
        if spent == previous:
            return spent
        budget["spent_tokens"] = spent
        previous = spent
    return counter.count(_compact_json(payload))


def _apply_response_token_budget(
    payload: ToolPayload,
    max_response_tokens: int,
) -> tuple[ToolPayload, ToolPayload | None]:
    """Enforce a hard ceiling on the complete serialized structured payload."""
    counter = token_counting.get_token_counter()
    if counter is None:
        return {}, _token_counter_unavailable_payload()

    working = copy.deepcopy(payload)
    original_rows = list(working.get("results") or [])
    working["budget"] = {
        "max_response_tokens": max_response_tokens,
        "spent_tokens": 0,
        "dropped_results": 0,
        "token_counter": counter.name,
        "hard_limit": True,
    }

    if _count_budgeted_payload(counter, working) > max_response_tokens:
        for row in working.get("results") or []:
            _strip_optional_result_metadata(row)
    while len(working.get("results") or []) > 1 and _count_budgeted_payload(counter, working) > max_response_tokens:
        working["results"].pop()
    if working.get("results") and _count_budgeted_payload(counter, working) > max_response_tokens:
        working["results"] = [_result_budget_ref(working["results"][0])]

    _prune_next_actions(working)
    for optional_key in (
        "hints",
        "recommended_next",
        "snippet_policy",
        "searched_fields",
        "terms",
        "query",
        "document_id",
        "response_profile",
        "total_matches",
        "offset",
    ):
        if _count_budgeted_payload(counter, working) <= max_response_tokens:
            break
        working.pop(optional_key, None)

    working["budget"]["dropped_results"] = len(original_rows) - len(working.get("results") or [])
    spent = _count_budgeted_payload(counter, working)
    if spent > max_response_tokens:
        minimum = {
            "status": working.get("status", "ok"),
            "results": [],
            "budget": {
                "max_response_tokens": max_response_tokens,
                "spent_tokens": 0,
                "dropped_results": len(original_rows),
                "token_counter": counter.name,
                "hard_limit": True,
                "overflow": True,
            },
        }
        spent = _count_budgeted_payload(counter, minimum)
        if spent > max_response_tokens:
            return {}, {
                "status": "error",
                "code": "RESPONSE_BUDGET_TOO_SMALL",
                "message": f"max_response_tokens={max_response_tokens} cannot fit the minimum response payload.",
            }
        working = minimum
    return working, None


# Hit-centered snippet windows are shared with collection search via the core
# leaf module; these aliases keep existing call sites unchanged.
_find_hits = snippet_windows.find_hits
_make_snippet = snippet_windows.make_snippet


def _search_terms(query: str | None, any_of: list[str] | None = None) -> list[str]:
    return list(parse_query(query, any_of).units)


def _normalized_verbosity(value: str | None) -> str:
    verbosity = (value or "compact").casefold()
    return verbosity if verbosity in _SEARCH_VERBOSITIES else "compact"


def _normalized_response_profile(response_profile: str | None, verbosity: str | None) -> tuple[str, str | None]:
    if verbosity is not None:
        legacy = _normalized_verbosity(verbosity)
        return ("debug" if legacy == "debug" else "evidence"), legacy
    profile = (response_profile or "nav").casefold()
    if profile not in _RESPONSE_PROFILES:
        profile = "nav"
    return profile, None


def _block_doc_region(block: DocumentBlockIR, by_id: dict[str, DocumentBlockIR]) -> str:
    return search_ranking.infer_doc_region(
        _block_path(block.id, by_id),
        block.title,
        block.type.value,
        source_type=str(block.metadata.get("source_block_type") or ""),
        role=str(block.metadata.get("role") or ""),
    )


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
    count_exact_tokens: bool = False,
) -> dict[str, Any]:
    content = document_block_text(document, block)
    selection_text = " ".join(part for part in [block.title or "", block.text_preview or "", content[:1200]] if part)
    char_count = len(content)
    token_estimate = _count_tokens(content) if count_exact_tokens else None
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


def _format_search_row(
    row: dict[str, Any],
    *,
    block: DocumentBlockIR,
    document: DocumentIR,
    page_citations: dict[str, Any],
    profile: str,
    by_id: dict[str, DocumentBlockIR],
    ordered_blocks: list[DocumentBlockIR],
    block_positions: dict[str, int],
    legacy_verbosity: str | None,
    term_count: int,
    id_prefix: str,
) -> dict[str, Any]:
    """Expose only profile-appropriate fields after ranking and pagination."""
    selection = row["selection"]
    citation_meta = page_citation_metadata(block.page_refs, page_citations)
    if profile == "nav":
        snippets = row.get("snippets") or []
        return _drop_empty_fields(
            {
                "block_id": _short_block_id(block.id, id_prefix),
                "kind": block.type.value,
                "path": " > ".join(selection["heading_path"]),
                "page": citation_meta.get("citation_label"),
                "score": row["score"],
                "coverage": f'{row["matched_terms_count"]}/{term_count}',
                "snippet": snippets[0]["snippet"] if snippets else "",
                "read_chars": selection["recommended_read_chars"],
                # Only worth tokens when it changes the next action.
                "needs_next": selection["neighbors"]["needs_next"] or None,
            }
        )

    selection = _selection_metadata(
        block,
        document,
        by_id,
        ordered_blocks,
        block_positions,
        selection["doc_region"],
        count_exact_tokens=True,
    )
    neighbors = dict(selection["neighbors"])
    neighbors["prev"] = _short_block_id(neighbors.get("prev"), id_prefix)
    neighbors["next"] = _short_block_id(neighbors.get("next"), id_prefix)
    output = {
        "block_id": _short_block_id(block.id, id_prefix),
        "block_type": block.type.value,
        "title": block.title,
        "heading_path": selection["heading_path"],
        "score": row["score"],
        "page_refs": block.page_refs,
        "page": citation_meta.get("citation_label"),
        "coverage_score": row.get("coverage_score"),
        "proximity_score": row.get("proximity_score"),
        "intent_fit": row.get("intent_fit"),
        "utility": row.get("utility"),
        "branch_id": row.get("branch_id"),
        "children_count": len(block.child_ids),
        "matched_terms": row["matched_terms"],
        "matched_terms_count": row["matched_terms_count"],
        "snippets": row["snippets"],
        "doc_region": selection["doc_region"],
        "answer_tags": selection["answer_tags"],
        "token_estimate": selection["token_estimate"],
        "recommended_read_chars": selection["recommended_read_chars"],
        "neighbors": neighbors,
        "flags": selection["flags"],
    }
    if profile == "debug" or legacy_verbosity in {"standard", "debug"}:
        output.update(
            {
                "selection_metadata": selection,
                "text_preview": block.text_preview,
                "keywords": block.metadata.get("keyword_terms", [])[:5],
                "source_block_ids": block.source_block_ids,
            }
        )
    if profile == "debug" or legacy_verbosity == "debug":
        output.update(
            {
                "searched_fields": row["selected_fields"],
                "matches": row["matches"],
                "new_words": [entry.get("term") for entry in block.metadata.get("new_word_terms", [])[:8]],
                "dedupe_key": selection["dedupe_key"],
            }
        )
    if legacy_verbosity is not None:
        output.update(
            {
                "id": block.id,
                "type": block.type.value,
                "matched": row["matched_terms"],
                "dedupe_key": selection["dedupe_key"],
            }
        )
    return _drop_empty_fields(output)


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
    prefix = _block_id_prefix(document)
    if parent_id is not None:
        parent_id = _canonical_block_id(parent_id, prefix, _block_index(document))

    blocks = []
    for block in sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0)):
        if depth is not None and block.depth > depth:
            continue
        if parent_id is not None and block.parent_id != parent_id:
            continue
        item = {
            "id": _short_block_id(block.id, prefix),
            "type": block.type.value,
            "title": block.title,
            "parent_id": _short_block_id(block.parent_id, prefix),
            "depth": block.depth,
            "children_count": len(block.child_ids),
            "page_refs": block.page_refs,
            "text_preview": block.text_preview,
            "source_range": block.metadata.get("source_range"),
        }
        if include_metadata_summary:
            item["metadata_summary"] = _drop_empty_fields(
                {
                    "keywords": block.metadata.get("keyword_terms", [])[:8],
                    "new_words": [entry.get("term") for entry in block.metadata.get("new_word_terms", [])[:8]],
                    "confidence": block.confidence.value,
                    "role": block.metadata.get("role"),
                }
            )
        blocks.append(_drop_empty_fields(item))

    total_blocks = len(blocks)
    offset = max(0, int(offset))
    if offset or limit is not None:
        blocks = blocks[offset : offset + limit] if limit is not None else blocks[offset:]
    return {
        "status": "ok",
        "document_id": document.id,
        **({"block_id_prefix": emitted_prefix} if (emitted_prefix := _emitted_block_id_prefix(document)) else {}),
        "page_ref_kind": PAGE_REF_KIND,
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
    prefix = _block_id_prefix(document)
    block = by_id.get(_canonical_block_id(block_id, prefix, by_id))
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}
    block_payload = to_plain_data(block)
    block_payload["id"] = _short_block_id(block.id, prefix)
    block_payload["parent_id"] = _short_block_id(block.parent_id, prefix)
    block_payload["child_ids"] = [_short_block_id(child_id, prefix) for child_id in block.child_ids]
    # Bounded metadata view: keyword statistics and thresholds are pipeline
    # internals; search_terms duplicates title + keywords + new words.
    metadata = block_payload.get("metadata") or {}
    block_payload["metadata"] = _drop_empty_fields(
        {
            "role": metadata.get("role"),
            "source_range": metadata.get("source_range"),
            "source_block_type": metadata.get("source_block_type"),
            "keyword_terms": (metadata.get("keyword_terms") or [])[:8],
            "new_word_terms": [entry.get("term") for entry in (metadata.get("new_word_terms") or [])[:8]],
        }
    )
    return {
        "status": "ok",
        "document_id": document.id,
        **({"block_id_prefix": emitted_prefix} if (emitted_prefix := _emitted_block_id_prefix(document)) else {}),
        "page_ref_kind": PAGE_REF_KIND,
        "block": _drop_empty_fields(block_payload),
        "page": page_citation_metadata(block.page_refs, page_citations).get("citation_label") or None,
        "block_path": _block_path(block.id, by_id),
    }


def read_block_tool(
    ir_path: str,
    block_id: str,
    include_children: bool = False,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    start: int = 0,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_id = _block_index(document)
    prefix = _block_id_prefix(document)
    block = by_id.get(_canonical_block_id(block_id, prefix, by_id))
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
    counter = token_counting.get_token_counter()
    if max_tokens is not None and counter is None:
        return _token_counter_unavailable_payload()
    text = "\n\n".join(document_block_text(document, item) for item in selected if document_block_text(document, item)).strip()
    source_type = str(block.metadata.get("source_block_type") or "").casefold()
    kind = "table" if block.type == DocumentBlockType.TABLE else "paragraph"
    if source_type in {"code", "code_block"}:
        kind = "code"
    elif source_type in {"list", "list_item"}:
        kind = "list"
    content, end, truncated = bounded_window(
        text,
        start=start,
        max_chars=max_chars,
        max_tokens=max_tokens,
        counter=counter,
        kind=kind,
    )
    page_refs = sorted({page for item in selected for page in item.page_refs})
    short_id = _short_block_id(block.id, prefix)
    return _drop_empty_fields(
        {
            "status": "ok",
            "document_id": document.id,
            "block_id_prefix": _emitted_block_id_prefix(document),
            "page_ref_kind": PAGE_REF_KIND,
            "block_id": short_id,
            "block_path": _block_path(block.id, by_id),
            "content": content,
            "truncated": truncated,
            "returned_range": {"start": max(0, min(int(start), len(text))), "end": end},
            "continuation": {"block_id": short_id, "start": end} if truncated else None,
            "complete_unit": complete_unit_for_kind(kind),
            "token_estimate": counter.count(content) if counter else None,
            "token_counter": counter.name if counter else None,
            "page_refs": page_refs,
            "page": page_citation_metadata(page_refs, page_citations).get("citation_label") or None,
        }
    )


def read_blocks_tool(
    ir_path: str,
    block_ids: list[str] | str,
    total_max_tokens: int = 800,
    per_block_max_tokens: int | None = None,
    context_mode: str = "none",
) -> ToolPayload:
    """Read several evidence blocks under one exact shared token budget."""
    if isinstance(block_ids, str):
        block_ids = [part.strip() for part in block_ids.split(",") if part.strip()]
    block_ids = list(dict.fromkeys(block_ids))
    if not block_ids:
        return {"status": "error", "code": "BLOCK_IDS_REQUIRED", "message": "Provide at least one block id."}
    mode = context_mode.casefold()
    if mode not in {"none", "auto", "neighbors", "children"}:
        return {"status": "error", "code": "INVALID_CONTEXT_MODE", "message": f"Unknown context_mode: {context_mode}"}
    counter = token_counting.get_token_counter()
    if counter is None:
        return _token_counter_unavailable_payload()
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    by_id = _block_index(document)
    prefix = _block_id_prefix(document)
    ordered, positions = _ordered_block_positions(document)
    expanded: list[str] = []

    def add(candidate_id: str) -> None:
        if candidate_id in by_id and candidate_id not in expanded:
            expanded.append(candidate_id)

    for block_id in block_ids:
        block = by_id.get(_canonical_block_id(block_id, prefix, by_id))
        if block is None:
            return {"status": "error", "code": "UNKNOWN_BLOCK_ID", "message": f"Unknown block_id: {block_id}"}
        add(block.id)
        use_children = mode == "children" or (mode == "auto" and block.type == DocumentBlockType.SECTION)
        use_neighbors = mode == "neighbors" or (
            mode == "auto" and _block_neighbor_metadata(block, ordered, positions)["needs_next"]
        )
        if use_children:
            pending = list(block.child_ids)
            while pending:
                child_id = pending.pop(0)
                add(child_id)
                child = by_id.get(child_id)
                if child is not None:
                    pending.extend(child.child_ids)
        if use_neighbors:
            neighbors = _block_neighbor_metadata(block, ordered, positions)
            if neighbors["prev"]:
                add(neighbors["prev"])
            if neighbors["next"]:
                add(neighbors["next"])

    budget = max(1, int(total_max_tokens))
    remaining = budget
    items: list[dict[str, Any]] = []
    for block_id in expanded:
        if remaining <= 0:
            break
        block_limit = remaining
        if per_block_max_tokens is not None:
            block_limit = min(block_limit, max(1, int(per_block_max_tokens)))
        item = read_block_tool(ir_path, block_id, max_tokens=block_limit)
        if item.get("status") != "ok":
            return item
        remaining -= int(item.get("token_estimate") or 0)
        # Envelope-level fields; repeating them per contained block is waste.
        for repeated_key in ("status", "document_id", "block_id_prefix", "page_ref_kind", "token_counter"):
            item.pop(repeated_key, None)
        items.append(item)
    return {
        "status": "ok",
        "document_id": document.id,
        **({"block_id_prefix": emitted_prefix} if (emitted_prefix := _emitted_block_id_prefix(document)) else {}),
        "page_ref_kind": PAGE_REF_KIND,
        "results": items,
        "budget": {"total_max_tokens": budget, "spent_tokens": budget - remaining, "remaining_tokens": remaining, "token_counter": counter.name},
        "has_more": len(items) < len(expanded) or any(item.get("truncated") for item in items),
    }


def search_blocks_tool(
    ir_path: str,
    query: str = "",
    limit: int = 10,
    offset: int = 0,
    any_of: list[str] | None = None,
    fields: list[str] | None = None,
    snippet_fields: list[str] | None = None,
    verbosity: str | None = None,
    include_snippets: bool = True,
    max_snippets_per_block: int = 2,
    search_body: bool = True,
    context_chars: int = 24,
    context_words: int = 8,
    max_response_tokens: int | None = None,
    response_profile: str = "nav",
    granularity: str = "auto",
    scope_block_id: str | None = None,
    max_evidence_tokens: int | None = None,
    adaptive_top_k: bool = True,
    max_results_per_branch: int | None = None,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_id = _block_index(document)
    id_prefix = _block_id_prefix(document)
    requested_granularity = granularity.casefold()
    if requested_granularity not in {"auto", "section", "leaf", "mixed"}:
        return {"status": "error", "code": "INVALID_GRANULARITY", "message": f"Unknown granularity: {granularity}"}
    if scope_block_id is not None:
        scope_block_id = _canonical_block_id(scope_block_id, id_prefix, by_id)
        if scope_block_id not in by_id:
            return {"status": "error", "code": "UNKNOWN_SCOPE_BLOCK", "message": f"Unknown scope_block_id: {scope_block_id}"}
    intents = retrieval_policy.query_intents(query)
    effective_granularity = requested_granularity
    if effective_granularity == "auto":
        effective_granularity = "section" if "overview" in intents else "leaf"
    scoped_ids = descendant_ids(scope_block_id, by_id) if scope_block_id else set(by_id)
    evidence_counter = token_counting.get_token_counter()
    if max_evidence_tokens is not None and evidence_counter is None:
        return _token_counter_unavailable_payload()
    structural_types = {DocumentBlockType.DOCUMENT, DocumentBlockType.SECTION}
    def eligible(block: DocumentBlockIR) -> bool:
        structural = block.type in structural_types
        return block.id in scoped_ids and (effective_granularity == "mixed" or structural == (effective_granularity == "section"))


    query_spec = parse_query(query, any_of)
    raw_terms = list(query_spec.units)
    route_rows: list[dict[str, Any]] = []
    route_index_path: str | None = None
    if effective_granularity in {"leaf", "mixed"} and raw_terms:
        try:
            candidate_index = sidecar_path(_resolve_document_path(ir_path))
            route_rows = route_sections(
                candidate_index,
                raw_terms,
                source_generation=source_digest(document),
                scope_block_id=scope_block_id,
            )
            if route_rows:
                route_index_path = str(candidate_index)
                if sum(1 for block in document.document_blocks if block.type == DocumentBlockType.SECTION) > 5:
                    routed_ids = set().union(*(descendant_ids(row["block_id"], by_id) for row in route_rows))
                    scoped_ids.intersection_update(routed_ids)
        except OSError:
            route_rows = []
    profile, legacy_verbosity = _normalized_response_profile(response_profile, verbosity)
    selected_fields = list(fields or _DEFAULT_SEARCH_FIELDS)
    if search_body and "body" not in selected_fields:
        selected_fields.append("body")
    if not search_body:
        selected_fields = [field for field in selected_fields if field != "body"]
    snippet_field_set = set(snippet_fields) if snippet_fields is not None else set(_DEFAULT_SNIPPET_FIELDS)

    if not raw_terms:
        empty: ToolPayload = {
            "status": "ok",
            "document_id": document.id,
            "response_profile": profile,
            "total_matches": 0,
            "offset": 0,
            "results": [],
            "hints": ["Empty query; provide query terms or any_of synonyms."],
        }
        if profile != "nav" or legacy_verbosity is not None:
            empty.update({"query": query, "terms": [], "searched_fields": selected_fields})
        if legacy_verbosity is not None:
            empty["verbosity"] = legacy_verbosity
        if max_response_tokens is not None:
            empty, budget_error = _apply_response_token_budget(empty, max_response_tokens)
            if budget_error is not None:
                return budget_error
        return empty

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
        if not eligible(block):
            continue
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
        searchable_text = " ".join(text for _, text in field_texts).casefold()
        exact_phrase = any(phrase.casefold() in searchable_text for phrase in query_spec.phrases)
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
        selection = _selection_metadata(block, document, by_id, ordered_blocks, block_positions, doc_region)
        ranking_text = " ".join(
            part for part in [block.title or "", block.text_preview or "", document_block_text(document, block)] if part
        )
        coverage = retrieval_policy.coverage_score(matched, raw_terms)
        proximity = retrieval_policy.proximity_score(ranking_text, raw_terms)
        intent_fit = retrieval_policy.intent_fit_score(selection["answer_tags"], intents)
        score *= (0.65 + 0.35 * coverage) * (0.85 + 0.15 * proximity) * (0.75 + 0.25 * intent_fit)
        evidence_tokens = (
            evidence_counter.count(document_block_text(document, block)) if max_evidence_tokens is not None else max(1, len(ranking_text) // 4)
        )
        results.append(
            {
                "block_id": block.id,
                "block_type": block.type.value,
                "score": round(score, 4),
                "matched_terms": matched,
                "matched_terms_count": len(set(matched)),
                "snippets": snippets,
                "recommended_read_chars": selection["recommended_read_chars"],
                "neighbors": selection["neighbors"],
                "selection": selection,
                "matches": matches,
                "selected_fields": selected_fields,
                "coverage_score": round(coverage, 4),
                "proximity_score": round(proximity, 4),
                "intent_fit": round(intent_fit, 4),
                "numeric_values": _NUMBER_PATTERN.findall(ranking_text),
                "branch_id": top_branch_id(block.id, by_id),
                "simhash": retrieval_policy.stable_simhash(ranking_text),
                "evidence_tokens": evidence_tokens,
                "order_index": block.order_index,
                "content_hash": block.content_hash,
            }
        )
    results.sort(key=lambda item: (-item["score"], item.get("order_index") or 0, item["block_id"]))
    filtered_results: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in results:
        content_hash = str(row.get("content_hash") or "")
        if content_hash and content_hash in seen_hashes:
            continue
        if effective_granularity == "mixed" and by_id[row["block_id"]].type in structural_types:
            descendant = next(
                (
                    other
                    for other in results
                    if is_ancestor(row["block_id"], other["block_id"], by_id)
                    and other["score"] >= row["score"] * 0.6
                ),
                None,
            )
            if descendant is not None:
                continue
        near_duplicate = next(
            (
                other
                for other in filtered_results
                if other["branch_id"] == row["branch_id"]
                and retrieval_policy.hamming_distance(int(other["simhash"]), int(row["simhash"])) <= 3
                and other.get("numeric_values") == row.get("numeric_values")
                and abs(int(other["evidence_tokens"]) - int(row["evidence_tokens"])) <= max(2, int(row["evidence_tokens"]) // 10)
            ),
            None,
        )
        if near_duplicate is not None:
            continue
        if max_results_per_branch is not None:
            branch_count = sum(1 for other in filtered_results if other["branch_id"] == row["branch_id"])
            if branch_count >= max(1, int(max_results_per_branch)):
                continue
        filtered_results.append(row)
        if content_hash:
            seen_hashes.add(content_hash)
    total_matches = len(filtered_results)
    if adaptive_top_k or max_evidence_tokens is not None:
        results = retrieval_policy.mmr_select(
            filtered_results, limit=len(filtered_results), max_evidence_tokens=max_evidence_tokens
        )
    else:
        results = filtered_results
    offset = max(0, int(offset))
    internal_page = results[offset : offset + limit]
    page = [
        _format_search_row(
            row,
            block=by_id[row["block_id"]],
            document=document,
            page_citations=page_citations,
            profile=profile,
            legacy_verbosity=legacy_verbosity,
            by_id=by_id,
            ordered_blocks=ordered_blocks,
            block_positions=block_positions,
            term_count=len(raw_terms),
            id_prefix=id_prefix,
        )
        for row in internal_page
    ]
    payload: ToolPayload = {
        "status": "ok",
        "document_id": document.id,
        **({"block_id_prefix": emitted_prefix} if (emitted_prefix := _emitted_block_id_prefix(document)) else {}),
        "response_profile": profile,
        "total_matches": total_matches,
        "offset": offset,
        "results": page,
    }
    if profile == "evidence":
        payload.update(
            {
                "page_ref_kind": PAGE_REF_KIND,
                "retrieval": {
                    "effective_granularity": effective_granularity,
                    "selected_evidence_tokens": sum(int(row.get("evidence_tokens") or 0) for row in internal_page),
                },
            }
        )
    if profile == "debug" or legacy_verbosity is not None:
        # Full diagnostics are a debugging surface, not agent guidance.
        payload.update(
            {
                "page_ref_kind": PAGE_REF_KIND,
                "query": query,
                "terms": raw_terms,
                "searched_fields": selected_fields,
                "retrieval": {
                    "requested_granularity": requested_granularity,
                    "effective_granularity": effective_granularity,
                    "scope_block_id": _short_block_id(scope_block_id, id_prefix),
                    "intents": intents,
                    "adaptive_top_k": adaptive_top_k,
                    "max_evidence_tokens": max_evidence_tokens,
                    "selected_evidence_tokens": sum(int(row.get("evidence_tokens") or 0) for row in internal_page),
                    "route_index_path": route_index_path,
                    "route_block_ids": [_short_block_id(row["block_id"], id_prefix) for row in route_rows],
                    "route_sources": [row.get("route_source", "lexical") for row in route_rows],
                    "route_count": len(route_rows),
                },
                "snippet_policy": {
                    "include_snippets": include_snippets,
                    "max_snippets_per_block": max_snippets,
                    "search_body": search_body,
                    "snippet_fields": sorted(snippet_field_set),
                    "context_chars": context_chars,
                    "context_words": context_words,
                },
            }
        )
    if legacy_verbosity is not None:
        payload["verbosity"] = legacy_verbosity
    hints = search_ranking.search_hints(
        result_count=len(internal_page),
        total_matches=total_matches,
        offset=offset,
        search_body=search_body,
        term_count=len(raw_terms),
        top_matched_terms=internal_page[0]["matched_terms_count"] if internal_page else 0,
    )
    if hints:
        payload["hints"] = hints
    recommended = search_ranking.recommended_next_action(ir_path, internal_page)
    if recommended is not None:
        for action in recommended.get("actions") or []:
            arguments = action.get("arguments") or {}
            for key in ("block_id", "parent_id"):
                if key in arguments:
                    arguments[key] = _short_block_id(arguments[key], id_prefix)
        payload["recommended_next"] = recommended
    auto_budget = max_response_tokens is None and token_counting.get_token_counter() is not None
    effective_response_budget = _AUTO_RESPONSE_TOKEN_BUDGET if auto_budget else max_response_tokens
    if effective_response_budget is not None and effective_response_budget <= 0:
        # Explicit 0 opts out of both the caller ceiling and the auto budget.
        effective_response_budget = None
    if effective_response_budget is not None:
        payload, budget_error = _apply_response_token_budget(payload, effective_response_budget)
        if budget_error is not None:
            return budget_error
        if auto_budget and not (payload.get("budget") or {}).get("dropped_results"):
            # The implicit ceiling did not bite; budget accounting is noise.
            payload.pop("budget", None)
    return payload


def block_tree_tool(
    ir_path: str,
    max_depth: int | None = None,
    max_nodes: int = 500,
    include_citations: bool = False,
    include_sketches: bool = False,
) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_parent = _children_by_parent(document)
    prefix = _block_id_prefix(document)
    sketches: dict[str, dict[str, Any]] = {}
    if include_sketches:
        # Ingest-time section sketches from the search sidecar: a one-glance
        # summary plus read cost per section, with zero block-body reads now.
        try:
            sketches = section_sketches(
                sidecar_path(_resolve_document_path(ir_path)), source_generation=source_digest(document)
            )
        except OSError:
            sketches = {}

    emitted = 0
    truncated = False

    def node(block: Any, depth: int) -> dict[str, Any]:
        nonlocal emitted, truncated
        emitted += 1
        item = {
            "id": _short_block_id(block.id, prefix),
            "type": block.type.value,
            "title": block.title,
            "depth": block.depth,
            "page_refs": block.page_refs,
            "source_range": block.metadata.get("source_range"),
        }
        sketch_row = sketches.get(block.id)
        if sketch_row:
            item["sketch"] = sketch_row["sketch"]
            item["read_cost_chars"] = sketch_row["subtree_cost"] * 4
        if include_citations:
            item["page"] = page_citation_metadata(block.page_refs, page_citations).get("citation_label")
        children = by_parent.get(block.id, [])
        if max_depth is not None and depth >= max_depth:
            # Collapse the subtree to a count so the outline stays cheap;
            # callers descend via parent_id in documa_list_blocks when needed.
            if children:
                item["children_count"] = len(children)
                truncated = True
            return _drop_empty_fields(item)
        rendered_children = []
        for child in children:
            if emitted >= max_nodes:
                truncated = True
                break
            rendered_children.append(node(child, depth + 1))
        if rendered_children:
            item["children"] = rendered_children
        if len(rendered_children) < len(children):
            item["children_count"] = len(children)
        return _drop_empty_fields(item)

    roots = []
    for block in by_parent.get(None, []):
        if emitted >= max_nodes:
            truncated = True
            break
        roots.append(node(block, 0))
    return {
        "status": "ok",
        "document_id": document.id,
        **({"block_id_prefix": emitted_prefix} if (emitted_prefix := _emitted_block_id_prefix(document)) else {}),
        "page_ref_kind": PAGE_REF_KIND,
        "truncated": truncated,
        "tree": roots,
    }


def block_xref_tool(ir_path: str, block_id: str) -> ToolPayload:
    try:
        document = load_document(ir_path)
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    _ensure_document_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_id = _block_index(document)
    prefix = _block_id_prefix(document)
    block = by_id.get(_canonical_block_id(block_id, prefix, by_id))
    if block is None:
        return {"status": "error", "message": f"Unknown block_id: {block_id}"}

    def ref(target_id: str | None) -> dict[str, Any] | None:
        if not target_id:
            return None
        target = by_id.get(target_id)
        if target is None:
            return {"id": _short_block_id(target_id, prefix), "found": False}
        return _drop_empty_fields(
            {
                "id": _short_block_id(target.id, prefix),
                "found": True,
                "type": target.type.value,
                "title": target.title,
                "page_refs": target.page_refs,
                "page": page_citation_metadata(target.page_refs, page_citations).get("citation_label"),
                "source_range": target.metadata.get("source_range"),
            }
        )

    relation_refs = [
        {
            "id": relation.id,
            "type": relation.type.value,
            "from_id": _short_block_id(relation.from_id, prefix),
            "to_id": _short_block_id(relation.to_id, prefix),
            "state": relation.state.value,
        }
        for relation in document.relations
        if relation.from_id == block.id or relation.to_id == block.id
    ]
    return _drop_empty_fields(
        {
            "status": "ok",
            "document_id": document.id,
            "block_id_prefix": _emitted_block_id_prefix(document),
            "page_ref_kind": PAGE_REF_KIND,
            "id": _short_block_id(block.id, prefix),
            "page_refs": block.page_refs,
            "page": page_citation_metadata(block.page_refs, page_citations).get("citation_label"),
            "parent": ref(block.parent_id),
            "children": [ref(child_id) for child_id in block.child_ids],
            "source_block_ids": block.source_block_ids,
            "source_chunk_ids": block.source_chunk_ids,
            "relations": relation_refs,
        }
    )


def benchmark_tool(
    manifest_path: str = "fixtures/pdf/manifest.json",
    fixtures_dir: str = "fixtures/pdf",
    out: str | None = None,
    require_files: bool = False,
    mode: str = "readiness",
    gold_dir: str = "fixtures/pdf/gold",
    quality_threshold: float = 0.85,
    pdf_provider: str = "auto",
    keyword_provider: str = "lingxi",
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
                pdf_provider=pdf_provider,
                keyword_provider=keyword_provider,
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
    update_index: bool = True,
    pdf_provider: str = "auto",
    keyword_provider: str = "lingxi",
) -> ToolPayload:
    result = registry_store.ingest_document(
        source,
        store_dir=store_dir,
        lang=lang,
        max_chars=max_chars,
        ocr=ocr,
        pdf_provider=pdf_provider,
        keyword_provider=keyword_provider,
    )
    # Keep the derived collection index coherent with the registry: upsert the
    # ingested document (a content-hash no-op for dedup hits) and drop rows of
    # entries this ingest superseded. With no index built yet this is one
    # cheap existence check; full rebuild stays the repair path.
    if result.get("status") == "ok" and update_index:
        entry = registry_store.get_entry(store_dir, result["document_id"])
        outcome = collection_index.upsert_document_index(store_dir, entry)
        for old_id in result.get("superseded_document_ids", []):
            collection_index.remove_document_index(store_dir, old_id)
        result["index_update"] = {
            "status": outcome.get("status"),
            "updated": outcome.get("updated"),
            "code": outcome.get("code"),
        }
    return result


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
    response_profile: str = "nav",
    document_ids: list[str] | None = None,
    group_by_document: bool = False,
    max_response_tokens: int | None = None,
) -> ToolPayload:
    payload = collection_index.search_collection(
        store_dir=store_dir,
        query=query,
        collection_id=collection_id,
        limit=limit,
        offset=offset,
        per_document_limit=per_document_limit,
        document_ids=document_ids,
        group_by_document=group_by_document,
    )
    collection_profile = response_profile.casefold()
    if collection_profile not in {"nav", "evidence"}:
        return {"status": "error", "code": "INVALID_RESPONSE_PROFILE", "message": f"Unknown response_profile: {response_profile}"}
    payload["response_profile"] = collection_profile
    if payload.get("status") != "ok":
        return payload
    results = payload.get("results", [])
    if payload.get("match_mode") is None:
        payload["hints"] = ["Empty query; provide search terms (quote adjacent words for phrases)."]
        if max_response_tokens is not None and max_response_tokens > 0:
            payload, budget_error = _apply_response_token_budget(payload, max_response_tokens)
            if budget_error is not None:
                return budget_error
        return payload
    if group_by_document:
        distinct_documents = len(results)
    else:
        distinct_documents = len({row["registry_document_id"] for row in results})
    hints = search_ranking.collection_search_hints(
        result_count=len(results),
        has_more=bool(payload.get("has_more")),
        offset=int(payload.get("offset", 0)),
        match_mode=payload.get("match_mode"),
        distinct_documents=distinct_documents,
        per_document_limit=per_document_limit,
        group_by_document=group_by_document,
    )
    if hints:
        payload["hints"] = hints
    # Build read actions from the full rows before any nav slimming.
    recommended = search_ranking.collection_recommended_next_action(results, grouped=group_by_document)
    if collection_profile == "nav":
        if group_by_document:
            payload["results"] = [
                {
                    "document_id": row["registry_document_id"],
                    "source": row["source_name"],
                    "hits": row["hit_count"],
                    "pages": row["page_count"],
                    "best_score": row["best_score"],
                    "best_snippet": row["top_snippet"],
                    "top_refs": [
                        {"block_id": top["block_id"], "score": top["score"]}
                        for top in row.get("top_blocks", [])
                    ],
                }
                for row in results
            ]
        else:
            payload["results"] = [
                _drop_empty_fields(
                    {
                        "document_id": row["registry_document_id"],
                        "source": row["source_name"],
                        "block_id": row["block_id"],
                        "path": " > ".join(row.get("heading_path") or []),
                        "page_refs": row.get("page_refs"),
                        "score": row["score"],
                        "snippet": row.get("snippet"),
                    }
                )
                for row in results
            ]
    if recommended is not None:
        payload["recommended_next"] = recommended
    auto_budget = max_response_tokens is None and token_counting.get_token_counter() is not None
    effective_response_budget = _AUTO_RESPONSE_TOKEN_BUDGET if auto_budget else max_response_tokens
    if effective_response_budget is not None and effective_response_budget <= 0:
        effective_response_budget = None
    if effective_response_budget is not None:
        payload, budget_error = _apply_response_token_budget(payload, effective_response_budget)
        if budget_error is not None:
            return budget_error
        if auto_budget and not (payload.get("budget") or {}).get("dropped_results"):
            payload.pop("budget", None)
        payload["result_count"] = len(payload.get("results") or [])
        if group_by_document:
            payload["document_count"] = payload["result_count"]
    return payload


def list_documents_tool(store_dir: str = registry_store.DEFAULT_STORE_DIR) -> ToolPayload:
    return registry_store.list_documents(store_dir=store_dir)


def delete_document_tool(
    document_id: str,
    store_dir: str = registry_store.DEFAULT_STORE_DIR,
    yes: bool = False,
    update_index: bool = True,
) -> ToolPayload:
    result = registry_store.delete_document(document_id, store_dir=store_dir, yes=yes)
    # Only after a confirmed delete: drop the document's index rows so search
    # stays coherent without a full rebuild.
    if result.get("status") == "ok" and yes and update_index:
        outcome = collection_index.remove_document_index(store_dir, document_id)
        result["index_update"] = {"status": outcome.get("status"), "code": outcome.get("code")}
    return result


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


def list_documa_tools(profile: str = "admin") -> list[dict[str, Any]]:
    return documa_tool_schemas(profile=profile)


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
        "documa_read_blocks": read_blocks_tool,
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


def call_documa_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    text_mode: str = "compat",
) -> dict[str, Any]:
    """Call a tool with MCP-compatible JSON fallback or an opt-in summary."""

    arguments = arguments or {}
    registry = _tool_registry()
    if name not in registry:
        payload = {"status": "error", "message": f"Unknown Documa tool: {name}"}
        return _tool_result(payload, is_error=True, text_mode=text_mode)

    try:
        payload = registry[name](**arguments)
    except TypeError as exc:
        payload = {"status": "error", "message": str(exc)}
        return _tool_result(payload, is_error=True, text_mode=text_mode)
    except Exception as exc:  # pragma: no cover - last-resort tool boundary guard
        payload = {"status": "error", "message": f"Tool execution failed: {exc}"}
        return _tool_result(payload, is_error=True, text_mode=text_mode)

    return _tool_result(
        payload,
        is_error=payload.get("status") == "error" or "error" in payload,
        text_mode=text_mode,
    )


def _tool_text_summary(payload: ToolPayload) -> str:
    status = str(payload.get("status") or "ok")
    if status == "error":
        detail = payload.get("code") or payload.get("message") or "tool error"
        return f"error: {detail}"
    results = payload.get("results")
    if isinstance(results, list):
        top = results[0].get("block_id") if results and isinstance(results[0], dict) else None
        suffix = f"; top block {top}" if top else ""
        return f"{len(results)} result(s){suffix}."
    return f"Tool completed with status={status}."


def _tool_result(payload: ToolPayload, *, is_error: bool = False, text_mode: str = "compat") -> dict[str, Any]:
    safe_payload = to_plain_data(payload)
    # MCP recommends serialized JSON in TextContent as a compatibility fallback.
    # Hosts that only consume structuredContent may opt into the smaller summary.
    text = _tool_text_summary(safe_payload) if text_mode == "summary" else _compact_json(safe_payload)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": safe_payload,
        "isError": is_error,
    }
