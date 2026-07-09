"""Mailbox and email-folder ingestion built on top of single-message adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from documa.storage.assets import safe_asset_name


_EMAIL_SUFFIXES = {".eml", ".msg"}


@dataclass(slots=True)
class MailboxIngestionOptions:
    source: Path
    out: Path
    lang: str = "auto"
    max_chars: int = 1200
    export_formats: list[str] | None = None
    recursive: bool = False
    continue_on_error: bool = True
    progress: str = "text"


ProcessMessage = Callable[[str, str, str, int, list[str] | None], dict[str, Any]]


def _iter_email_sources(source: Path, *, recursive: bool) -> list[Path]:
    iterator = source.rglob("*") if recursive else source.iterdir()
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() in _EMAIL_SUFFIXES),
        key=lambda path: str(path.relative_to(source)).lower(),
    )


def _message_output_dir(root: Path, source_root: Path, message_path: Path, index: int) -> Path:
    relative = message_path.relative_to(source_root)
    stem = safe_asset_name("_".join(relative.with_suffix("").parts))
    suffix = safe_asset_name(message_path.suffix.lower().lstrip(".") or "email")
    return root / "messages" / f"{index:04d}_{stem}_{suffix}"


def _progress_event(event: str, **fields: Any) -> dict[str, Any]:
    payload = {"event": event}
    payload.update(fields)
    return payload


def ingest_mailbox_collection(
    options: MailboxIngestionOptions,
    *,
    process_message: ProcessMessage,
) -> dict[str, Any]:
    source = Path(options.source)
    out = Path(options.out)
    export_formats = options.export_formats

    if not source.exists():
        return {
            "status": "error",
            "message": f"Mailbox source does not exist: {source}",
            "code": "MAILBOX_SOURCE_NOT_FOUND",
        }
    if not source.is_dir():
        return {
            "status": "error",
            "message": f"Mailbox source must be a directory: {source}",
            "code": "MAILBOX_SOURCE_NOT_DIRECTORY",
        }

    messages = _iter_email_sources(source, recursive=options.recursive)
    out.mkdir(parents=True, exist_ok=True)
    progress_events: list[dict[str, Any]] = []
    progress_events.append(
        _progress_event(
            "mailbox_discovered",
            source=str(source),
            recursive=options.recursive,
            message_count=len(messages),
        )
    )

    documents: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, message_path in enumerate(messages, start=1):
        relative = message_path.relative_to(source)
        message_out = _message_output_dir(out, source, message_path, index)
        progress_events.append(
            _progress_event(
                "mailbox_message_started",
                index=index,
                source=str(message_path),
                relative_path=str(relative),
                output_dir=str(message_out),
            )
        )
        result = process_message(
            str(message_path),
            str(message_out),
            options.lang,
            options.max_chars,
            export_formats,
        )
        if result.get("status") == "ok":
            documents.append(
                {
                    "index": index,
                    "source": str(message_path),
                    "relative_path": str(relative),
                    "format": message_path.suffix.lower().lstrip("."),
                    "document_id": result.get("document_id"),
                    "parser": result.get("parser"),
                    "output_dir": str(message_out),
                    "ir_path": result.get("output_path"),
                    "export_paths": result.get("export_paths", {}),
                    "page_count": result.get("page_count"),
                    "chunk_count": result.get("chunk_count"),
                    "relation_count": result.get("relation_count"),
                }
            )
            progress_events.append(
                _progress_event(
                    "mailbox_message_completed",
                    index=index,
                    source=str(message_path),
                    document_id=result.get("document_id"),
                )
            )
            continue

        error = {
            "index": index,
            "source": str(message_path),
            "relative_path": str(relative),
            "output_dir": str(message_out),
            "error": result.get("error") or {"message": result.get("message", "Unknown error")},
        }
        errors.append(error)
        progress_events.append(
            _progress_event(
                "mailbox_message_failed",
                index=index,
                source=str(message_path),
                error=error["error"],
            )
        )
        if not options.continue_on_error:
            break

    summary = {
        "message_count": len(messages),
        "succeeded_count": len(documents),
        "failed_count": len(errors),
        "processed_count": len(documents) + len(errors),
    }
    status = "ok" if not errors else ("partial" if documents and options.continue_on_error else "error")
    manifest_path = out / "documa.mailbox.manifest.json"
    manifest = {
        "status": status,
        "collection_type": "email_mailbox",
        "source": str(source),
        "output_dir": str(out),
        "recursive": options.recursive,
        "summary": summary,
        "documents": documents,
        "errors": errors,
    }
    return {
        **manifest,
        "manifest_path": str(manifest_path),
        "progress_events": progress_events if options.progress == "jsonl" else [],
        "manifest": manifest,
    }
