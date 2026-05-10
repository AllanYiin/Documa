import unittest

from documa.core.ir import (
    BlockIR,
    BlockType,
    ChunkIR,
    Confidence,
    DocumentIR,
    ImageIR,
    PageIR,
    RelationState,
    RelationType,
    SpanIR,
    SpanStyle,
    TableIR,
    TextContent,
)
from documa.pipeline import CaptionLinkingStage, FootnoteLinkingStage, ProvenanceLinkingStage, TocLinkingStage


def block(block_id, text, bbox=(40, 40, 200, 60), block_type=BlockType.TEXT, page_number=1, metadata=None):
    return BlockIR(
        id=block_id,
        type=block_type,
        page_number=page_number,
        text=TextContent(text),
        bbox=bbox,
        metadata=metadata or {},
    )


class Stage4RelationPipelineTests(unittest.TestCase):
    def test_footnote_linking_links_superscript_marker_to_body(self):
        marker_span = SpanIR(
            id="s1",
            text=TextContent("1"),
            bbox=(85, 24, 91, 31),
            font_size=7,
            style=[SpanStyle.SUPERSCRIPT],
        )
        marker_block = block("body", "Documa keeps evidence1")
        marker_block.spans = [marker_span]
        footnote_block = block(
            "fn1",
            "1 Footnote body",
            bbox=(40, 460, 260, 480),
            block_type=BlockType.FOOTNOTE,
        )
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[marker_block, footnote_block])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = FootnoteLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.FOOTNOTE_MARKER_TO_BODY)
        self.assertEqual(doc.relations[0].from_id, "body")
        self.assertEqual(doc.relations[0].to_id, "fn1")
        self.assertEqual(doc.relations[0].metadata["marker"], "1")

    def test_footnote_linking_marks_missing_body_as_unresolved(self):
        marker_block = block("body", "Documa keeps evidence")
        marker_block.metadata["footnote_marker"] = "2"
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[marker_block])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = FootnoteLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.UNRESOLVED)
        self.assertEqual(doc.relations[0].state, RelationState.UNRESOLVED)
        self.assertIsNone(doc.relations[0].to_id)

    def test_toc_linking_links_toc_item_to_heading(self):
        toc = block(
            "toc",
            "1 Section",
            block_type=BlockType.TOC,
            metadata={"toc_items": [{"title": "Section 1", "page_number": 2}]},
        )
        heading = block("h1", "Section 1", block_type=BlockType.HEADING, page_number=2)
        page1 = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[toc])
        page2 = PageIR(id="p2", page_number=2, width=400, height=500, blocks=[heading])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page1, page2])

        result = TocLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.TOC_ITEM_TO_HEADING)
        self.assertEqual(doc.relations[0].from_id, "toc")
        self.assertEqual(doc.relations[0].to_id, "h1")
        self.assertEqual(doc.relations[0].confidence, Confidence.HIGH)

    def test_caption_linking_links_figure_caption_to_image(self):
        caption = block("cap1", "Figure 1: Revenue chart", bbox=(40, 140, 260, 160))
        image = ImageIR(
            id="img1",
            page_number=1,
            bbox=(40, 40, 260, 130),
            asset_ref="assets/img1.png",
        )
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[caption], images=[image])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])

        result = CaptionLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.CAPTION_TO_IMAGE)
        self.assertEqual(doc.relations[0].from_id, "cap1")
        self.assertEqual(doc.relations[0].to_id, "img1")

    def test_caption_linking_links_table_caption_to_table(self):
        caption = block("cap1", "表 1：資料表", bbox=(40, 20, 260, 40))
        table_block = block("tbl_block", "A B", bbox=(40, 45, 260, 120), block_type=BlockType.TABLE)
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[caption, table_block])
        doc = DocumentIR(
            id="d1",
            source_name="fixture.pdf",
            pages=[page],
            tables=[TableIR(id="table_tbl_block", block_id="tbl_block")],
        )

        result = CaptionLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.CAPTION_TO_TABLE)
        self.assertEqual(doc.relations[0].to_id, "tbl_block")

    def test_provenance_linking_materializes_chunk_sources(self):
        source = block("b1", "source")
        page = PageIR(id="p1", page_number=1, width=400, height=500, blocks=[source])
        chunk = ChunkIR(id="chunk1", text=TextContent("source"), source_block_ids=["b1"])
        doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page], chunks=[chunk])

        result = ProvenanceLinkingStage().run(doc)

        self.assertTrue(result.changed)
        self.assertEqual(doc.relations[0].type, RelationType.CHUNK_TO_SOURCE)
        self.assertEqual(doc.relations[0].from_id, "chunk1")
        self.assertEqual(doc.relations[0].to_id, "b1")
        self.assertEqual(chunk.relation_ids, [doc.relations[0].id])


if __name__ == "__main__":
    unittest.main()

