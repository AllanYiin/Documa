import base64
import tempfile
import unittest
from pathlib import Path

from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.ir import BlockType


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _load_pymupdf():
    try:
        import pymupdf  # type: ignore

        return pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore

        return pymupdf


@unittest.skipUnless(_load_pymupdf(), "PyMuPDF is required")
class PyMuPDFAdapterTests(unittest.TestCase):
    def _create_pdf(self, path: Path) -> None:
        pymupdf = _load_pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=240)
        page.insert_text((40, 50), "繁體中文 English", fontsize=12)
        page.insert_text((40, 80), "简体中文资料", fontsize=12)
        page.insert_image(pymupdf.Rect(40, 100, 80, 140), stream=PNG_1X1)
        doc.set_toc([[1, "章節一", 1]])
        doc.save(path)
        doc.close()

    def _create_table_pdf(self, path: Path) -> None:
        pymupdf = _load_pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=220)
        x0, y0, x1, y1 = 40, 50, 260, 150
        page.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
        page.draw_line((100, y0), (100, y1))
        page.draw_line((x0, 80), (x1, 80))
        page.draw_line((x0, 115), (x1, 115))
        page.insert_text((50, 70), "Rate", fontsize=10)
        page.insert_text((130, 70), "Item", fontsize=10)
        page.insert_text((50, 100), "100%", fontsize=10)
        page.insert_text((130, 100), "Capital", fontsize=10)
        page.insert_text((50, 140), "90%", fontsize=10)
        page.insert_text((130, 140), "Stable deposits", fontsize=10)
        doc.save(path)
        doc.close()

    def test_parse_pdf_extracts_text_preview_and_image_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "測試 文件.pdf"
            asset_dir = tmp_path / "assets"
            self._create_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(
                pdf_path,
                ParseOptions(asset_dir=asset_dir, languages=["auto"], preview_scale=0.5),
            )

            self.assertEqual(doc.parser, "pymupdf")
            self.assertEqual(doc.page_count, 1)
            self.assertEqual(doc.pages[0].metadata["preview_asset_ref"], "previews/page_0001.png")
            self.assertTrue((asset_dir / "previews/page_0001.png").exists())
            self.assertGreaterEqual(len(doc.pages[0].blocks), 1)
            self.assertTrue(any(block.type == BlockType.TEXT for block in doc.pages[0].blocks))
            text = "".join(block.text.raw_text for block in doc.pages[0].blocks if block.text)
            self.assertIn("English", text)
            self.assertIn("測試 文件.pdf", doc.source_name)
            self.assertGreaterEqual(len(doc.pages[0].images), 1)
            image_ref = doc.pages[0].images[0].asset_ref
            self.assertTrue((asset_dir / image_ref).exists())
            self.assertIsNone(doc.pages[0].images[0].metadata.get("pymupdf_object"))

    def test_parse_without_asset_dir_keeps_ir_parser_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "plain.pdf"
            self._create_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(pdf_path, ParseOptions(extract_images=False))

            self.assertEqual(doc.page_count, 1)
            self.assertNotIn("preview_asset_ref", doc.pages[0].metadata)
            self.assertEqual(doc.pages[0].images, [])
            self.assertTrue(all(not hasattr(block, "get_text") for block in doc.pages[0].blocks))

    def test_parse_pdf_emits_table_candidates_without_duplicate_text_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "table.pdf"
            self._create_table_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(pdf_path, ParseOptions(extract_images=False))

            table_blocks = [block for block in doc.pages[0].blocks if block.type == BlockType.TABLE]
            self.assertEqual(len(table_blocks), 1)
            table_block = table_blocks[0]
            self.assertEqual(table_block.metadata["table_rows"][0], ["Rate", "Item"])
            self.assertEqual(table_block.metadata["table_rows"][1], ["100%", "Capital"])
            self.assertTrue(table_block.metadata["source_block_ids"])
            non_table_text = " ".join(
                block.text.raw_text
                for block in doc.pages[0].blocks
                if block.type != BlockType.TABLE and block.text
            )
            self.assertNotIn("Stable deposits", non_table_text)
