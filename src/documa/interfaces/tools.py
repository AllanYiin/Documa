"""Tool execution layer for CLI, MCP, and direct LLM tool calling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.errors import DocumaError
from documa.core.ir import DocumentIR, to_plain_data
from documa.core.serialization import document_from_plain_data
from documa.exporters import ExportOptions, JsonExporter, MarkdownExporter, RagJsonExporter
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.pipeline import ChunkingStage, PipelineContext, ProvenanceLinkingStage, run_default_pipeline
from documa.quality import BenchmarkOptions, DoctorOptions, run_doctor, run_fixture_benchmark


ToolPayload = dict[str, Any]


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
    if isinstance(payload, str):
        output_path.write_text(payload, encoding="utf-8", newline="\n")
    else:
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


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
        document = PyMuPDFAdapter().parse(
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
        document = PyMuPDFAdapter().parse(
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
        }
        export_names = export_formats or ["rag-json"]
        for export_format in export_names:
            if export_format not in exporters:
                return {"status": "error", "message": f"Unsupported export format: {export_format}"}
            suffix = {"json": "json", "markdown": "md", "rag-json": "rag.json"}[export_format]
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
        ChunkingStage().run(document, context)
        ProvenanceLinkingStage().run(document, context)

    exporters = {
        "json": JsonExporter(),
        "markdown": MarkdownExporter(),
        "rag-json": RagJsonExporter(),
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
