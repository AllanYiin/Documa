import json
import sys
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.interfaces import call_documa_tool


class MarkdownQueryModelTests(unittest.TestCase):
    def test_process_markdown_builds_queryable_section_paragraph_blocks(self):
        from io import StringIO

        source = """# 架構總覽

向量資料庫的查詢流程包含 metadata filter、embedding search 與 rerank。

## 成本監控

成本監控應同時追蹤查詢成本、儲存成本與 P95 latency per dollar。
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            md_path = tmp_path / "report.md"
            out_dir = tmp_path / "out"
            md_path.write_text(source, encoding="utf-8")

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["process", str(md_path), "--out", str(out_dir), "--export-format", "block-json"])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            ir_path = out_dir / "documa.ir.json"
            blocks_json = json.loads((out_dir / "documa.blocks.json").read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertTrue(ir_path.exists())
            self.assertGreaterEqual(blocks_json["block_count"], 1)

            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            paragraph_titles = [item["title"] for item in listed["structuredContent"]["blocks"] if item["type"] == "paragraph"]
            self.assertIn("向量資料庫的查詢流程包含 metadata filter、embedding search 與 rerank。", paragraph_titles)

            search = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "P95 latency", "limit": 3, "response_profile": "evidence"},
            )
            self.assertFalse(search["isError"])
            self.assertGreaterEqual(len(search["structuredContent"]["results"]), 1)
            self.assertEqual(search["structuredContent"]["results"][0]["matched_terms"], ["P95", "latency"])
            self.assertTrue(search["structuredContent"]["results"][0]["snippets"])

    def test_markdown_plus_block_header_falls_back_to_first_paragraph_title(self):
        source = """- **#cost-summary** `type:note`
  成本監控應同時追蹤查詢成本、儲存成本與 CPU 效率。

  這一段補充成本模型。
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            md_path = tmp_path / "sample.mdp.md"
            md_path.write_text(source, encoding="utf-8")

            result = call_documa_tool("documa_process", {"source": str(md_path)})
            document = result["structuredContent"]["document"]
            section_titles = [
                block["title"]
                for block in document["document_blocks"]
                if block["type"] == "section"
            ]

            self.assertFalse(result["isError"])
            self.assertIn("成本監控應同時追蹤查詢成本、儲存成本與 CPU 效率。", section_titles)

            ir_path = tmp_path / "documa.ir.json"
            ir_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            tree = call_documa_tool("documa_block_tree", {"ir_path": str(ir_path)})
            xref = call_documa_tool(
                "documa_block_xref",
                {"ir_path": str(ir_path), "block_id": document["document_blocks"][0]["id"]},
            )

            self.assertFalse(tree["isError"])
            self.assertFalse(xref["isError"])
            self.assertEqual(tree["structuredContent"]["tree"][0]["type"], "document")
            self.assertIn("children", xref["structuredContent"])


if __name__ == "__main__":
    unittest.main()
