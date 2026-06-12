import json
import sys
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import BlockType
from documa.interfaces import call_documa_tool


class DocumentFormatAdapterTests(unittest.TestCase):
    def test_docx_process_builds_sections_tables_and_searchable_blocks(self):
        try:
            from docx import Document  # type: ignore
        except ImportError:
            self.skipTest("python-docx is required")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            docx_path = tmp_path / "strategy.docx"
            out_dir = tmp_path / "out"

            document = Document()
            document.add_heading("策略總覽", level=1)
            document.add_paragraph("Documa 會保留文件結構、人類閱讀順序與分塊閱讀線索。")
            table = document.add_table(rows=2, cols=2)
            table.cell(0, 0).text = "格式"
            table.cell(0, 1).text = "支援"
            table.cell(1, 0).text = "DOCX"
            table.cell(1, 1).text = "段落與表格"
            document.save(docx_path)

            result = call_documa_tool(
                "documa_process",
                {"source": str(docx_path), "out": str(out_dir), "export_formats": ["block-json"]},
            )

            self.assertFalse(result["isError"])
            payload = result["structuredContent"]
            self.assertEqual(payload["parser"], "docx")
            self.assertEqual(payload["status"], "ok")

            ir = json.loads((out_dir / "documa.ir.json").read_text(encoding="utf-8"))
            block_types = [block["type"] for page in ir["pages"] for block in page["blocks"]]
            self.assertIn(BlockType.HEADING.value, block_types)
            self.assertIn(BlockType.TABLE.value, block_types)

            search = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(out_dir / "documa.ir.json"), "query": "分塊閱讀", "limit": 5},
            )
            self.assertFalse(search["isError"])
            self.assertGreaterEqual(len(search["structuredContent"]["results"]), 1)

    def test_pptx_process_treats_slides_as_pages(self):
        try:
            from pptx import Presentation  # type: ignore
        except ImportError:
            self.skipTest("python-pptx is required")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pptx_path = tmp_path / "briefing.pptx"

            presentation = Presentation()
            slide = presentation.slides.add_slide(presentation.slide_layouts[1])
            slide.shapes.title.text = "產品路線"
            body = slide.shapes.placeholders[1].text_frame
            body.text = "支援 PDF、Markdown、DOCX、PPTX、HTML"
            rows = cols = 2
            table = slide.shapes.add_table(rows, cols, 0, 0, presentation.slide_width // 2, presentation.slide_height // 4).table
            table.cell(0, 0).text = "項目"
            table.cell(0, 1).text = "狀態"
            table.cell(1, 0).text = "PPTX"
            table.cell(1, 1).text = "已解析"
            presentation.save(pptx_path)

            result = call_documa_tool("documa_process", {"source": str(pptx_path)})
            document = result["structuredContent"]["document"]

            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["parser"], "pptx")
            self.assertEqual(result["structuredContent"]["page_count"], 1)
            self.assertTrue(any(block["type"] == "section" and block["title"] == "產品路線" for block in document["document_blocks"]))
            self.assertTrue(any(block["type"] == "table" for block in document["document_blocks"]))

    def test_html_cli_process_preserves_dom_order_headings_tables_and_links(self):
        html = """<!doctype html>
<html lang="zh-Hant">
  <head><title>Documa 文件</title></head>
  <body>
    <section>
      <h1>文件理解</h1>
      <p>HTML 解析應保留 <a href="https://example.com/ref">來源連結</a> 與閱讀順序。</p>
      <table>
        <tr><th>格式</th><th>功能</th></tr>
        <tr><td>HTML</td><td>DOM order</td></tr>
      </table>
    </section>
  </body>
</html>
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            html_path = tmp_path / "sample.html"
            out_dir = tmp_path / "out"
            html_path.write_text(html, encoding="utf-8")

            from io import StringIO

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["process", str(html_path), "--out", str(out_dir), "--export-format", "block-json"])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            self.assertEqual(exit_code, 0)
            self.assertEqual(output["parser"], "html")
            ir = json.loads((out_dir / "documa.ir.json").read_text(encoding="utf-8"))
            blocks = [block for page in ir["pages"] for block in page["blocks"]]
            self.assertEqual([block["type"] for block in blocks], ["heading", "paragraph", "table"])
            self.assertEqual(blocks[1]["metadata"]["links"][0]["href"], "https://example.com/ref")


if __name__ == "__main__":
    unittest.main()
