import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TableIR, TextContent, to_plain_data
from documa.exporters import MarkdownExporter, RagJsonExporter
from documa.interfaces import documa_tool_schemas
from documa.pipeline import ChunkingStage, PipelineContext, ProvenanceLinkingStage


def block(block_id, text, block_type=BlockType.PARAGRAPH, page_number=1, metadata=None, order_index=1):
    return BlockIR(
        id=block_id,
        type=block_type,
        page_number=page_number,
        text=TextContent(text),
        bbox=(40, 40 + order_index * 20, 260, 58 + order_index * 20),
        order_index=order_index,
        metadata=metadata or {},
    )


class Stage5ChunkExportTests(unittest.TestCase):
    def test_chunking_stage_preserves_heading_path_and_sources(self):
        heading = block("h1", "第一章", block_type=BlockType.HEADING, metadata={"heading_level": 1}, order_index=1)
        paragraph = block("p1", "這是一段繁體中文內容 with English context.", order_index=2)
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[heading, paragraph])
        doc = DocumentIR(id="d1", source_name="測試.pdf", pages=[page])

        result = ChunkingStage().run(doc, PipelineContext(settings={"max_chars": 80}))

        self.assertTrue(result.changed)
        self.assertEqual(len(doc.chunks), 2)
        self.assertEqual(doc.chunks[1].heading_path, ["第一章"])
        self.assertEqual(doc.chunks[1].source_block_ids, ["p1"])
        self.assertEqual(doc.chunks[1].page_refs, [1])

    def test_chunking_stage_emits_table_chunk_from_table_markdown(self):
        table_block = block("tbl", "A B", block_type=BlockType.TABLE)
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[table_block])
        doc = DocumentIR(
            id="d1",
            source_name="table.pdf",
            pages=[page],
            tables=[TableIR(id="table_tbl", block_id="tbl", markdown="| A | B |\n| --- | --- |\n| 1 | 2 |")],
        )

        ChunkingStage().run(doc)

        self.assertEqual(doc.chunks[0].metadata["chunk_kind"], "table")
        self.assertIn("| A | B |", doc.chunks[0].text.raw_text)

    def test_rag_json_exporter_uses_page_content_and_metadata(self):
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[block("p1", "content")])
        doc = DocumentIR(id="d1", source_name="source.pdf", pages=[page])
        ChunkingStage().run(doc)
        ProvenanceLinkingStage().run(doc)

        payload = RagJsonExporter().export(doc)

        self.assertEqual(payload["chunk_count"], 1)
        self.assertEqual(payload["chunks"][0]["page_content"], "content")
        self.assertEqual(payload["chunks"][0]["metadata"]["source_block_ids"], ["p1"])
        self.assertEqual(payload["chunks"][0]["metadata"]["relation_ids"], [doc.relations[0].id])

    def test_markdown_exporter_outputs_utf8_text(self):
        heading = block("h1", "標題", block_type=BlockType.HEADING, metadata={"heading_level": 1})
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[heading])
        doc = DocumentIR(id="d1", source_name="報告.pdf", pages=[page])

        markdown = MarkdownExporter().export(doc)

        self.assertIn("# 報告.pdf", markdown)
        self.assertIn("# 標題", markdown)

    def test_markdown_exporter_marks_page_furniture_as_comments(self):
        page = PageIR(
            id="p1",
            page_number=1,
            width=400,
            height=500,
            blocks=[
                block("header", "固定抬頭", block_type=BlockType.PAGE_HEADER, order_index=1),
                block("body", "正文內容", order_index=2),
                block("footer", "1", block_type=BlockType.PAGE_FOOTER, order_index=3),
            ],
        )
        doc = DocumentIR(id="d1", source_name="報告.pdf", pages=[page])

        markdown = MarkdownExporter().export(doc)

        self.assertIn("<!-- page-header: 固定抬頭 -->", markdown)
        self.assertIn("正文內容", markdown)
        self.assertIn("<!-- page-footer: 1 -->", markdown)

    def test_cli_export_rag_json_auto_chunks_ir_file(self):
        from io import StringIO
        import sys

        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[block("p1", "可檢索內容")])
        doc = DocumentIR(id="d1", source_name="source.pdf", pages=[page])

        with tempfile.TemporaryDirectory() as tmp:
            ir_path = Path(tmp) / "documa.ir.json"
            out_path = Path(tmp) / "rag.json"
            ir_path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["export", str(ir_path), "--format", "rag-json", "--out", str(out_path)])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            exported = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertEqual(exported["chunks"][0]["page_content"], "可檢索內容")

    def test_tool_schemas_expose_input_and_output_schemas(self):
        schemas = {item["name"]: item for item in documa_tool_schemas()}

        self.assertIn("documa_parse", schemas)
        self.assertIn("documa_export", schemas)
        self.assertIn("documa_inspect", schemas)
        self.assertIn("inputSchema", schemas["documa_export"])
        self.assertIn("outputSchema", schemas["documa_export"])


if __name__ == "__main__":
    unittest.main()
