import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.interfaces import token_counting


class _TestTokenCounter:
    name = "test:chars"

    def count(self, text):
        return len(text)

    def truncate(self, text, max_tokens):
        if len(text) <= max_tokens:
            return text, False
        return text[:max_tokens], True


class Stage11BlockDemoTests(unittest.TestCase):
    def test_cli_block_demo_writes_trace_with_token_usage(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        from io import StringIO
        import sys

        token_counting.set_token_counter(_TestTokenCounter())
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                pdf_path = tmp_path / "input.pdf"
                out_dir = tmp_path / "demo"
                pdf = pymupdf.open()
                page = pdf.new_page(width=300, height=180)
                page.insert_text((20, 40), "Generative AI", fontsize=18)
                page.insert_text((20, 80), "Generative AI helps data science teams organize documents.", fontsize=12)
                pdf.save(pdf_path)
                pdf.close()

                old_stdout = sys.stdout
                sys.stdout = StringIO()
                try:
                    exit_code = main(
                        [
                            "block-demo",
                            str(pdf_path),
                            "--question",
                            "How does generative AI help data science?",
                            "--out",
                            str(out_dir),
                        ]
                    )
                    output = json.loads(sys.stdout.getvalue())
                finally:
                    sys.stdout = old_stdout

                trace_path = out_dir / "documa.block_demo.trace.json"
                trace = json.loads(trace_path.read_text(encoding="utf-8"))

                self.assertEqual(exit_code, 0)
                self.assertEqual(output["status"], "ok")
                self.assertTrue(trace_path.exists())
                self.assertEqual(trace["demo"], "block_based_reading")
                self.assertGreaterEqual(trace["summary"]["document_block_count"], 1)
                self.assertGreater(trace["summary"]["token_usage"]["total_tokens"], 0)
                self.assertEqual(
                    [step["name"] for step in trace["steps"]],
                    [
                        "load_pdf",
                        "build_blocks_and_metadata",
                        "list_all_blocks",
                        "rank_blocks_by_metadata",
                        "read_selected_block_bodies",
                        "synthesize_answer",
                    ],
                )
                self.assertIn("Generative AI", trace["answer"]["answer"])
                self.assertIn("回答：", trace["answer"]["answer"])
                self.assertIn("依據：", trace["answer"]["answer"])
        finally:
            token_counting.reset_token_counter()


if __name__ == "__main__":
    unittest.main()
