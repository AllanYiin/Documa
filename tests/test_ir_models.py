import json
import unittest

from documa.core.ir import (
    BlockIR,
    BlockType,
    ChunkIR,
    Confidence,
    DocumentIR,
    PageIR,
    RelationIR,
    RelationType,
    TextContent,
    to_plain_data,
)


class IRModelTests(unittest.TestCase):
    def test_document_ir_serializes_without_ascii_escaping(self):
        block = BlockIR(
            id="b1",
            type=BlockType.PARAGRAPH,
            page_number=1,
            text=TextContent("繁體中文與 English"),
            bbox=(0.0, 0.0, 100.0, 20.0),
            confidence=Confidence.HIGH,
        )
        doc = DocumentIR(
            id="d1",
            source_name="測試.pdf",
            parser="stage0",
            pages=[PageIR(id="p1", page_number=1, width=595.0, height=842.0, blocks=[block])],
        )

        payload = json.dumps(to_plain_data(doc), ensure_ascii=False)

        self.assertIn("繁體中文", payload)
        self.assertIn('"type": "paragraph"', payload)
        self.assertIn('"confidence": "high"', payload)

    def test_chunk_requires_source_blocks_by_convention(self):
        chunk = ChunkIR(
            id="c1",
            text=TextContent("chunk 內容"),
            source_block_ids=["b1"],
            page_refs=[1],
            bbox_refs=[(0.0, 0.0, 10.0, 10.0)],
        )

        self.assertEqual(chunk.source_block_ids, ["b1"])
        self.assertEqual(chunk.text.normalized_text, "chunk 內容")

    def test_relation_can_be_unresolved(self):
        relation = RelationIR(
            id="r1",
            type=RelationType.UNRESOLVED,
            from_id="b1",
            evidence=["marker detected but body missing"],
        )

        self.assertIsNone(relation.to_id)
        self.assertEqual(relation.type, RelationType.UNRESOLVED)


if __name__ == "__main__":
    unittest.main()

