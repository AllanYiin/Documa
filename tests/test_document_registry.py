"""Document registry tests (Stage 4): ingest, dedup, supersede, delete, rebuild.

Uses markdown sources for speed; the registry is format-agnostic because it
delegates parsing to process_document_tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from documa.collections.registry import (
    delete_document,
    ingest_document,
    list_documents,
    load_registry,
    rebuild_index,
)
from documa.interfaces.tools import cite_block_tool, ingest_document_tool, load_document


@pytest.fixture()
def store(tmp_path) -> dict:
    source = tmp_path / "note.md"
    source.write_text("# Title\n\nA paragraph about layered media.\n", encoding="utf-8")
    return {"dir": tmp_path / ".documa", "source": source, "tmp": tmp_path}


class TestIngest:
    def test_ingest_returns_stable_content_addressed_id(self, store):
        result = ingest_document(str(store["source"]), store_dir=store["dir"])
        assert result["status"] == "ok"
        assert result["document_id"].startswith("doc-")
        assert result["deduplicated"] is False
        assert Path(result["ir_path"]).exists()

        registry = load_registry(store["dir"])
        entry = registry["documents"][0]
        assert entry["ir_path"] == f"documents/{result['document_id']}/documa.ir.json"
        assert "\\" not in entry["ir_path"]
        assert entry["status"] == "active"
        assert (store["dir"] / "documents" / result["document_id"] / "ingest.json").exists()

    def test_ingest_twice_returns_same_id_without_duplicate_entry(self, store):
        first = ingest_document(str(store["source"]), store_dir=store["dir"])
        second = ingest_document(str(store["source"]), store_dir=store["dir"])
        assert second["document_id"] == first["document_id"]
        assert second["deduplicated"] is True
        assert len(load_registry(store["dir"])["documents"]) == 1

    def test_changed_content_supersedes_old_entry(self, store):
        first = ingest_document(str(store["source"]), store_dir=store["dir"])
        store["source"].write_text("# Title\n\nCompletely new content.\n", encoding="utf-8")
        second = ingest_document(str(store["source"]), store_dir=store["dir"])

        assert second["document_id"] != first["document_id"]
        by_id = {e["document_id"]: e for e in load_registry(store["dir"])["documents"]}
        assert by_id[first["document_id"]]["status"] == "superseded"
        assert by_id[first["document_id"]]["superseded_by"] == second["document_id"]
        assert by_id[second["document_id"]]["status"] == "active"

    def test_missing_source_is_reported(self, store):
        result = ingest_document(str(store["tmp"] / "nope.md"), store_dir=store["dir"])
        assert result["code"] == "SOURCE_NOT_FOUND"


class TestListAndDelete:
    def test_list_documents_reports_entries(self, store):
        ingest_document(str(store["source"]), store_dir=store["dir"])
        result = list_documents(store_dir=store["dir"])
        assert result["status"] == "ok"
        assert result["document_count"] == 1

    def test_delete_requires_confirmation(self, store):
        ingested = ingest_document(str(store["source"]), store_dir=store["dir"])
        refused = delete_document(ingested["document_id"], store_dir=store["dir"])
        assert refused["status"] == "confirm_required"
        assert Path(ingested["ir_path"]).exists()

        removed = delete_document(ingested["document_id"], store_dir=store["dir"], yes=True)
        assert removed["status"] == "ok"
        assert not Path(ingested["ir_path"]).exists()
        assert list_documents(store_dir=store["dir"])["document_count"] == 0

    def test_delete_unknown_id_is_reported(self, store):
        result = delete_document("doc-ffffffffffffffff", store_dir=store["dir"], yes=True)
        assert result["code"] == "DOCUMENT_ID_NOT_FOUND"


class TestRecovery:
    def test_rebuild_index_restores_entries_from_documents_dir(self, store):
        ingested = ingest_document(str(store["source"]), store_dir=store["dir"])
        (store["dir"] / "registry.json").unlink()

        result = rebuild_index(store_dir=store["dir"])
        assert result["status"] == "ok"
        assert result["rebuilt"] == 1
        assert load_registry(store["dir"])["documents"][0]["document_id"] == ingested["document_id"]

    def test_corrupted_registry_is_backed_up_and_reported(self, store):
        ingest_document(str(store["source"]), store_dir=store["dir"])
        (store["dir"] / "registry.json").write_text("{not json", encoding="utf-8")

        result = ingest_document(str(store["source"]), store_dir=store["dir"])
        assert result["code"] == "REGISTRY_CORRUPTED"
        assert (store["dir"] / "registry.json.corrupted").exists()

        recovered = rebuild_index(store_dir=store["dir"])
        assert recovered["rebuilt"] == 1


class TestDocumentIdResolution:
    def test_load_document_accepts_document_id_from_default_store(self, store, monkeypatch):
        monkeypatch.chdir(store["tmp"])
        ingested = ingest_document_tool(source="note.md")
        document = load_document(ingested["document_id"])
        assert document.page_count >= 1

    def test_citation_by_id_and_by_path_are_equivalent(self, store, monkeypatch):
        monkeypatch.chdir(store["tmp"])
        ingested = ingest_document_tool(source="note.md")
        block_id = load_document(ingested["document_id"]).document_blocks[0].id

        by_id = cite_block_tool(ir_path=ingested["document_id"], block_id=block_id)
        by_path = cite_block_tool(ir_path=ingested["ir_path"], block_id=block_id)
        by_id.pop("timing_ms"), by_path.pop("timing_ms")
        assert by_id == by_path

    def test_unknown_document_id_reports_not_found(self, store, monkeypatch):
        monkeypatch.chdir(store["tmp"])
        result = cite_block_tool(ir_path="doc-ffffffffffffffff", block_id="x")
        assert result["code"] == "IR_LOAD_FAILED"
        assert "DOCUMENT_ID_NOT_FOUND" in result["message"]

    def test_existing_file_path_wins_over_registry(self, store, monkeypatch):
        monkeypatch.chdir(store["tmp"])
        ingested = ingest_document_tool(source="note.md")
        # A real file whose name collides with an id-looking string.
        decoy = Path("doc-0000000000000000")
        decoy.write_text(json.dumps(json.loads(Path(ingested["ir_path"]).read_text(encoding="utf-8"))), encoding="utf-8")
        document = load_document(str(decoy))
        assert document.source_name.endswith("note.md")
