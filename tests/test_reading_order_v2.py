"""ReadingOrderStage v2 unit tests: zones, gutters, spanners, trace, fallbacks."""

from __future__ import annotations

import unittest

from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent
from documa.pipeline import PipelineContext, ReadingOrderStage


def block(block_id, text, bbox, block_type=BlockType.TEXT, order_index=None):
    return BlockIR(
        id=block_id,
        type=block_type,
        page_number=1,
        text=TextContent(text),
        bbox=bbox,
        order_index=order_index,
    )


def page_with(blocks):
    return PageIR(id="p1", page_number=1, width=600, height=800, blocks=blocks)


def run_stage(page, settings=None):
    doc = DocumentIR(id="d1", source_name="fixture.pdf", pages=[page])
    ReadingOrderStage().run(doc, PipelineContext(settings=settings or {}))
    return page


class TwoColumnTests(unittest.TestCase):
    def test_column_first_order_reads_whole_left_column_before_right(self):
        page = page_with(
            [
                block("L1", "left one", (50, 100, 280, 140)),
                block("R1", "right one", (320, 100, 550, 140)),
                block("L2", "left two", (50, 160, 280, 200)),
                block("R2", "right two", (320, 160, 550, 200)),
            ]
        )
        run_stage(page)
        self.assertEqual([b.id for b in page.blocks], ["L1", "L2", "R1", "R2"])
        self.assertEqual([b.order_index for b in page.blocks], [1, 2, 3, 4])
        for b in page.blocks:
            self.assertEqual(b.metadata["reading_order"]["rule"], "column_flow")
            self.assertIn("continuity", b.metadata["reading_order"]["gestalt"])

    def test_narrow_gutter_below_threshold_stays_single_column(self):
        page = page_with(
            [
                block("L1", "left", (50, 100, 295, 140)),
                block("R1", "right", (300, 100, 550, 140)),  # 5pt gap < 12pt
            ]
        )
        run_stage(page)
        self.assertEqual(page.blocks[0].metadata["reading_order"]["rule"], "single_column")
        self.assertEqual(page.metadata["reading_order_trace"]["gutters"], [])


class SpannerTests(unittest.TestCase):
    def _spanner_page(self):
        return page_with(
            [
                block("TITLE", "wide title", (50, 40, 550, 80)),  # width 500/500 -> spanner
                block("L1", "left one", (50, 120, 280, 160)),
                block("R1", "right one", (320, 120, 550, 160)),
                block("L2", "left two", (50, 180, 280, 220)),
                block("R2", "right two", (320, 180, 550, 220)),
            ]
        )

    def test_cross_column_title_is_ordered_before_columns(self):
        page = run_stage(self._spanner_page())
        self.assertEqual([b.id for b in page.blocks], ["TITLE", "L1", "L2", "R1", "R2"])
        title_trace = page.blocks[0].metadata["reading_order"]
        self.assertEqual(title_trace["rule"], "spanner")
        self.assertIsNone(title_trace["column_index"])

    def test_near_spanner_title_does_not_mask_gutter(self):
        # Title at 69% of content width: not a page spanner at default 0.65?
        # It IS >= 0.65, so shrink it to 60% and confirm banding still rescues
        # the columns via recursion when ratio override marks it as spanner.
        page = page_with(
            [
                block("TITLE", "medium title", (50, 40, 350, 80)),  # 60% of content width
                block("L1", "left one", (50, 120, 280, 160)),
                block("R1", "right one", (320, 120, 550, 160)),
                block("L2", "left two", (50, 180, 280, 220)),
            ]
        )
        run_stage(page, settings={"reading_order_spanner_ratio": 0.55})
        self.assertEqual([b.id for b in page.blocks], ["TITLE", "L1", "L2", "R1"])

    def test_footer_type_is_spanner_even_when_narrow(self):
        page = page_with(
            [
                block("BODY", "body", (50, 100, 550, 600)),
                block("PN", "3", (290, 760, 310, 775), block_type=BlockType.PAGE_FOOTER),
            ]
        )
        run_stage(page)
        self.assertEqual(page.blocks[-1].id, "PN")
        self.assertEqual(page.blocks[-1].metadata["reading_order"]["rule"], "spanner")


class TraceTests(unittest.TestCase):
    def test_page_trace_records_zones_and_gutters(self):
        page = page_with(
            [
                block("L1", "left", (50, 100, 280, 140)),
                block("R1", "right", (320, 100, 550, 140)),
            ]
        )
        run_stage(page)
        trace = page.metadata["reading_order_trace"]
        self.assertEqual(len(trace["gutters"]), 1)
        self.assertAlmostEqual(trace["gutters"][0]["x0"], 280.0)
        self.assertAlmostEqual(trace["gutters"][0]["x1"], 320.0)
        content_zones = [z for z in trace["zones"] if z["kind"] == "content"]
        self.assertEqual(content_zones[0]["column_count"], 2)

    def test_strategy_is_stamped_on_every_block(self):
        page = run_stage(page_with([block("A", "a", (50, 100, 200, 140))]))
        self.assertEqual(page.blocks[0].metadata["reading_order"]["strategy"], "zone_column_v2")

    def test_original_order_index_is_preserved(self):
        page = page_with(
            [
                block("R1", "right", (320, 100, 550, 140), order_index=1),
                block("L1", "left", (50, 100, 280, 140), order_index=2),
            ]
        )
        run_stage(page)
        by_id = {b.id: b for b in page.blocks}
        self.assertEqual(by_id["R1"].metadata["original_order_index"], 1)
        self.assertEqual(by_id["L1"].order_index, 1)


class FallbackTests(unittest.TestCase):
    def test_blocks_without_bbox_go_last_with_fallback_rule(self):
        no_bbox = BlockIR(id="X", type=BlockType.TEXT, page_number=1, text=TextContent("x"), bbox=None)
        page = page_with([no_bbox, block("A", "a", (50, 100, 200, 140))])
        run_stage(page)
        self.assertEqual(page.blocks[-1].id, "X")
        self.assertEqual(page.blocks[-1].metadata["reading_order"]["rule"], "fallback_row_major")

    def test_empty_page_and_single_block_page(self):
        empty = PageIR(id="p0", page_number=1, width=600, height=800, blocks=[])
        doc = DocumentIR(id="d", source_name="s", pages=[empty])
        ReadingOrderStage().run(doc)  # must not raise
        self.assertEqual(empty.blocks, [])

        single = run_stage(page_with([block("ONLY", "only", (50, 100, 550, 140))]))
        self.assertEqual(single.blocks[0].order_index, 1)

    def test_overlapping_intervals_degrade_to_single_column_zone(self):
        # Interleaved x-intervals: no clean gutter -> honest single column.
        page = page_with(
            [
                block("A", "a", (50, 100, 400, 140)),
                block("B", "b", (300, 160, 550, 200)),
                block("C", "c", (100, 220, 450, 260)),
            ]
        )
        run_stage(page)
        trace = page.metadata["reading_order_trace"]
        self.assertEqual(trace["gutters"], [])
        self.assertTrue(all(b.metadata["reading_order"]["rule"] != "column_flow" for b in page.blocks))


class DeterminismTests(unittest.TestCase):
    def test_same_input_gives_same_order_and_trace(self):
        def build():
            return page_with(
                [
                    block("TITLE", "wide title", (50, 40, 550, 80)),
                    block("L1", "left one", (50, 120, 280, 160)),
                    block("R1", "right one", (320, 120, 550, 160)),
                ]
            )

        first = run_stage(build())
        second = run_stage(build())
        self.assertEqual([b.id for b in first.blocks], [b.id for b in second.blocks])
        self.assertEqual(first.metadata["reading_order_trace"], second.metadata["reading_order_trace"])


if __name__ == "__main__":
    unittest.main()
