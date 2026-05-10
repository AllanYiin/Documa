import unittest

from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentIR,
    ImageIR,
    PageIR,
    SpanIR,
    SpanStyle,
    TextContent,
)
from documa.pipeline import (
    ImageNormalizationStage,
    InlineSemanticsStage,
    LayoutClassificationStage,
    ParagraphGroupingStage,
    ReadingOrderStage,
    TableNormalizationStage,
)


def span(text, bbox, font_size=12.0):
    return SpanIR(id=f"s_{text}", text=TextContent(text), bbox=bbox, font_size=font_size)


def block(block_id, text, bbox, font_size=12.0, order_index=None, metadata=None):
    return BlockIR(
        id=block_id,
        type=BlockType.TEXT,
        page_number=1,
        text=TextContent(text),
        bbox=bbox,
        spans=[span(text, bbox, font_size)],
        order_index=order_index,
        metadata=metadata or {},
    )


class Stage3PipelineTests(unittest.TestCase):
    def test_reading_order_sorts_by_columns_before_rows(self):
        page = PageIR(
            id="p1",
            page_number=1,
            width=400,
            height=500,
            blocks=[
                block("left_top", "A1", (40, 20, 150, 40), order_index=1),
                block("right_top", "B1", (240, 20, 360, 40), order_index=2),
                block("left_bottom", "A2", (40, 60, 150, 80), order_index=3),
                block("right_bottom", "B2", (240, 60, 360, 80), order_index=4),
            ],
        )
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = ReadingOrderStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual([b.id for b in page.blocks], ["left_top", "left_bottom", "right_top", "right_bottom"])
        self.assertEqual([b.order_index for b in page.blocks], [1, 2, 3, 4])

    def test_inline_semantics_marks_smaller_raised_span_as_superscript(self):
        base = SpanIR(id="base", text=TextContent("note"), bbox=(40, 20, 70, 34), font_size=12)
        marker = SpanIR(id="marker", text=TextContent("1"), bbox=(72, 12, 78, 20), font_size=7)
        page = PageIR(
            id="p1",
            page_number=1,
            width=300,
            height=300,
            blocks=[
                BlockIR(
                    id="b1",
                    type=BlockType.TEXT,
                    page_number=1,
                    text=TextContent("note1"),
                    bbox=(40, 12, 78, 34),
                    spans=[base, marker],
                )
            ],
        )
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = InlineSemanticsStage().run(doc)

        self.assertTrue(result.changed)
        self.assertIn(SpanStyle.SUPERSCRIPT, marker.style)

    def test_layout_classification_marks_heading_and_page_regions(self):
        page = PageIR(
            id="p1",
            page_number=1,
            width=400,
            height=500,
            blocks=[
                block("header", "Documa", (30, 5, 120, 18), font_size=9),
                block("heading", "Section 1", (40, 80, 220, 105), font_size=20),
                block("body", "body text", (40, 130, 220, 150), font_size=12),
                block("footer", "1", (190, 475, 210, 492), font_size=9),
            ],
        )
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = LayoutClassificationStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(page.blocks[0].type, BlockType.PAGE_HEADER)
        self.assertEqual(page.blocks[1].type, BlockType.HEADING)
        self.assertEqual(page.blocks[3].type, BlockType.PAGE_FOOTER)

    def test_paragraph_grouping_merges_adjacent_cjk_rows_without_space(self):
        page = PageIR(
            id="p1",
            page_number=1,
            width=400,
            height=500,
            blocks=[
                block("line1", "這是第一行", (40, 40, 200, 60), order_index=1),
                block("line2", "接續第二行。", (40, 64, 220, 84), order_index=2),
            ],
        )
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = ParagraphGroupingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(len(page.blocks), 1)
        self.assertEqual(page.blocks[0].type, BlockType.PARAGRAPH)
        self.assertEqual(page.blocks[0].text.raw_text, "這是第一行接續第二行。")
        self.assertEqual(page.blocks[0].metadata["source_block_ids"], ["line1", "line2"])

    def test_table_normalization_converts_candidate_rows_to_table_ir(self):
        table_block = block(
            "table_block",
            "A B",
            (40, 40, 220, 120),
            metadata={"table_rows": [["Name", "Value"], ["A", "1"]]},
        )
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[table_block])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = TableNormalizationStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(table_block.type, BlockType.TABLE)
        self.assertEqual(len(doc.tables), 1)
        self.assertIn("| Name | Value |", doc.tables[0].markdown)

    def test_image_normalization_marks_chart_candidate_by_caption(self):
        image = ImageIR(
            id="img1",
            page_number=1,
            bbox=(40, 40, 160, 120),
            asset_ref="images/img1.png",
            caption="Figure 1: revenue chart",
        )
        page = PageIR(id="p1", page_number=1, width=400, height=500, images=[image])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = ImageNormalizationStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(image.image_type, "chart_candidate")
        self.assertTrue(image.metadata["normalized"])


if __name__ == "__main__":
    unittest.main()

