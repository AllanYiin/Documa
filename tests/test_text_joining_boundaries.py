import copy

import pytest

from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentIR,
    PageIR,
    SpanIR,
    SpanStyle,
    TextContent,
)
from documa.core.text_joining import typography_matches
from documa.exporters.pdf_reflow import reflow_pdf_text
from documa.pipeline.paragraphs import ParagraphGroupingStage
from documa.pipeline.pdf_headings import assign_pdf_heading_levels


def row(identity, text, y, size=10, font="F1", metadata=None):
    return BlockIR(
        id=identity,
        type=BlockType.TEXT,
        page_number=1,
        text=TextContent(text),
        bbox=(0, y, 400, y + 10),
        metadata=metadata or {},
        spans=[
            SpanIR(
                id=identity + "s",
                text=TextContent(text),
                bbox=(0, y, 400, y + 10),
                font_size=size,
                font_name=font,
            )
        ],
    )


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize(
    "change", ["size", "font", "bold", "heading", "ordered", "unordered"]
)
def test_boundaries_on_either_side_block_export_and_paragraph_join(side, change):
    rows = [row("a", "A full width line", 0), row("b", "continues here", 14)]
    changed = rows[side]
    if change == "size":
        changed.spans[0].font_size = 11
    elif change == "font":
        changed.spans[0].font_name = "F2"
    elif change == "bold":
        changed.spans[0].style = [SpanStyle.BOLD]
    else:
        role = {"heading": "H2", "ordered": "ol", "unordered": "ul"}[change]
        changed.metadata["tag"] = role
        changed.spans[0].metadata["tag"] = role
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[PageIR(id="p", page_number=1, width=600, height=800, blocks=rows)],
    )
    ParagraphGroupingStage().run(doc)
    assert len(doc.pages[0].blocks) == 2
    mixed = row("mixed", "A full width line\ncontinues here", 0)
    mixed.bbox = (0, 0, 400, 24)
    mixed.spans = rows[0].spans + rows[1].spans
    assert reflow_pdf_text(mixed, mixed.text.raw_text) == mixed.text.raw_text


def test_float_noise_is_not_a_new_font_size_but_rendered_scale_is():
    a, b = row("a", "some text", 0), row("b", "next text", 14, size=10.000001)
    assert typography_matches(a.spans, b.spans)
    a.spans[0].font_size = b.spans[0].font_size = 1
    b.spans[0].bbox = (0, 14, 400, 28)
    assert not typography_matches(a.spans, b.spans)


def test_same_scaled_font_uses_rendered_size_for_line_spacing():
    mixed = row("mixed", "A full width line\ncontinues here", 0)
    mixed.bbox = (0, 0, 400, 24)
    mixed.spans = (
        row("a", "A full width line", 0, size=1).spans
        + row("b", "continues here", 14, size=1).spans
    )
    assert (
        reflow_pdf_text(mixed, mixed.text.raw_text)
        == "A full width line continues here"
    )


def test_identical_typography_and_plain_rows_can_still_join():
    rows = [row("a", "A full width line", 0), row("b", "continues here", 14)]
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[PageIR(id="p", page_number=1, width=600, height=800, blocks=rows)],
    )
    ParagraphGroupingStage().run(doc)
    assert len(doc.pages[0].blocks) == 1


@pytest.mark.parametrize("side", [0, 1])
def test_caption_and_body_do_not_merge_even_with_the_same_font(side):
    rows = [row("a", "Ordinary body text", 0), row("b", "Another body line", 14)]
    rows[side].text = TextContent("Figure 1: A caption describing the figure.")
    rows[side].spans[0].text = rows[side].text
    doc = DocumentIR(id="d", source_name="test.pdf", parser="rust_pdf", pages=[
        PageIR(id="p", page_number=1, width=600, height=800, blocks=rows)])
    ParagraphGroupingStage().run(doc)
    assert len(doc.pages[0].blocks) == 2


def test_dominant_font_change_is_detected_even_with_same_font_set():
    left = (
        row("a", "long body phrase", 0, font="A").spans
        + row("b", "x", 0, font="B").spans
    )
    right = (
        row("c", "x", 14, font="A").spans
        + row("d", "long body phrase", 14, font="B").spans
    )
    assert not typography_matches(left, right)


def test_h6_render_limit_does_not_drop_numbered_parent_relationship():
    from documa.pipeline import BlockTreeBuildingStage

    parent, child = row("a", "1.1 Parent", 100), row("b", "1.1.1 Child", 130)
    for item in (parent, child):
        item.type = BlockType.HEADING
        item.metadata["heading_level"] = 6
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(id="p", page_number=1, width=600, height=800, blocks=[parent, child])
        ],
    )
    BlockTreeBuildingStage().run(doc)
    nodes = {b.title: b for b in doc.document_blocks if b.title}
    assert nodes["1.1.1 Child"].parent_id == nodes["1.1 Parent"].id


def test_global_font_levels_use_later_pages_and_recompute_auto_levels():
    title = row("title", "A Document Title", 80, size=30)
    early = row("early", "Small section", 200, size=12)
    mid = row("mid", "Medium section", 100, size=18)
    late = row("late", "Large section", 200, size=24)
    for item in (title, early, mid, late):
        item.type = BlockType.HEADING
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(
                id="p1",
                page_number=1,
                width=600,
                height=800,
                blocks=[title, early, row("body", "normal body text", 250)],
            ),
            PageIR(id="p2", page_number=2, width=600, height=800, blocks=[mid, late]),
        ],
    )
    assign_pdf_heading_levels(doc)
    assert [
        late.metadata["heading_level"],
        mid.metadata["heading_level"],
        early.metadata["heading_level"],
    ] == [2, 3, 4]
    assert [r["font_size"] for r in doc.metadata["pdf_heading_font_scale"]] == [
        24,
        18,
        12,
    ]
    extra = copy.deepcopy(late)
    extra.id = "extra"
    extra.spans[0].font_size = 26
    extra.metadata.clear()
    doc.pages[1].blocks.append(extra)
    assign_pdf_heading_levels(doc)
    assert extra.metadata["heading_level"] == 2
    assert late.metadata["heading_level"] == 3 and early.metadata["heading_level"] == 5


def test_abstract_uses_font_rank_without_a_fixed_level():
    items = [
        row("title", "A Document Title", 80, 30),
        row("large", "Large section", 180, 20),
        row("medium", "Medium section", 250, 16),
        row("abstract", "Abstract", 320, 12),
    ]
    for item in items:
        item.type = BlockType.HEADING
    items[-1].metadata.update(
        heading_level=2, heading_strategy="pdf_semantic_label_or_number"
    )
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(
                id="p",
                page_number=1,
                width=600,
                height=800,
                blocks=items + [row("body", "normal body text", 400)],
            )
        ],
    )
    assign_pdf_heading_levels(doc)
    assert items[-1].metadata["heading_level"] == 4


def test_more_than_five_font_tiers_stay_in_h2_to_h6():
    title = row("title", "A Document Title", 80, 40)
    title.type = BlockType.HEADING
    headings = [
        row(str(i), f"Section style {i}", 200, size)
        for i, size in enumerate([28, 24, 20, 18, 16, 14, 12])
    ]
    for item in headings:
        item.type = BlockType.HEADING
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(
                id="p",
                page_number=1,
                width=600,
                height=800,
                blocks=[title, *headings, row("body", "normal body text", 400)],
            )
        ],
    )
    assign_pdf_heading_levels(doc)
    levels = [h.metadata["heading_level"] for h in headings]
    assert levels == sorted(levels) and set(levels) == {2, 3, 4, 5, 6}
