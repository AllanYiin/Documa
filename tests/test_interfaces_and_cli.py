import json
import tempfile
import unittest
from pathlib import Path

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.cli import main
from documa.core.ir import DocumentIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


class DummyAdapter(ParserAdapter):
    name = "dummy"

    def parse(self, source, options=None):
        return DocumentIR(id="d1", source_name=str(source), parser=self.name)


class DummyStage(PipelineStage):
    name = "dummy_stage"

    def run(self, document, context=None):
        return StageResult(document=document, stage_name=self.name, changed=False)


class InterfaceTests(unittest.TestCase):
    def test_parser_adapter_contract(self):
        doc = DummyAdapter().parse("測試.pdf", ParseOptions(languages=["zh-Hant", "en"]))

        self.assertEqual(doc.parser, "dummy")
        self.assertEqual(doc.source_name, "測試.pdf")

    def test_pipeline_stage_contract(self):
        doc = DocumentIR(id="d1", source_name="a.pdf")
        result = DummyStage().run(doc, PipelineContext(project_id="p1"))

        self.assertEqual(result.stage_name, "dummy_stage")
        self.assertIs(result.document, doc)

    def test_cli_version_outputs_json(self):
        from io import StringIO
        import sys

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            exit_code = main(["--version"])
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        self.assertIn("documa_version", json.loads(output))

    def test_cli_parse_writes_utf8_ir_json(self):
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
            out_dir = tmp_path / "輸出"
            pdf = pymupdf.open()
            page = pdf.new_page(width=200, height=120)
            page.insert_text((20, 40), "English text", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["parse", str(pdf_path), "--out", str(out_dir), "--lang", "zh-Hant,en"])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            ir_path = out_dir / "documa.ir.json"
            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertTrue(ir_path.exists())
            ir_text = ir_path.read_text(encoding="utf-8")
            self.assertIn("English text", ir_text)


if __name__ == "__main__":
    unittest.main()
