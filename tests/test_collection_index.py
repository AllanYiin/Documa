import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from documa.collections import registry as registry_store
from documa.cli import main
from documa.collections.sqlite_index import build_collection_index, search_collection, store_collection_health
from documa.interfaces import call_documa_tool, list_documa_tools
from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    PageIR,
    TextContent,
    to_plain_data,
)


def _write_document(
    store: Path,
    document_id: str,
    ir_document_id: str,
    source_name: str,
    body: str,
    *,
    status: str = "active",
    blocks: list[dict] | None = None,
) -> None:
    """Write a synthetic registry document.

    ``blocks`` overrides the default single-block layout: each entry is a dict
    with optional keys id/title/body/keywords (body required).
    """
    specs = blocks or [
        {
            "id": "target-block",
            "title": f"{source_name} section",
            "body": body,
            "keywords": ["alpha", "needle"],
        }
    ]
    sources = []
    document_blocks = []
    for index, spec in enumerate(specs, start=1):
        source = BlockIR(
            id=f"{document_id}-source-{index}",
            type=BlockType.PARAGRAPH,
            page_number=1,
            text=TextContent(spec["body"]),
            bbox=(40, 40 + index * 40, 220, 70 + index * 40),
            order_index=index,
        )
        sources.append(source)
        document_blocks.append(
            DocumentBlockIR(
                id=spec.get("id") or f"block-{index}",
                type=DocumentBlockType.PARAGRAPH,
                title=spec.get("title"),
                source_block_ids=[source.id],
                page_refs=[1],
                bbox_refs=[(40, 40 + index * 40, 220, 70 + index * 40)],
                text_preview=spec["body"][:80],
                content_hash=f"{document_id}-hash-{index}",
                order_index=index,
                metadata={"keyword_terms": spec.get("keywords", [])},
            )
        )
    doc = DocumentIR(
        id=ir_document_id,
        source_name=source_name,
        pages=[PageIR(id=f"{document_id}-page", page_number=1, width=400, height=500, blocks=sources)],
        document_blocks=document_blocks,
    )

    doc_dir = store / "documents" / document_id
    doc_dir.mkdir(parents=True)
    ir_rel = Path("documents") / document_id / "documa.ir.json"
    ir_path = store / ir_rel
    ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")
    ingest = {
        "document_id": document_id,
        "source_path": str((store / f"{source_name}.md").resolve().as_posix()),
        "source_name": source_name,
        "content_hash": f"{document_id}-content",
        "ir_path": ir_rel.as_posix(),
        "ir_document_id": ir_document_id,
        "status": status,
        "superseded_by": None,
    }
    (doc_dir / "ingest.json").write_text(json.dumps(ingest, ensure_ascii=False), encoding="utf-8")


class CollectionIndexTests(unittest.TestCase):
    def test_builds_default_collection_from_active_registry_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "alpha needle revenue")
            _write_document(store, "doc-active-b", "ir-b", "beta.md", "beta needle risk")
            _write_document(store, "doc-old", "ir-old", "old.md", "old needle text", status="superseded")
            registry_store.rebuild_index(store)

            indexed = build_collection_index(store)
            result = search_collection(store, query="needle", limit=10)

            self.assertEqual(indexed["status"], "ok")
            self.assertEqual(indexed["collection_id"], "default")
            self.assertEqual(indexed["document_count"], 2)
            self.assertEqual(indexed["block_count"], 2)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["result_count"], 2)
            self.assertEqual({item["registry_document_id"] for item in result["results"]}, {"doc-active-a", "doc-active-b"})
            self.assertNotIn("doc-old", {item["registry_document_id"] for item in result["results"]})

    def test_results_are_citation_ready_and_use_registry_document_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "unique collection needle")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            result = search_collection(store, query="unique", limit=5)
            hit = result["results"][0]

            self.assertEqual(hit["registry_document_id"], "doc-active-a")
            self.assertEqual(hit["ir_document_id"], "ir-a")
            self.assertEqual(hit["block_id"], "target-block")
            self.assertEqual(hit["read_ref"], {"ir_path": "doc-active-a", "block_id": "target-block"})
            self.assertEqual(hit["page_refs"], [1])
            self.assertEqual(hit["citation_string"], "[alpha.md, PDF p.1]")
            self.assertIn("unique", hit["snippet"])

    def test_title_hits_outrank_body_hits_across_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(
                store,
                "doc-title-hit",
                "ir-title",
                "title.md",
                "",
                blocks=[{"id": "b1", "title": "solvency buffer overview", "body": "unrelated body text here."}],
            )
            _write_document(
                store,
                "doc-body-hit",
                "ir-body",
                "body.md",
                "",
                blocks=[{"id": "b1", "title": "chapter two", "body": "the solvency margin is described in this body."}],
            )
            registry_store.rebuild_index(store)
            build_collection_index(store)

            result = search_collection(store, query="solvency", limit=10)

            self.assertEqual(result["result_count"], 2)
            # Weighted bm25: a title hit must outrank a body hit.
            self.assertEqual(result["results"][0]["registry_document_id"], "doc-title-hit")

    def test_document_ids_scopes_search_and_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-a", "ir-a", "a.md", "shared needle in document a")
            _write_document(store, "doc-b", "ir-b", "b.md", "shared needle in document b")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            scoped = search_collection(store, query="shared", document_ids=["doc-b"])
            invalid_empty = search_collection(store, query="shared", document_ids=[])
            invalid_blank = search_collection(store, query="shared", document_ids=["doc-a", " "])

            self.assertEqual(scoped["result_count"], 1)
            self.assertEqual(scoped["results"][0]["registry_document_id"], "doc-b")
            self.assertEqual(invalid_empty["code"], "DOCUMENT_IDS_INVALID")
            self.assertEqual(invalid_blank["code"], "DOCUMENT_IDS_INVALID")

    def test_per_document_limit_caps_exactly_with_paging(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            for doc_index in ("a", "b"):
                _write_document(
                    store,
                    f"doc-{doc_index}",
                    f"ir-{doc_index}",
                    f"{doc_index}.md",
                    "",
                    blocks=[
                        {"id": f"b{n}", "body": f"cluster needle occurrence {n} in document {doc_index}"}
                        for n in range(1, 4)
                    ],
                )
            registry_store.rebuild_index(store)
            build_collection_index(store)

            first = search_collection(store, query="cluster", limit=2, per_document_limit=1)
            second = search_collection(store, query="cluster", limit=2, offset=1, per_document_limit=1)

            # Exactly one hit per document; both documents represented.
            self.assertEqual(first["result_count"], 2)
            self.assertEqual(
                {row["registry_document_id"] for row in first["results"]},
                {"doc-a", "doc-b"},
            )
            self.assertFalse(first["has_more"])
            # Paging over the capped set stays exact (no over-fetch heuristics).
            self.assertEqual(second["result_count"], 1)
            self.assertFalse(second["has_more"])

    def test_multi_term_queries_require_all_terms_before_falling_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-both", "ir-both", "both.md", "capital buffer requirement details")
            _write_document(store, "doc-partial", "ir-partial", "partial.md", "requirement only appears here")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            strict = search_collection(store, query="capital requirement", limit=10)
            fallback = search_collection(store, query="capital nonexistent-term", limit=10)

            # Both terms present -> only the document containing both matches.
            self.assertEqual(strict["match_mode"], "all_terms")
            self.assertEqual(strict["result_count"], 1)
            self.assertEqual(strict["results"][0]["registry_document_id"], "doc-both")
            # One term matches nothing -> degrade to any-term and say so.
            self.assertEqual(fallback["match_mode"], "any_term")
            self.assertEqual(fallback["result_count"], 1)
            self.assertEqual(fallback["results"][0]["registry_document_id"], "doc-both")

    def test_quoted_phrases_match_adjacent_terms_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-phrase", "ir-phrase", "phrase.md", "capital buffer requirement")
            _write_document(store, "doc-scattered", "ir-scattered", "scattered.md", "buffer of working capital")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            phrase = search_collection(store, query='"capital buffer"', limit=10)

            self.assertEqual(phrase["match_mode"], "all_terms")
            self.assertEqual(phrase["result_count"], 1)
            self.assertEqual(phrase["results"][0]["registry_document_id"], "doc-phrase")

    def test_cjk_subphrase_queries_match_indexed_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-zh", "ir-zh", "zh.md", "本節說明資本緩衝要求的計算方式。")
            _write_document(store, "doc-zh-other", "ir-zh-other", "zh2.md", "本節討論流動性覆蓋比率。")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            result = search_collection(store, query="資本緩衝", limit=10)

            self.assertEqual(result["match_mode"], "all_terms")
            self.assertEqual(result["result_count"], 1)
            self.assertEqual(result["results"][0]["registry_document_id"], "doc-zh")

    def test_health_reports_outdated_index_version_as_stale(self):
        from documa.collections import sqlite_index

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "version needle")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            with mock.patch.object(sqlite_index, "INDEX_VERSION", "999"):
                health = store_collection_health(store)

            self.assertEqual(health["status"], "warning")
            self.assertEqual(health["code"], "COLLECTION_INDEX_STALE")
            self.assertTrue(health["index_version_outdated"])

    def test_collection_health_reports_missing_and_stale_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            missing = store_collection_health(store)
            self.assertEqual(missing["status"], "warning")
            self.assertEqual(missing["code"], "COLLECTION_INDEX_NOT_FOUND")

            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "alpha needle")
            registry_store.rebuild_index(store)
            build_collection_index(store)
            health = store_collection_health(store)

            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["collection_id"], "default")
            self.assertEqual(health["document_count"], 1)
            self.assertEqual(health["block_count"], 1)


    def test_registry_changes_mark_index_stale_and_filter_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "stale needle")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            registry_path = store / "registry.json"
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["documents"][0]["status"] = "superseded"
            registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

            searched = search_collection(store, query="stale")
            health = store_collection_health(store)

            self.assertEqual(searched["status"], "ok")
            self.assertEqual(searched["result_count"], 0)
            self.assertEqual(health["status"], "warning")
            self.assertEqual(health["code"], "COLLECTION_INDEX_STALE")
            self.assertEqual(health["stale_documents"], ["doc-active-a"])

    def test_tool_and_cli_surfaces_expose_collection_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "tooling collection needle")
            registry_store.rebuild_index(store)

            indexed = call_documa_tool("documa_index_collection", {"store_dir": str(store)})
            searched = call_documa_tool("documa_search_collection", {"store_dir": str(store), "query": "tooling"})
            schemas = {tool["name"] for tool in list_documa_tools()}

            self.assertFalse(indexed["isError"])
            self.assertFalse(searched["isError"])
            self.assertIn("documa_index_collection", schemas)
            self.assertIn("documa_search_collection", schemas)
            self.assertEqual(searched["structuredContent"]["results"][0]["registry_document_id"], "doc-active-a")

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                index_exit_code = main(["index-collection", "--store-dir", str(store)])
                index_output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            self.assertEqual(index_exit_code, 0)
            self.assertEqual(index_output["status"], "ok")
            self.assertEqual(index_output["block_count"], 1)

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                search_exit_code = main(["search-collection", "--store-dir", str(store), "--query", "tooling"])
                search_output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            self.assertEqual(search_exit_code, 0)
            self.assertEqual(search_output["status"], "ok")
            self.assertEqual(search_output["result_count"], 1)

    def test_doctor_reports_collection_health_when_store_dir_is_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            _write_document(store, "doc-active-a", "ir-a", "alpha.md", "doctor collection needle")
            registry_store.rebuild_index(store)
            build_collection_index(store)

            payload = call_documa_tool(
                "documa_doctor",
                {"project_root": ".", "include_benchmark": False, "store_dir": str(store)},
            )["structuredContent"]

            self.assertIn("collection_health", payload)
            self.assertEqual(payload["collection_health"]["status"], "ok")
            self.assertEqual(payload["collection_health"]["block_count"], 1)


if __name__ == "__main__":
    unittest.main()
