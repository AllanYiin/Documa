from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from documa.adapters.base import ParseOptions
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.adapters.registry import RustFirstPdfAdapter, adapter_for_source
from documa.adapters.rust_pdf_adapter import RustPdfAdapter, _load_rust_pdf
from documa.core.errors import DocumaError
from documa.core.ir import BlockType
from documa.interfaces.citation import cite_block
from documa.pipeline.reading_order import ReadingOrderStage


class _FakeRustPdf:
    @staticmethod
    def extract_layout(data, **options):
        assert data == b"fake-pdf"
        assert options["normalize_unicode"] is False
        return {
            "schema_version": 1,
            "parser": {"name": "rust-pdf-parser", "version": "0.2.0", "stage": "stage-11"},
            "coordinate_space": "layout_unrotated_top_left",
            "options_digest": "0" * 64,
            "capabilities": {name: True for name in (
                "source_order", "tagged_order", "inferred_order", "main_flow",
                "text_blocks", "semantic_roles", "tables", "image_placements", "navigation",
            )},
            "text": "TitleBody42Figure 1",
            "warnings": [{"code": "font_fallback_encoding", "message": "fallback"}],
            "named_destinations": [{"name": "intro", "target": {"kind": "go_to", "page_index": 0}}],
            "outlines": [{"id": "o1", "title": "Intro", "depth": 0, "target": {"kind": "go_to", "page_index": 0}}],
            "pages": [{
                "page_index": 0, "page_number": 1,
                "object": {"number": 3, "generation": 0},
                "coordinate_space": "layout_unrotated_top_left",
                "geometry": {
                    "coordinate_space": "layout_unrotated_top_left", "rotation": 90,
                    "layout_bounds": {"x0": 0, "y0": 0, "x1": 200, "y1": 400},
                },
                "semantic_nodes": [
                    {
                        "id": "p0-n0", "kind": "text_block", "role": "heading", "text": "Title",
                        "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 30},
                        "confidence": 0.95, "rule_id": "heading", "provenance": {},
                        "spans": [{
                            "id": "p0-s0", "text": "Title",
                            "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 30},
                            "font_size": 18, "font_resource": "Bold", "confidence": 0.9,
                            "rule_id": "span", "provenance": {},
                        }],
                    },
                    {
                        "id": "p0-n1", "kind": "text_block", "role": "paragraph", "text": "Body",
                        "bbox": {"x0": 10, "y0": 40, "x1": 100, "y1": 60},
                        "confidence": 0.8, "rule_id": "paragraph", "provenance": {}, "spans": [],
                    },
                    {
                        "id": "p0-n2", "kind": "text_block", "role": "table_cell", "text": "42",
                        "bbox": {"x0": 10, "y0": 80, "x1": 100, "y1": 100},
                        "confidence": 0.9, "rule_id": "cell", "provenance": {}, "spans": [],
                    },
                    {
                        "id": "p0-n3", "kind": "text_block", "role": "caption", "text": "Figure 1",
                        "bbox": {"x0": 20, "y0": 160, "x1": 120, "y1": 180},
                        "confidence": 0.9, "rule_id": "caption", "provenance": {}, "spans": [],
                    },
                ],
                "tables": [{
                    "id": "p0-t0", "bbox": {"x0": 10, "y0": 75, "x1": 100, "y1": 105},
                    "rows": 1, "columns": 1, "evidence": "tagged", "source_node_ids": ["p0-n2"],
                    "confidence": 0.95, "rule_id": "table",
                    "cells": [{
                        "id": "c0", "row": 0, "column": 0, "row_span": 1, "column_span": 1,
                        "role": "data", "text": "42", "source_node_ids": ["p0-n2"],
                        "confidence": 0.9, "rule_id": "cell",
                    }],
                }],
                "image_placements": [{
                    "id": "p0-i0", "paint_ordinal": 0, "resource_name": "Im1",
                    "object": {"number": 8, "generation": 0},
                    "bbox": {"x0": 20, "y0": 110, "x1": 120, "y1": 155},
                    "quad": {}, "source_node_ids": ["p0-n3"], "tag": "Figure",
                    "alt_text": "author alt", "confidence": 1.0, "rule_id": "figure", "provenance": {},
                }],
                "links": [{
                    "id": "l0", "bbox": {"x0": 10, "y0": 10, "x1": 80, "y1": 30},
                    "quads": [], "target": {"kind": "uri", "uri": "https://example.invalid"},
                    "confidence": 1.0, "rule_id": "link",
                }],
                "orders": {
                    "source_order": ["p0-n0", "p0-n2", "p0-n1", "p0-n3"],
                    "tagged_order": ["p0-n0", "p0-n1", "p0-n2", "p0-n3"],
                    "inferred_order": ["p0-n0", "p0-n1", "p0-n2", "p0-n3"],
                    "main_flow": ["p0-n0", "p0-n1", "p0-n2", "p0-n3"],
                },
            }],
        }


class RustPdfAdapterTests(unittest.TestCase):
    def test_loader_requires_rust_pdf_parser_0_2_0(self):
        incompatible = SimpleNamespace(version_info=lambda: ("0.1.0", "stage-10"))
        with patch.dict(sys.modules, {"rust_pdf": incompatible}):
            with self.assertRaises(DocumaError) as caught:
                _load_rust_pdf()
        self.assertEqual(caught.exception.detail.code, "RUST_PDF_INCOMPATIBLE_VERSION")
        self.assertEqual(caught.exception.detail.context["actual"], "0.1.0")

    def test_registry_default_stays_pymupdf_and_rust_is_explicit(self):
        self.assertIsInstance(adapter_for_source("sample.pdf"), RustFirstPdfAdapter)
        self.assertIsInstance(adapter_for_source("sample.pdf", pdf_provider="rust"), RustPdfAdapter)
        self.assertIsInstance(adapter_for_source("sample.pdf", pdf_provider="pymupdf"), PyMuPDFAdapter)
        with self.assertRaises(DocumaError) as caught:
            adapter_for_source("sample.pdf", pdf_provider="unknown")
        self.assertEqual(caught.exception.detail.code, "INVALID_PDF_PROVIDER")

    def test_layout_mapping_preserves_coordinates_order_tables_images_and_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(b"fake-pdf")
            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=_FakeRustPdf()):
                document = RustPdfAdapter().parse(source)
        self.assertEqual(document.parser, "rust_pdf")
        self.assertEqual(document.adapter_version, "rust-pdf/0.2.0")
        self.assertEqual(document.metadata["coordinate_space"], "layout_unrotated_top_left")
        self.assertEqual(document.metadata["rust_pdf_metadata_profile"], "compact_trace_v1")
        self.assertEqual(
            document.metadata["rust_pdf_trace_schema"]["fields"],
            ["source_ordinal_start", "source_ordinal_end", "mcids", "text_origins", "rule_id"],
        )
        self.assertEqual(document.metadata["toc"][0]["title"], "Intro")
        page = document.pages[0]
        self.assertEqual((page.width, page.height, page.rotation), (200.0, 400.0, 90))
        self.assertEqual(page.metadata["rust_pdf_orders"]["source_order"][1], "p0-n2")
        self.assertNotIn("rust_pdf_main_flow_ids", page.metadata)
        self.assertNotIn("geometry", page.metadata)
        self.assertEqual(page.blocks[0].source_refs, ["rust-pdf:node:1:p0-n0"])
        self.assertEqual(page.blocks[0].metadata["rust_pdf_trace"], [None, None, [], [], "heading"])
        self.assertNotIn("source_type", page.blocks[0].metadata)
        self.assertNotIn("source_blocks", page.blocks[2].metadata)
        self.assertEqual(cite_block(document, "rust_p0-n0")["status"], "ok")
        self.assertEqual(
            [block.id for block in page.blocks],
            ["rust_p0-n0", "rust_p0-n1", "rust_p0-t0", "rust_p0-n3"],
        )
        self.assertEqual(page.blocks[0].type, BlockType.HEADING)
        self.assertEqual(page.blocks[0].spans[0].style[0].value, "bold")
        table = page.blocks[2]
        self.assertEqual(table.type, BlockType.TABLE)
        self.assertEqual(table.metadata["table_rows"], [["42"]])
        self.assertEqual(table.metadata["source_block_ids"], ["rust_p0-n2"])
        self.assertEqual(page.images[0].caption, "Figure 1")
        self.assertEqual(page.images[0].metadata["alt_text"], "author alt")
        self.assertEqual(page.images[0].metadata["coordinate_space"], "layout_unrotated_top_left")
        self.assertEqual(page.metadata["links"][0]["target"]["kind"], "uri")
        original_ids = [block.id for block in page.blocks]
        result = ReadingOrderStage().run(document)
        self.assertEqual([block.id for block in result.document.pages[0].blocks], original_ids)
        self.assertEqual(
            result.document.pages[0].metadata["reading_order_trace"]["provider"],
            "rust_pdf_inferred_order_v1",
        )

    def test_verbose_metadata_restores_legacy_evidence_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(b"fake-pdf")
            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=_FakeRustPdf()):
                document = RustPdfAdapter().parse(
                    source,
                    ParseOptions(metadata={"rust_pdf_include_verbose_metadata": True}),
                )
        page = document.pages[0]
        block = page.blocks[0]
        span = block.spans[0]
        self.assertEqual(document.metadata["rust_pdf_metadata_profile"], "verbose_v1")
        self.assertNotIn("rust_pdf_trace_schema", document.metadata)
        self.assertEqual(page.metadata["rust_pdf_main_flow_ids"], page.metadata["rust_pdf_orders"]["main_flow"])
        self.assertEqual(page.metadata["geometry"]["rotation"], 90)
        self.assertEqual(block.metadata["source_type"], "rust_pdf_layout_node")
        self.assertEqual(block.metadata["rust_pdf_node_id"], "p0-n0")
        self.assertEqual(block.metadata["provenance"], {})
        self.assertEqual(span.metadata["source"], "rust_pdf_layout_span")
        self.assertEqual(span.metadata["provenance"], {})
        self.assertEqual(cite_block(document, block.id)["status"], "ok")

    def test_streaming_api_is_preferred_and_short_stream_is_rejected(self):
        class Stream:
            def __init__(self, declared_pages=1):
                layout = _FakeRustPdf.extract_layout(b"fake-pdf", normalize_unicode=False)
                pages = layout.pop("pages")
                layout.pop("text")
                layout["page_count"] = declared_pages
                self.metadata = layout
                self._pages = iter(pages)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._pages)

        class StreamingRust:
            @staticmethod
            def extract_layout_stream(data, **options):
                assert data == b"fake-pdf"
                assert options["quality"] is True
                return Stream()

            @staticmethod
            def extract_layout(data, **options):
                raise AssertionError("whole-document fallback must not run")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(b"fake-pdf")
            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=StreamingRust()):
                document = RustPdfAdapter().parse(source)
            self.assertEqual(document.metadata["page_transfer"], "native_events_v2")
            self.assertEqual(len(document.pages), 1)

            class ShortStreamingRust(StreamingRust):
                @staticmethod
                def extract_layout_stream(data, **options):
                    return Stream(declared_pages=2)

            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=ShortStreamingRust()):
                with self.assertRaises(DocumaError) as caught:
                    RustPdfAdapter().parse(source)
        self.assertEqual(caught.exception.detail.code, "RUST_PDF_LAYOUT_INCOMPATIBLE")

    def test_decorative_placements_are_aggregated_by_default_and_opt_in_is_reversible(self):
        class DecorativeRust(_FakeRustPdf):
            @staticmethod
            def extract_layout(data, **options):
                value = _FakeRustPdf.extract_layout(data, **options)
                value["pages"][0]["image_placements"].append({
                    "id": "p0-i1", "paint_ordinal": 1, "resource_name": "Dot",
                    "object": {"number": 9, "generation": 0},
                    "bbox": {"x0": 1, "y0": 1, "x1": 2, "y1": 2},
                    "quad": {}, "source_node_ids": [], "confidence": 0.8,
                    "rule_id": "paint", "provenance": {},
                })
                return value

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(b"fake-pdf")
            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=DecorativeRust()):
                compact = RustPdfAdapter().parse(source)
                complete = RustPdfAdapter().parse(
                    source,
                    ParseOptions(metadata={"rust_pdf_include_decorative_images": True}),
                )
        self.assertEqual(len(compact.pages[0].images), 1)
        self.assertEqual(compact.pages[0].metadata["decorative_image_placements_suppressed"], 1)
        self.assertEqual(compact.metadata["decorative_image_placements_suppressed"], 1)
        self.assertEqual(len(complete.pages[0].images), 2)
        self.assertEqual(complete.pages[0].images[1].image_type, "decorative")
        self.assertTrue(complete.metadata["rust_pdf_include_decorative_images"])

    def test_incompatible_schema_is_a_stable_recoverable_error(self):
        class WrongSchema(_FakeRustPdf):
            @staticmethod
            def extract_layout(data, **options):
                value = _FakeRustPdf.extract_layout(data, **options)
                value["schema_version"] = 99
                return value
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.pdf"
            source.write_bytes(b"fake-pdf")
            with patch("documa.adapters.rust_pdf_adapter._load_rust_pdf", return_value=WrongSchema()):
                with self.assertRaises(DocumaError) as caught:
                    RustPdfAdapter().parse(source)
        self.assertEqual(caught.exception.detail.code, "RUST_PDF_LAYOUT_INCOMPATIBLE")
        self.assertTrue(caught.exception.detail.recoverable)


if __name__ == "__main__":
    unittest.main()
