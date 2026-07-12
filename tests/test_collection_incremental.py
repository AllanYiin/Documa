"""Incremental collection index maintenance: ingest/delete keep search coherent.

Real ingest flows (markdown sources for speed) — no manual index-collection
rebuild after the initial build.
"""

from __future__ import annotations

from unittest import mock

import pytest

from documa.collections import sqlite_index
from documa.collections.sqlite_index import build_collection_index, search_collection, store_collection_health
from documa.interfaces.tools import delete_document_tool, ingest_document_tool


@pytest.fixture()
def store(tmp_path):
    return {"dir": tmp_path / ".documa", "tmp": tmp_path}


def _write_source(store, name: str, text: str):
    source = store["tmp"] / name
    source.write_text(text, encoding="utf-8")
    return source


class TestIncrementalIndex:
    def test_first_ingest_without_index_skips_cheaply(self, store):
        source = _write_source(store, "a.md", "# A\n\nalpha incremental needle.\n")
        result = ingest_document_tool(str(source), store_dir=str(store["dir"]))

        assert result["status"] == "ok"
        assert result["index_update"]["status"] == "skipped"
        assert result["index_update"]["code"] == "COLLECTION_INDEX_NOT_FOUND"

    def test_ingest_after_initial_build_is_searchable_without_rebuild(self, store):
        first = _write_source(store, "a.md", "# A\n\nalpha incremental needle.\n")
        ingest_document_tool(str(first), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        second = _write_source(store, "b.md", "# B\n\nbeta fresh-needle content.\n")
        result = ingest_document_tool(str(second), store_dir=str(store["dir"]))
        searched = search_collection(store["dir"], query="fresh-needle")

        assert result["index_update"]["status"] == "ok"
        assert result["index_update"]["updated"] is True
        assert searched["result_count"] == 1
        assert searched["results"][0]["registry_document_id"] == result["document_id"]
        assert store_collection_health(store["dir"])["status"] == "ok"

    def test_reingesting_identical_content_short_circuits_on_hash(self, store):
        source = _write_source(store, "a.md", "# A\n\nalpha incremental needle.\n")
        ingest_document_tool(str(source), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        again = ingest_document_tool(str(source), store_dir=str(store["dir"]))

        assert again["deduplicated"] is True
        assert again["index_update"]["status"] == "ok"
        assert again["index_update"]["updated"] is False

    def test_superseding_content_replaces_old_rows(self, store):
        source = _write_source(store, "a.md", "# A\n\noldneedle content.\n")
        first = ingest_document_tool(str(source), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        source.write_text("# A\n\nnewneedle content entirely.\n", encoding="utf-8")
        second = ingest_document_tool(str(source), store_dir=str(store["dir"]))

        old_hits = search_collection(store["dir"], query="oldneedle")
        new_hits = search_collection(store["dir"], query="newneedle")

        assert second["document_id"] != first["document_id"]
        assert old_hits["result_count"] == 0
        assert new_hits["result_count"] == 1
        # The superseded document's rows are gone, so health stays coherent.
        health = store_collection_health(store["dir"])
        assert health["status"] == "ok"
        assert health["stale_documents"] == []

    def test_confirmed_delete_removes_rows_without_rebuild(self, store):
        source = _write_source(store, "a.md", "# A\n\ndelete-needle content.\n")
        result = ingest_document_tool(str(source), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        deleted = delete_document_tool(result["document_id"], store_dir=str(store["dir"]), yes=True)
        searched = search_collection(store["dir"], query="delete-needle")

        assert deleted["status"] == "ok"
        assert deleted["index_update"]["status"] == "ok"
        assert searched["result_count"] == 0
        assert store_collection_health(store["dir"])["status"] == "ok"

    def test_version_outdated_index_skips_upsert(self, store):
        source = _write_source(store, "a.md", "# A\n\nversioned needle.\n")
        ingest_document_tool(str(source), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        with mock.patch.object(sqlite_index, "INDEX_VERSION", "999"):
            other = _write_source(store, "b.md", "# B\n\nanother needle.\n")
            result = ingest_document_tool(str(other), store_dir=str(store["dir"]))

        assert result["index_update"]["status"] == "skipped"
        assert result["index_update"]["code"] == "COLLECTION_INDEX_VERSION_OUTDATED"

    def test_update_index_false_leaves_index_untouched(self, store):
        first = _write_source(store, "a.md", "# A\n\nalpha needle.\n")
        ingest_document_tool(str(first), store_dir=str(store["dir"]))
        build_collection_index(store["dir"])

        second = _write_source(store, "b.md", "# B\n\nunindexedneedle content.\n")
        result = ingest_document_tool(str(second), store_dir=str(store["dir"]), update_index=False)
        searched = search_collection(store["dir"], query="unindexedneedle")

        assert "index_update" not in result
        assert searched["result_count"] == 0
