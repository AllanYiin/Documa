import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent
from documa.pipeline import PipelineContext, run_default_pipeline
from documa.search.sidecar import build_search_sidecar


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

    def _create_borderless_glossary_pdf(self, path: Path) -> None:
        pymupdf = _load_pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=520, height=360)
        page.insert_text((190, 50), "縮 寫 名 詞 表", fontsize=16, fontname="china-t")
        page.insert_text((60, 90), "ABCP", fontsize=10)
        page.insert_text((120, 90), "Asset-backed commercial paper", fontsize=10)
        page.insert_text((330, 90), "資產基礎商業本票", fontsize=10, fontname="china-t")
        page.insert_text((60, 125), "CUSIP", fontsize=10)
        page.insert_text((120, 125), "Committee on Uniform Security", fontsize=10)
        page.insert_text((330, 125), "統一證券識別程序委員", fontsize=10, fontname="china-t")
        page.insert_text((120, 150), "Identification Procedures", fontsize=10)
        page.insert_text((330, 150), "會", fontsize=10, fontname="china-t")
        page.insert_text((60, 185), "VRDN", fontsize=10)
        page.insert_text((120, 185), "Variable Rate Demand Note", fontsize=10)
        page.insert_text((330, 185), "浮動利率債務工具", fontsize=10, fontname="china-t")
        doc.save(path)
        doc.close()

    def _create_numbered_paragraph_pdf(self, path: Path) -> None:
        pymupdf = _load_pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=520, height=360)
        page.insert_text((210, 50), "Section Note", fontsize=14)
        for index, y in enumerate([90, 130, 170, 210], start=1):
            page.insert_text((60, y), f"{index}.", fontsize=10)
            page.insert_text((120, y), "This is a numbered paragraph that happens to wrap", fontsize=10)
            page.insert_text((330, y + 15), "near a third visual column.", fontsize=10)
        doc.save(path)
        doc.close()

    def _create_mixed_image_pdf(self, path: Path) -> None:
        pymupdf = _load_pymupdf()
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=240)
        page.insert_image(pymupdf.Rect(20, 20, 22, 180), stream=PNG_1X1)
        page.insert_image(pymupdf.Rect(80, 80, 140, 140), stream=PNG_1X1)
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

    def test_parse_pdf_suppresses_decorative_image_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "decorative-images.pdf"
            asset_dir = tmp_path / "assets"
            self._create_mixed_image_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(pdf_path, ParseOptions(asset_dir=asset_dir))

            self.assertEqual(len(doc.pages[0].images), 1)
            self.assertEqual(doc.pages[0].metadata["decorative_images_suppressed"], 1)
            self.assertEqual(doc.pages[0].images[0].bbox, (80.0, 80.0, 140.0, 140.0))
            image_assets = list((asset_dir / "images").glob("*.png"))
            self.assertEqual(len(image_assets), 1)

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

    def test_table_ids_stay_unique_when_a_detected_candidate_is_skipped(self):
        page = PageIR(
            id="page_1",
            page_number=1,
            width=300,
            height=220,
            blocks=[
                BlockIR(
                    id="p1_b1",
                    type=BlockType.TEXT,
                    page_number=1,
                    text=TextContent("detected table"),
                    bbox=(20, 20, 120, 80),
                    order_index=1,
                ),
                BlockIR(
                    id="p1_b2",
                    type=BlockType.TEXT,
                    page_number=1,
                    text=TextContent("borderless table"),
                    bbox=(140, 100, 280, 180),
                    order_index=2,
                ),
            ],
        )
        skipped = SimpleNamespace(extract=lambda: [], bbox=(0, 0, 10, 10))
        detected = SimpleNamespace(
            extract=lambda: [["Metric", "Value"], ["Revenue", "42"]],
            bbox=(20, 20, 120, 80),
        )
        borderless = {
            "rows": [["Metric", "Value"], ["Adoption", "9%"]],
            "bbox": (140, 100, 280, 180),
            "strategy": "borderless_column_table",
            "profile": "test_profile",
            "synthetic_header": False,
        }

        with (
            patch(
                "documa.adapters.pymupdf_adapter._find_page_tables", return_value=[skipped, detected]
            ),
            patch(
                "documa.adapters.pymupdf_adapter._borderless_column_tables", return_value=[borderless]
            ),
        ):
            PyMuPDFAdapter()._parse_tables(object(), page)

        table_ids = [block.id for block in page.blocks if block.type == BlockType.TABLE]
        self.assertEqual(table_ids, ["p1_table2", "p1_table3"])
        self.assertEqual(len(table_ids), len(set(table_ids)))

        document = DocumentIR(
            id="doc-unique-tables", source_name="tables.pdf", parser="pymupdf", pages=[page]
        )
        pipeline_run = run_default_pipeline(document, PipelineContext(), include_chunking=True)
        document_block_ids = [block.id for block in pipeline_run.document.document_blocks]
        self.assertEqual(len(document_block_ids), len(set(document_block_ids)))
        with tempfile.TemporaryDirectory() as tmp:
            result = build_search_sidecar(pipeline_run.document, Path(tmp) / "documa.search.idx")
        self.assertEqual(result["block_count"], len(document_block_ids))

    def test_parse_pdf_reconstructs_borderless_glossary_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "borderless-glossary.pdf"
            self._create_borderless_glossary_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(pdf_path, ParseOptions(extract_images=False))

            table_blocks = [
                block
                for block in doc.pages[0].blocks
                if block.type == BlockType.TABLE
                and block.metadata.get("extraction_strategy") == "borderless_column_table"
            ]
            self.assertEqual(len(table_blocks), 1)
            self.assertEqual(table_blocks[0].metadata["borderless_profile"], "glossary_abbreviation_list")
            self.assertFalse(table_blocks[0].metadata["synthetic_header"])
            rows = table_blocks[0].metadata["table_rows"]
            self.assertEqual(rows[0], ["縮寫", "英文", "中文"])
            self.assertIn(["ABCP", "Asset-backed commercial paper", "資產基礎商業本票"], rows)
            self.assertIn(
                [
                    "CUSIP",
                    "Committee on Uniform Security\nIdentification Procedures",
                    "統一證券識別程序委員\n會",
                ],
                rows,
            )
            non_table_text = " ".join(
                block.text.raw_text
                for block in doc.pages[0].blocks
                if block.type != BlockType.TABLE and block.text
            )
            self.assertNotIn("Identification Procedures", non_table_text)

    def test_parse_pdf_does_not_treat_short_numbered_section_as_borderless_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "numbered-paragraphs.pdf"
            self._create_numbered_paragraph_pdf(pdf_path)

            doc = PyMuPDFAdapter().parse(pdf_path, ParseOptions(extract_images=False))

            table_blocks = [
                block
                for block in doc.pages[0].blocks
                if block.type == BlockType.TABLE
                and block.metadata.get("extraction_strategy") == "borderless_column_table"
            ]
            self.assertEqual(table_blocks, [])
