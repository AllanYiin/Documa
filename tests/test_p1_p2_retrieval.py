import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from documa.core.ir import BlockIR, BlockType, DocumentBlockIR, DocumentBlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool
from documa.interfaces import token_counting
from documa.interfaces.mcp_server import create_mcp_server
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.pipeline import BlockKeywordExtractionStage
from documa.search.sidecar import APPLICATION_ID, SEARCH_INDEX_VERSION, build_search_sidecar, route_sections


class _CharCounter:
    name = "chars"

    def count(self, text):
        return len(text)

    def truncate(self, text, max_tokens):
        return text[:max_tokens], len(text) > max_tokens


def _source(block_id, text, order, page=1):
    return BlockIR(
        id=block_id,
        type=BlockType.PARAGRAPH,
        page_number=page,
        text=TextContent(text),
        order_index=order,
    )


def _hierarchical_document(section_count=2):
    sources = []
    blocks = [
        DocumentBlockIR(
            id="root",
            type=DocumentBlockType.DOCUMENT,
            title="報告",
            child_ids=[f"sec-{index}" for index in range(section_count)],
            order_index=0,
        )
    ]
    order = 1
    for index in range(section_count):
        source_id = f"p-{index}"
        text = f"第 {index} 節資料。capital buffer ratio {10 + index}% because policy {index}."
        sources.append(_source(source_id, text, order))
        blocks.extend(
            [
                DocumentBlockIR(
                    id=f"sec-{index}",
                    type=DocumentBlockType.SECTION,
                    title=f"政策章節 {index}",
                    parent_id="root",
                    child_ids=[f"leaf-{index}"],
                    page_refs=[1],
                    order_index=order,
                    metadata={"search_terms": ["capital buffer", f"policy {index}"]},
                ),
                DocumentBlockIR(
                    id=f"leaf-{index}",
                    type=DocumentBlockType.PARAGRAPH,
                    parent_id=f"sec-{index}",
                    source_block_ids=[source_id],
                    page_refs=[1],
                    text_preview=text,
                    content_hash=f"hash-{index}",
                    order_index=order + 1,
                    metadata={"search_terms": ["capital buffer", f"policy {index}"]},
                ),
            ]
        )
        order += 2
    return DocumentIR(
        id="p12",
        source_name="p12.md",
        pages=[PageIR(id="page-1", page_number=1, width=400, height=600, blocks=sources)],
        document_blocks=blocks,
    )


class P1P2RetrievalTests(unittest.TestCase):
    def setUp(self):
        token_counting.set_token_counter(_CharCounter())

    def tearDown(self):
        token_counting.reset_token_counter()

    def _write(self, directory, document):
        path = Path(directory) / "documa.ir.json"
        path.write_text(json.dumps(to_plain_data(document), ensure_ascii=False), encoding="utf-8")
        return path

    def test_boundary_read_returns_continuation_cursor(self):
        document = _hierarchical_document(1)
        document.pages[0].blocks[0].text = TextContent("第一句完整。第二句也完整。第三句結束。")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, document)
            first = call_documa_tool(
                "documa_read_block", {"ir_path": str(path), "block_id": "leaf-0", "max_tokens": 8}
            )["structuredContent"]
            second = call_documa_tool(
                "documa_read_block",
                {"ir_path": str(path), "block_id": "leaf-0", "start": first["continuation"]["start"]},
            )["structuredContent"]

        self.assertEqual(first["content"], "第一句完整。")
        self.assertEqual(first["complete_unit"], "sentence")
        self.assertEqual(first["returned_range"], {"start": 0, "end": 6})
        self.assertTrue(second["content"].startswith("第二句"))

    def test_batch_read_enforces_shared_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, _hierarchical_document(3))
            result = call_documa_tool(
                "documa_read_blocks",
                {
                    "ir_path": str(path),
                    "block_ids": ["leaf-0", "leaf-1", "leaf-2"],
                    "total_max_tokens": 50,
                    "per_block_max_tokens": 24,
                },
            )["structuredContent"]

        self.assertEqual(result["status"], "ok")
        self.assertLessEqual(result["budget"]["spent_tokens"], 50)
        self.assertEqual(result["budget"]["remaining_tokens"], 50 - result["budget"]["spent_tokens"])
        self.assertTrue(result["has_more"])

    def test_scoped_search_reports_numeric_ranking_features(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, _hierarchical_document(2))
            result = call_documa_tool(
                "documa_search_blocks",
                {
                    "ir_path": str(path),
                    "query": "capital buffer ratio 多少",
                    "scope_block_id": "sec-1",
                    "granularity": "leaf",
                    "response_profile": "debug",
                },
            )["structuredContent"]

        self.assertEqual([row["block_id"] for row in result["results"]], ["leaf-1"])
        self.assertEqual(result["retrieval"]["scope_block_id"], "sec-1")
        self.assertIn("numeric", result["retrieval"]["intents"])
        self.assertGreater(result["results"][0]["coverage_score"], 0)
        self.assertGreater(result["results"][0]["intent_fit"], 0)

    def test_tool_profiles_are_cumulative_and_mcp_filters_discovery(self):
        agent = {item["name"] for item in documa_tool_schemas(profile="agent")}
        advanced = {item["name"] for item in documa_tool_schemas(profile="advanced")}
        admin = {item["name"] for item in documa_tool_schemas(profile="admin")}

        self.assertIn("documa_read_blocks", agent)
        self.assertNotIn("documa_doctor", agent)
        self.assertIn("documa_block_tree", advanced)
        self.assertIn("documa_doctor", admin)
        self.assertTrue(agent < advanced < admin)
        mcp_names = {tool.name for tool in asyncio.run(create_mcp_server(profile="agent").list_tools())}
        self.assertIn("documa_read_blocks", mcp_names)
        self.assertNotIn("documa_doctor", mcp_names)

    def test_sidecar_versions_routes_and_deterministic_sketches(self):
        document = _hierarchical_document(6)
        BlockKeywordExtractionStage().run(document)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "documa.search.idx"
            first = build_search_sidecar(document, path)
            first_routes = route_sections(path, ["capital buffer"], source_generation=first["source_digest"])
            second = build_search_sidecar(document, path)
            second_routes = route_sections(path, ["capital buffer"], source_generation=second["source_digest"])
            connection = sqlite3.connect(path)
            try:
                application_id = connection.execute("PRAGMA application_id").fetchone()[0]
                user_version = connection.execute("PRAGMA user_version").fetchone()[0]
                sketch_count = connection.execute("SELECT COUNT(*) FROM routes WHERE sketch <> ''").fetchone()[0]
                features = json.loads(connection.execute("SELECT features_json FROM blocks WHERE block_id = 'leaf-0'").fetchone()[0])
                term_stat_count = connection.execute("SELECT COUNT(*) FROM term_stats").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(application_id, APPLICATION_ID)
        self.assertEqual(user_version, SEARCH_INDEX_VERSION)
        self.assertGreaterEqual(sketch_count, 6)
        self.assertEqual(first_routes, second_routes)
        self.assertTrue(features["document_idf"])
        self.assertTrue(features["cjk_substring_suppression"])
        self.assertLessEqual(len(features["retrieval_terms"]), 6)
        self.assertGreater(term_stat_count, 0)

    def test_search_uses_hierarchical_route_sidecar_for_large_outline(self):
        document = _hierarchical_document(6)
        BlockKeywordExtractionStage().run(document)
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._write(tmp, document)
            build_search_sidecar(document, Path(tmp) / "documa.search.idx")
            result = call_documa_tool(
                "documa_search_blocks",
                {
                    "ir_path": str(ir_path),
                    "query": "policy 5 capital buffer",
                    "granularity": "leaf",
                    "response_profile": "debug",
                },
            )["structuredContent"]

        self.assertTrue(result["retrieval"]["route_index_path"].endswith("documa.search.idx"))
        self.assertIn("sec-5", result["retrieval"]["route_block_ids"])
        self.assertTrue(all(row["branch_id"] in result["retrieval"]["route_block_ids"] for row in result["results"]))


if __name__ == "__main__":
    unittest.main()
