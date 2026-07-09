"""Document registry: content-hash document_id index over a local store directory.

A thin JSON index (``<store_dir>/registry.json``) mapping stable document_ids
to stored IR paths. The ``documents/`` directory is the source of truth: each
ingested document also carries its own ``ingest.json`` entry, so the index can
always be rebuilt from disk (``rebuild_index``). This is deliberately NOT a
database - no query language, no locking; single-writer local CLI semantics.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REGISTRY_VERSION = "1"
DEFAULT_STORE_DIR = ".documa"
DOCUMENT_ID_PREFIX = "doc-"

ToolPayload = dict[str, Any]


def _registry_path(store_dir: Path) -> Path:
    return store_dir / "registry.json"


def _documents_dir(store_dir: Path) -> Path:
    return store_dir / "documents"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_content_hash(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_id_for_hash(content_hash: str) -> str:
    return DOCUMENT_ID_PREFIX + content_hash[:16]


def load_registry(store_dir: str | Path = DEFAULT_STORE_DIR) -> dict[str, Any] | ToolPayload:
    """Load the registry index; on corruption, back it up and report an error."""
    store = Path(store_dir)
    path = _registry_path(store)
    if not path.exists():
        return {"registry_version": REGISTRY_VERSION, "documents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
            raise ValueError("registry root must be an object with a 'documents' list")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        backup = path.with_name("registry.json.corrupted")
        shutil.copy2(path, backup)
        return {
            "status": "error",
            "code": "REGISTRY_CORRUPTED",
            "message": (
                f"Registry index is corrupted ({exc}). A backup was saved to {backup}. "
                "Run: documa ingest --rebuild-index"
            ),
            "backup_path": str(backup),
        }


def _save_registry(store_dir: Path, data: dict[str, Any]) -> None:
    # Local import: tools.py imports this module, so keep the reverse edge lazy.
    from documa.interfaces.tools import write_payload

    _registry_path(store_dir).parent.mkdir(parents=True, exist_ok=True)
    write_payload(_registry_path(store_dir), data)


def get_entry(store_dir: str | Path, document_id: str) -> dict[str, Any] | None:
    registry = load_registry(store_dir)
    if registry.get("code") == "REGISTRY_CORRUPTED":
        return None
    for entry in registry["documents"]:
        if entry.get("document_id") == document_id:
            return entry
    return None


def resolve_ir_path(store_dir: str | Path, document_id: str) -> Path | None:
    entry = get_entry(store_dir, document_id)
    if entry is None:
        return None
    return Path(store_dir) / Path(PurePosixPath(entry["ir_path"]))


def ingest_document(
    source: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    lang: str = "auto",
    max_chars: int = 1200,
    ocr: bool = False,
) -> ToolPayload:
    """Parse + process a document into the store and register a document_id."""
    from documa.interfaces.tools import process_document_tool

    source_path = Path(source)
    if not source_path.is_file():
        return {"status": "error", "code": "SOURCE_NOT_FOUND", "message": f"Source file not found: {source}"}

    store = Path(store_dir)
    registry = load_registry(store)
    if registry.get("code") == "REGISTRY_CORRUPTED":
        return registry

    content_hash = compute_content_hash(source_path)
    document_id = document_id_for_hash(content_hash)

    existing = next(
        (
            entry
            for entry in registry["documents"]
            if entry.get("content_hash") == content_hash and entry.get("status") == "active"
        ),
        None,
    )
    if existing is not None:
        return {
            "status": "ok",
            "document_id": existing["document_id"],
            "deduplicated": True,
            "ir_path": str(store / Path(PurePosixPath(existing["ir_path"]))),
        }

    document_dir = _documents_dir(store) / document_id
    result = process_document_tool(
        source=str(source_path), out=str(document_dir), lang=lang, max_chars=max_chars, ocr=ocr
    )
    if result.get("status") != "ok":
        return result

    ir_rel = PurePosixPath("documents") / document_id / "documa.ir.json"
    ir_payload = json.loads((store / Path(ir_rel)).read_text(encoding="utf-8"))
    entry = {
        "document_id": document_id,
        "source_path": str(source_path.resolve().as_posix()),
        "source_name": source_path.name,
        "content_hash": content_hash,
        "ir_path": str(ir_rel),
        "ir_document_id": ir_payload.get("id"),
        "ir_version": ir_payload.get("ir_version"),
        "producer_version": ir_payload.get("producer_version"),
        "ingested_at": _now_iso(),
        "status": "active",
        "superseded_by": None,
    }

    # Same source path re-ingested with new content supersedes the old entry.
    resolved_source = entry["source_path"]
    for old in registry["documents"]:
        if old.get("source_path") == resolved_source and old.get("status") == "active":
            old["status"] = "superseded"
            old["superseded_by"] = document_id

    registry["documents"].append(entry)
    # Per-document copy makes documents/ the source of truth for rebuilds.
    from documa.interfaces.tools import write_payload

    write_payload(document_dir / "ingest.json", entry)
    _save_registry(store, registry)

    return {
        "status": "ok",
        "document_id": document_id,
        "deduplicated": False,
        "ir_path": str(store / Path(ir_rel)),
        "page_count": result.get("page_count"),
        "chunk_count": result.get("chunk_count"),
        "parser": result.get("parser"),
    }


def list_documents(store_dir: str | Path = DEFAULT_STORE_DIR) -> ToolPayload:
    registry = load_registry(store_dir)
    if registry.get("code") == "REGISTRY_CORRUPTED":
        return registry
    return {
        "status": "ok",
        "registry_version": registry.get("registry_version", REGISTRY_VERSION),
        "document_count": len(registry["documents"]),
        "documents": registry["documents"],
    }


def delete_document(
    document_id: str,
    store_dir: str | Path = DEFAULT_STORE_DIR,
    yes: bool = False,
) -> ToolPayload:
    store = Path(store_dir)
    registry = load_registry(store)
    if registry.get("code") == "REGISTRY_CORRUPTED":
        return registry

    entry = next((item for item in registry["documents"] if item.get("document_id") == document_id), None)
    if entry is None:
        return {
            "status": "error",
            "code": "DOCUMENT_ID_NOT_FOUND",
            "message": f"No document with id {document_id!r} in registry.",
        }

    document_dir = _documents_dir(store) / document_id
    if not yes:
        return {
            "status": "confirm_required",
            "document_id": document_id,
            "message": "Refusing to delete without --yes. Re-run with --yes to remove the paths below.",
            "would_remove": [str(document_dir)],
        }

    if document_dir.exists():
        shutil.rmtree(document_dir)
    registry["documents"] = [item for item in registry["documents"] if item.get("document_id") != document_id]
    _save_registry(store, registry)
    return {"status": "ok", "document_id": document_id, "removed": [str(document_dir)]}


def rebuild_index(store_dir: str | Path = DEFAULT_STORE_DIR) -> ToolPayload:
    """Rebuild registry.json from the per-document ingest.json files."""
    store = Path(store_dir)
    documents_dir = _documents_dir(store)
    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    if documents_dir.exists():
        for meta_path in sorted(documents_dir.glob("*/ingest.json")):
            try:
                entry = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                skipped.append(str(meta_path.parent.name))
                continue
            if isinstance(entry, dict) and entry.get("document_id"):
                entries.append(entry)
            else:
                skipped.append(str(meta_path.parent.name))
    registry = {"registry_version": REGISTRY_VERSION, "documents": entries}
    _save_registry(store, registry)
    return {
        "status": "ok",
        "rebuilt": len(entries),
        "skipped": skipped,
        "registry_path": str(_registry_path(store)),
    }
