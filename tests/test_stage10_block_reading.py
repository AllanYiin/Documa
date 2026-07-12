import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    PageIR,
    TableIR,
    TextContent,
    to_plain_data,
)
from documa.exporters import BlockJsonExporter
from documa.interfaces import call_documa_tool
from documa.pipeline import BlockKeywordExtractionStage, BlockTreeBuildingStage, ChunkingStage, PipelineContext


def block(block_id, text, block_type=BlockType.PARAGRAPH, page_number=1, order_index=1, metadata=None):
    return BlockIR(
        id=block_id,
        type=block_type,
        page_number=page_number,
        text=TextContent(text),
        bbox=(40, 40 + order_index * 20, 260, 58 + order_index * 20),
        order_index=order_index,
        metadata=metadata or {},
    )


class Stage10BlockReadingTests(unittest.TestCase):
    def test_block_tree_builds_sections_pages_and_furniture_metadata(self):
        doc = DocumentIR(
            id="d1",
            source_name="block.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("header", "公司文件", BlockType.PAGE_HEADER, order_index=1),
                        block("h1", "第一章", BlockType.HEADING, order_index=2, metadata={"heading_level": 1}),
                        block("p1", "段落內容", order_index=3),
                        block("footer", "1", BlockType.PAGE_FOOTER, order_index=4),
                    ],
                )
            ],
        )

        result = BlockTreeBuildingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertTrue(any(item.title == "第一章" for item in doc.document_blocks))
        self.assertTrue(any(item.source_block_ids == ["p1"] for item in doc.document_blocks))
        root = next(item for item in doc.document_blocks if item.parent_id is None)
        self.assertEqual(len(root.metadata["furniture"]), 2)

    def test_block_tree_adds_printed_page_citation_metadata(self):
        doc = DocumentIR(
            id="d1",
            source_name="block.pdf",
            pages=[
                PageIR(
                    id="p12",
                    page_number=12,
                    width=400,
                    height=500,
                    blocks=[
                        block("body", "第十二張 PDF 頁的正文", page_number=12, order_index=1),
                        block("footer", "6", BlockType.PAGE_FOOTER, page_number=12, order_index=2),
                    ],
                )
            ],
        )

        BlockTreeBuildingStage().run(doc)

        paragraph = next(item for item in doc.document_blocks if item.source_block_ids == ["body"])
        self.assertEqual(paragraph.page_refs, [12])
        self.assertEqual(paragraph.metadata["page_ref_kind"], "physical_page_number_1_based")
        self.assertEqual(paragraph.metadata["printed_page_labels"], ["6"])
        self.assertEqual(paragraph.metadata["citation_label"], "PDF p.12 (printed p.6)")
        self.assertEqual(doc.metadata["page_citations"]["12"]["printed_page_label"], "6")

    def test_block_json_export_omits_page_furniture(self):
        header_text = "固定頁首重複文字"
        footer_text = "固定頁尾頁碼"
        doc = DocumentIR(
            id="d1",
            source_name="block.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("header", header_text, BlockType.PAGE_HEADER, order_index=1),
                        block("p1", f"{header_text} 段落內容", order_index=2),
                        block("footer", footer_text, BlockType.PAGE_FOOTER, order_index=3),
                    ],
                )
            ],
        )
        BlockTreeBuildingStage().run(doc)
        root = next(item for item in doc.document_blocks if item.parent_id is None)
        paragraph = next(item for item in doc.document_blocks if item.source_block_ids == ["p1"])
        root.metadata["search_terms"] = [header_text, "段落內容"]
        paragraph.metadata["keyword_terms"] = [header_text, "段落內容"]
        paragraph.metadata["keyword_stats"] = {"child_support": {header_text: 1, "段落內容": 1}}
        paragraph.metadata["new_word_terms"] = [{"term": header_text, "count": 1}, {"term": "段落內容", "count": 1}]

        payload = BlockJsonExporter().export(doc)
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(len(root.metadata["furniture"]), 2)
        self.assertNotIn("furniture", payload["blocks"][0]["metadata"])
        self.assertNotIn("page_header", serialized)
        self.assertNotIn("page_footer", serialized)
        self.assertNotIn(header_text, serialized)
        self.assertNotIn(footer_text, serialized)
        self.assertIn("段落內容", serialized)
        self.assertEqual(payload["page_ref_kind"], "physical_page_number_1_based")
        self.assertIn("1", payload["page_citations"])

    def test_keyword_stage_aggregates_bottom_up_with_dynamic_thresholds(self):
        doc = DocumentIR(
            id="d1",
            source_name="keyword.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("h1", "人工智慧", BlockType.HEADING, order_index=1, metadata={"heading_level": 1}),
                        block("p1", "生成式人工智慧協助資料科學。", order_index=2),
                        block("p2", "生成式人工智慧模型改善資料科學流程。", order_index=3),
                    ],
                )
            ],
        )
        BlockTreeBuildingStage().run(doc)

        result = BlockKeywordExtractionStage().run(doc)

        self.assertTrue(result.changed)
        section = next(item for item in doc.document_blocks if item.title == "人工智慧")
        self.assertEqual(section.metadata["keyword_thresholds"]["strategy"], "bottom_up_aggregation")
        self.assertGreaterEqual(section.metadata["keyword_stats"]["child_support"].get("生成式人工智慧", 0), 1)
        self.assertIn("生成式人工智慧", section.metadata["search_terms"])

    def test_chunking_uses_intra_block_parent_ids(self):
        doc = DocumentIR(
            id="d1",
            source_name="chunk.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("h1", "第一章", BlockType.HEADING, order_index=1, metadata={"heading_level": 1}),
                        block("p1", "這是一段可檢索內容。", order_index=2),
                    ],
                )
            ],
        )
        BlockTreeBuildingStage().run(doc)

        ChunkingStage().run(doc, PipelineContext(settings={"max_chars": 20}))

        self.assertEqual(len(doc.chunks), 1)
        self.assertIsNotNone(doc.chunks[0].parent_block_id)
        self.assertEqual(doc.chunks[0].metadata["intra_block_view"], True)
        self.assertEqual(doc.chunks[0].source_block_ids, ["p1"])

    def test_table_chunk_repeats_table_context_and_headers(self):
        table_block = block(
            "tbl",
            "A B",
            BlockType.TABLE,
            order_index=2,
            metadata={"caption": "表格說明", "unit": "萬元"},
        )
        doc = DocumentIR(
            id="d1",
            source_name="table.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("h1", "財務資訊", BlockType.HEADING, order_index=1, metadata={"heading_level": 1}),
                        table_block,
                    ],
                )
            ],
            tables=[
                TableIR(
                    id="table_tbl",
                    block_id="tbl",
                    markdown="| 年度 | 營收 |\n| --- | --- |\n| 2025 | 10 |\n| 2026 | 20 |",
                )
            ],
        )
        BlockTreeBuildingStage().run(doc)

        ChunkingStage().run(doc, PipelineContext(settings={"max_chars": 80}))

        self.assertGreaterEqual(len(doc.chunks), 1)
        for chunk in doc.chunks:
            self.assertIn("| 年度 | 營收 |", chunk.text.raw_text)
            self.assertIn("Caption: 表格說明", chunk.text.raw_text)
            self.assertEqual(chunk.metadata["table_context_included"], True)

    def test_block_tools_support_progressive_disclosure(self):
        doc = DocumentIR(
            id="d1",
            source_name="tool.pdf",
            pages=[
                PageIR(
                    id="p12",
                    page_number=12,
                    width=400,
                    height=500,
                    blocks=[
                        block("p1", "工具查詢內容", page_number=12, order_index=1),
                        block("footer", "6", BlockType.PAGE_FOOTER, page_number=12, order_index=2),
                    ],
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            block_id = listed["structuredContent"]["blocks"][-1]["id"]
            read = call_documa_tool("documa_read_block", {"ir_path": str(ir_path), "block_id": block_id})

            self.assertFalse(listed["isError"])
            self.assertFalse(read["isError"])
            self.assertEqual(read["structuredContent"]["content"], "工具查詢內容")
            self.assertEqual(listed["structuredContent"]["blocks"][-1]["page_refs"], [12])
            self.assertEqual(read["structuredContent"]["printed_page_labels"], ["6"])
            self.assertEqual(read["structuredContent"]["citation_label"], "PDF p.12 (printed p.6)")

    def test_search_blocks_uses_body_snippets_without_returning_full_body(self):
        doc = DocumentIR(
            id="d1",
            source_name="tool.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[block("source", "正文裡有 hidden-needle，preview 沒有這個詞。")],
                )
            ],
            document_blocks=[
                DocumentBlockIR(
                    id="db1",
                    type=DocumentBlockType.PARAGRAPH,
                    title="正文區塊",
                    source_block_ids=["source"],
                    page_refs=[1],
                    text_preview="這是短 preview。",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            body_search = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "hidden-needle"})
            metadata_only = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "hidden-needle", "search_body": False},
            )

            self.assertFalse(body_search["isError"])
            self.assertEqual(body_search["structuredContent"]["results"][0]["id"], "db1")
            self.assertEqual(body_search["structuredContent"]["results"][0]["citation_label"], "PDF p.1")
            self.assertEqual(body_search["structuredContent"]["results"][0]["snippets"][0]["field"], "body")
            self.assertIn("hidden-needle", body_search["structuredContent"]["results"][0]["snippets"][0]["snippet"])
            self.assertNotIn("content", body_search["structuredContent"]["results"][0])
            self.assertNotIn("text_preview", body_search["structuredContent"]["results"][0])
            self.assertEqual(body_search["structuredContent"]["results"][0]["block_id"], "db1")
            self.assertEqual(body_search["structuredContent"]["results"][0]["block_type"], "paragraph")
            self.assertIn("heading_path", body_search["structuredContent"]["results"][0])
            self.assertIn("token_estimate", body_search["structuredContent"]["results"][0])
            self.assertIn("recommended_read_chars", body_search["structuredContent"]["results"][0])
            self.assertIn("neighbors", body_search["structuredContent"]["results"][0])
            self.assertIn("flags", body_search["structuredContent"]["results"][0])
            self.assertNotIn("keywords", body_search["structuredContent"]["results"][0])
            self.assertNotIn("searched_fields", body_search["structuredContent"]["results"][0])
            self.assertEqual(metadata_only["structuredContent"]["results"], [])

            debug = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "hidden-needle", "verbosity": "debug"},
            )
            self.assertIn("keywords", debug["structuredContent"]["results"][0])
            self.assertIn("matches", debug["structuredContent"]["results"][0])

    def test_cli_blocks_command_lists_blocks(self):
        from io import StringIO

        doc = DocumentIR(
            id="d1",
            source_name="cli.pdf",
            pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=[block("p1", "CLI 內容")])],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["blocks", str(ir_path)])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertGreaterEqual(output["block_count"], 2)

    def test_search_blocks_ranks_rare_term_evidence_above_common_term_repetition(self):
        doc = DocumentIR(
            id="d1",
            source_name="rank.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        block("s1", "常見詞 常見詞 常見詞 常見詞 常見詞 常見詞", order_index=1),
                        block("s2", "常見詞出現一次，並包含資本緩衝要求的細節。", order_index=2),
                    ],
                )
            ],
            document_blocks=[
                DocumentBlockIR(
                    id="db_common",
                    type=DocumentBlockType.PARAGRAPH,
                    source_block_ids=["s1"],
                    page_refs=[1],
                    order_index=1,
                ),
                DocumentBlockIR(
                    id="db_rare",
                    type=DocumentBlockType.PARAGRAPH,
                    source_block_ids=["s2"],
                    page_refs=[1],
                    order_index=2,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            result = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "常見詞 資本緩衝"},
            )

            content = result["structuredContent"]
            self.assertEqual(content["total_matches"], 2)
            # The block matching the rare term must outrank the block that only
            # repeats the common term, even though the latter has more raw hits.
            self.assertEqual(content["results"][0]["id"], "db_rare")
            self.assertEqual(content["results"][0]["matched_terms_count"], 2)

    def test_search_blocks_demotes_toc_hits_below_body_evidence(self):
        doc = DocumentIR(
            id="d1",
            source_name="toc.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[block("s1", "本節說明資本緩衝要求的計算方式。", order_index=2)],
                )
            ],
            document_blocks=[
                DocumentBlockIR(
                    id="db_toc",
                    type=DocumentBlockType.TOC,
                    title="目錄",
                    text_preview="第三章 資本緩衝要求 ...... 12",
                    page_refs=[1],
                    order_index=1,
                ),
                DocumentBlockIR(
                    id="db_body",
                    type=DocumentBlockType.PARAGRAPH,
                    source_block_ids=["s1"],
                    page_refs=[1],
                    order_index=2,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            result = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "資本緩衝"},
            )

            content = result["structuredContent"]
            ids = [row["id"] for row in content["results"]]
            self.assertIn("db_toc", ids)
            self.assertEqual(content["results"][0]["id"], "db_body")
            toc_row = next(row for row in content["results"] if row["id"] == "db_toc")
            self.assertEqual(toc_row["doc_region"], "toc")
            self.assertLess(toc_row["score"], content["results"][0]["score"])

    def test_mcp_server_exposes_block_reading_tools(self):
        class FakeFastMCP:
            def __init__(self, name, instructions):
                self.name = name
                self.instructions = instructions
                self.tools = {}

            def tool(self):
                def register(func):
                    self.tools[func.__name__] = func
                    return func

                return register

        original = {
            "mcp": sys.modules.get("mcp"),
            "mcp.server": sys.modules.get("mcp.server"),
            "mcp.server.fastmcp": sys.modules.get("mcp.server.fastmcp"),
        }
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")
        fastmcp_module.FastMCP = FakeFastMCP
        sys.modules["mcp"] = types.ModuleType("mcp")
        sys.modules["mcp.server"] = types.ModuleType("mcp.server")
        sys.modules["mcp.server.fastmcp"] = fastmcp_module
        try:
            from documa.interfaces.mcp_server import create_mcp_server

            server = create_mcp_server()
        finally:
            for name, module in original.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertIn("progressive document blocks", server.instructions)
        self.assertIn("documa_list_blocks", server.tools)
        self.assertIn("documa_inspect_block", server.tools)
        self.assertIn("documa_read_block", server.tools)
        self.assertIn("documa_search_blocks", server.tools)


if __name__ == "__main__":
    unittest.main()
