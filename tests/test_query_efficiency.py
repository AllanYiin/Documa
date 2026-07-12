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


if __name__ == "__main__":
    unittest.main()
