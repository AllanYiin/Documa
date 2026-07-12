"""Regression tests for query-efficiency mechanisms: document cache, budgets, response size."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool
from documa.interfaces import tools as tools_module


def _paragraph(block_id: str, text: str, order_index: int = 1) -> BlockIR:
    return BlockIR(
        id=block_id,
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent(text),
        bbox=(40, 40 + order_index * 20, 260, 58 + order_index * 20),
        order_index=order_index,
    )


def _write_ir(directory: str, document: DocumentIR, name: str = "documa.ir.json") -> Path:
    ir_path = Path(directory) / name
    ir_path.write_text(json.dumps(to_plain_data(document), ensure_ascii=False), encoding="utf-8")
    return ir_path


def _fixture_document(text: str = "快取測試內容 cache-probe") -> DocumentIR:
    return DocumentIR(
        id="d1",
        source_name="cache.pdf",
        pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=[_paragraph("b1", text)])],
    )


class DocumentCacheTests(unittest.TestCase):
    def setUp(self):
        tools_module.clear_document_cache()

    def tearDown(self):
        tools_module.clear_document_cache()

    def test_cache_hit_skips_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            with mock.patch.object(
                tools_module,
                "document_from_plain_data",
                wraps=tools_module.document_from_plain_data,
            ) as parse_spy:
                first = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
                second = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})

            self.assertFalse(first["isError"])
            self.assertFalse(second["isError"])
            self.assertEqual(parse_spy.call_count, 1)

    def test_cache_invalidates_when_ir_file_is_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document("第一版內容 first-version"))
            before = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "first-version"})
            self.assertEqual(len(before["structuredContent"]["results"]), 1)

            # Rewrite with different content (and different size, so the
            # mtime/size cache key is guaranteed to change even on coarse clocks).
            _write_ir(tmp, _fixture_document("第二版內容 second-version-longer-body"))
            stale = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "first-version"})
            fresh = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "second-version-longer-body"})

            self.assertEqual(stale["structuredContent"]["results"], [])
            self.assertEqual(len(fresh["structuredContent"]["results"]), 1)

    def test_cached_and_uncached_payloads_are_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            params = {"ir_path": str(ir_path), "query": "cache-probe"}

            warm_first = call_documa_tool("documa_search_blocks", params)
            warm_second = call_documa_tool("documa_search_blocks", params)
            tools_module.clear_document_cache()
            cold = call_documa_tool("documa_search_blocks", params)

            self.assertEqual(warm_first["structuredContent"], warm_second["structuredContent"])
            self.assertEqual(warm_first["structuredContent"], cold["structuredContent"])

    def test_cache_eviction_keeps_recent_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                _write_ir(tmp, _fixture_document(f"doc-{index} 內容"), name=f"doc{index}.ir.json")
                for index in range(tools_module._DOCUMENT_CACHE_MAX_ENTRIES + 2)
            ]
            for path in paths:
                call_documa_tool("documa_inspect", {"ir_path": str(path)})
            self.assertLessEqual(len(tools_module._DOCUMENT_CACHE), tools_module._DOCUMENT_CACHE_MAX_ENTRIES)

    def test_rag_export_chunking_does_not_pollute_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document("導出測試 " + "長內容片段。" * 40))
            # Warm the cache, then export rag-json (which chunks with max_chars).
            call_documa_tool("documa_inspect", {"ir_path": str(ir_path)})
            call_documa_tool("documa_export", {"ir_path": str(ir_path), "format": "rag-json", "max_chars": 60})
            cached = next(iter(tools_module._DOCUMENT_CACHE.values()))
            self.assertEqual(cached.chunks, [])


class TokenBudgetTests(unittest.TestCase):
    def setUp(self):
        tools_module.clear_document_cache()

    def tearDown(self):
        tools_module.clear_document_cache()

    def test_token_estimate_is_cjk_aware(self):
        cjk = "資本緩衝要求說明文件內容" * 10  # 120 CJK chars
        ascii_text = "capital buffer requirement " * 10  # ~270 ASCII chars
        cjk_estimate = tools_module._estimate_tokens(cjk)
        ascii_estimate = tools_module._estimate_tokens(ascii_text)
        # CJK: ~0.8 token/char, far above the chars/4 heuristic.
        self.assertEqual(cjk_estimate, 96)
        self.assertEqual(ascii_estimate, 68)

    def test_read_block_max_tokens_truncates_content(self):
        text = "資本緩衝要求的完整說明。" * 50
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document(text))
            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            block_id = listed["structuredContent"]["blocks"][-1]["id"]

            full = call_documa_tool("documa_read_block", {"ir_path": str(ir_path), "block_id": block_id})
            budgeted = call_documa_tool(
                "documa_read_block",
                {"ir_path": str(ir_path), "block_id": block_id, "max_tokens": 40},
            )

            self.assertFalse(full["structuredContent"]["truncated"])
            self.assertIn("token_estimate", full["structuredContent"])
            self.assertTrue(budgeted["structuredContent"]["truncated"])
            self.assertLessEqual(budgeted["structuredContent"]["token_estimate"], 40)
            self.assertLess(
                len(budgeted["structuredContent"]["content"]),
                len(full["structuredContent"]["content"]),
            )

    def _multi_block_document(self, count: int = 6) -> DocumentIR:
        blocks = [
            _paragraph(f"b{index}", f"預算測試段落 {index} budget-needle 內容說明。", order_index=index)
            for index in range(1, count + 1)
        ]
        return DocumentIR(
            id="d1",
            source_name="budget.pdf",
            pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=blocks)],
        )

    def test_search_blocks_offset_pages_through_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            first = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2},
            )["structuredContent"]
            second = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2, "offset": 2},
            )["structuredContent"]

            self.assertGreaterEqual(first["total_matches"], 4)
            self.assertEqual(len(first["results"]), 2)
            self.assertEqual(second["offset"], 2)
            first_ids = {row["id"] for row in first["results"]}
            second_ids = {row["id"] for row in second["results"]}
            self.assertFalse(first_ids & second_ids)

    def test_search_blocks_max_response_tokens_drops_lowest_ranked_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            unbounded = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 10},
            )["structuredContent"]
            bounded = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 10, "max_response_tokens": 300},
            )["structuredContent"]

            self.assertGreater(len(unbounded["results"]), 1)
            self.assertLess(len(bounded["results"]), len(unbounded["results"]))
            # At least the top-ranked row always survives the budget.
            self.assertGreaterEqual(len(bounded["results"]), 1)
            self.assertEqual(bounded["results"][0]["id"], unbounded["results"][0]["id"])
            budget = bounded["budget"]
            self.assertEqual(budget["max_response_tokens"], 300)
            self.assertEqual(budget["dropped_results"], len(unbounded["results"]) - len(bounded["results"]))

    def test_list_blocks_supports_limit_and_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            page = call_documa_tool(
                "documa_list_blocks",
                {"ir_path": str(ir_path), "limit": 3, "offset": 1},
            )["structuredContent"]

            self.assertEqual(page["block_count"], 3)
            self.assertEqual(page["offset"], 1)
            self.assertGreater(page["total_blocks"], 4)
            self.assertTrue(page["has_more"])

    def test_compact_search_response_size_stays_bounded(self):
        # Regression guardrail: the serialized compact response must stay small
        # enough that a default search costs well under ~1k estimated tokens.
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document(count=10))
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 5},
            )["structuredContent"]

            serialized = json.dumps(payload, ensure_ascii=False)
            per_result = len(serialized) / max(1, len(payload["results"]))
            # Calibrated against the current compact row (~950 chars incl. legacy
            # aliases); a breach means response fat regressed, not a tuning goal.
            self.assertLessEqual(per_result, 1000, f"compact search row grew too large: {per_result:.0f} chars/result")
            self.assertLessEqual(tools_module._estimate_tokens(serialized), 1500)


if __name__ == "__main__":
    unittest.main()
