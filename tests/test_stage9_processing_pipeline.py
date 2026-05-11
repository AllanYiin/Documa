import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent
from documa.interfaces import call_documa_tool, list_documa_tools
from documa.pipeline import PipelineContext, run_default_pipeline


def make_doc() -> DocumentIR:
    blocks = [
        BlockIR(
            id="h1",
            type=BlockType.TEXT,
            page_number=1,
            text=TextContent("Stage 9"),
            bbox=(40, 30, 180, 55),
            order_index=1,
            spans=[],
            metadata={"heading_level": 1},
        ),
        BlockIR(
            id="p1",
            type=BlockType.TEXT,
            page_number=1,
            text=TextContent("這是一段可檢索內容。"),
            bbox=(40, 80, 260, 100),
            order_index=2,
        ),
    ]
    return DocumentIR(
        id="d1",
        source_name="stage9.pdf",
        pages=[PageIR(id="page1", page_number=1, width=400, height=500, blocks=blocks)],
    )


class Stage9ProcessingPipelineTests(unittest.TestCase):
    def test_default_pipeline_runs_stage3_to_stage5(self):
        doc = make_doc()

        result = run_default_pipeline(doc, PipelineContext(settings={"max_chars": 200}))

        stage_names = [item.stage_name for item in result.stage_results]
        self.assertIn("reading_order", stage_names)
        self.assertIn("caption_linking", stage_names)
        self.assertIn("rag_chunking", stage_names)
        self.assertIn("provenance_linking", stage_names)
        self.assertGreaterEqual(len(result.document.chunks), 1)
        self.assertGreaterEqual(len(result.document.relations), 1)

    def test_process_tool_schema_is_available(self):
        schemas = {tool["name"]: tool for tool in list_documa_tools()}

        self.assertIn("documa_process", schemas)
        self.assertIn("export_formats", schemas["documa_process"]["inputSchema"]["properties"])

    def test_process_tool_accepts_single_export_format_string(self):
        result = call_documa_tool(
            "documa_process",
            {"source": "missing.pdf", "export_formats": "rag-json"},
        )

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "error")

    def test_process_tool_unknown_file_reports_error(self):
        result = call_documa_tool("documa_process", {"source": "missing.pdf"})

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "error")

    def test_cli_process_writes_ir_and_rag_export(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        from io import StringIO
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "input.pdf"
            out_dir = tmp_path / "out"
            pdf = pymupdf.open()
            page = pdf.new_page(width=240, height=160)
            page.insert_text((20, 50), "Documa process text", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["process", str(pdf_path), "--out", str(out_dir)])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            ir_path = out_dir / "documa.ir.json"
            rag_path = out_dir / "documa.rag.json"
            exported = json.loads(rag_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertTrue(ir_path.exists())
            self.assertTrue(rag_path.exists())
            self.assertGreaterEqual(exported["chunk_count"], 1)


if __name__ == "__main__":
    unittest.main()
