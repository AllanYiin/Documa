import copy

import pytest

from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, SpanIR, TextContent
from documa.exporters import MarkdownExporter
from documa.exporters.pdf_reflow import reflow_pdf_text


def paragraph(text="A full width line\ncontinues here", boxes=None):
    lines = text.splitlines()
    boxes = boxes or [(0, 0, 400, 10), (0, 14, 180, 24)]
    return BlockIR(
        id="body",
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent(text),
        bbox=(0, 0, 400, max(b[3] for b in boxes)),
        spans=[
            SpanIR(id=f"s{i}", text=TextContent(line), bbox=box, font_size=10)
            for i, (line, box) in enumerate(zip(lines, boxes))
        ],
    )


def test_full_width_soft_wrap_is_joined_without_mutating_evidence():
    block = paragraph()
    before = copy.deepcopy(block)
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[PageIR(id="p", page_number=1, width=600, height=800, blocks=[block])],
    )
    assert "A full width line continues here" in MarkdownExporter().export(doc)
    assert block == before


@pytest.mark.parametrize("ending", [".", "?", "!", "。", "？", "！", '."', ". [9]"])
def test_sentence_ending_keeps_break(ending):
    block = paragraph(f"A complete sentence{ending}\nNext sentence")
    block.type = BlockType.TEXT
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


@pytest.mark.parametrize(
    "boxes",
    [
        [(0, 0, 240, 10), (0, 14, 180, 24)],  # Short row.
        [(0, 0, 400, 10), (0, 30, 180, 40)],  # Paragraph-sized vertical gap.
        [(0, 0, 400, 10), (20, 14, 200, 24)],  # Indented new paragraph.
        [(0, 0, 400, 10), (0, 0, 180, 10)],  # Same row / ambiguous mapping.
    ],
)
def test_paragraph_boundaries_keep_break(boxes):
    block = paragraph(boxes=boxes)
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


def test_relative_line_spacing_detects_extra_gap_below_absolute_limit():
    text = "First full row\nsecond full row\nnew paragraph"
    block = paragraph(text, [(0, 0, 400, 10), (0, 12, 400, 22), (0, 30, 200, 40)])
    assert (
        reflow_pdf_text(block, text) == "First full row second full row\nnew paragraph"
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("我們持續研究\n新的方法", "我們持續研究新的方法"),
        ("an image-\nand-text model", "an image-and-text model"),
        (
            "Words, references [9]\ncontinue normally",
            "Words, references [9] continue normally",
        ),
    ],
)
def test_join_separators_preserve_source_characters(text, expected):
    assert reflow_pdf_text(paragraph(text), text) == expected


def test_inline_citation_does_not_split_a_known_paragraph():
    block = paragraph("Some words [9]\ncontinue here")
    block.spans[:1] = [
        SpanIR(
            id="a", text=TextContent("Some words"), bbox=(0, 0, 380, 10), font_size=10
        ),
        SpanIR(id="b", text=TextContent("[9]"), bbox=(385, -2, 400, 6), font_size=7),
    ]
    assert reflow_pdf_text(block, block.text.raw_text) == "Some words [9] continue here"


def test_missing_or_mismatched_geometry_keeps_breaks():
    for mutation in ("missing", "changed", "crossing"):
        block = paragraph()
        if mutation == "missing":
            block.spans[0].bbox = None
        elif mutation == "changed":
            block.spans[0].text = TextContent("different content")
        else:
            block.spans[0].text = TextContent(block.text.raw_text)
            block.spans = block.spans[:1]
        assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


@pytest.mark.parametrize("role", ["list", "list_item", "list_body", "code"])
def test_structured_roles_keep_breaks(role):
    block = paragraph()
    block.metadata["rust_pdf_role"] = role
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


def test_non_pdf_export_and_list_marker_keep_breaks():
    block = paragraph()
    doc = DocumentIR(
        id="d",
        source_name="test.md",
        parser="markdown",
        pages=[PageIR(id="p", page_number=1, width=600, height=800, blocks=[block])],
    )
    assert block.text.raw_text in MarkdownExporter().export(doc)
    block = paragraph("First row\n1. Another item")
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


@pytest.mark.parametrize(
    "text",
    [
        "def combine(left, right):\n    return left + right",
        "result = combine(left,\nright)",
        "if (ready) {\nexecute();",
        '"name": "a long value",\n"count": 2',
        "import numpy as np\nnp.zeros(10)",
        "```python\nresult = 1",
        "A comment describing code\n    indented_call()",
    ],
)
def test_untagged_full_width_code_keeps_newlines_and_indentation(text):
    block = paragraph(text)
    assert reflow_pdf_text(block, text) == text


@pytest.mark.parametrize("marker", ["tag", "font"])
def test_code_metadata_or_monospace_font_protects_plain_looking_code(marker):
    block = paragraph()
    if marker == "tag":
        block.metadata["tag"] = "Code"
    else:
        block.spans[0].font_name = "ABCDEF+CourierNewPSMT"
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


@pytest.mark.parametrize(
    "marker", ["1.", "2)", "(3)", "[4]", "a.", "IV.", "-", "*", "+", "•", "一、", "①"]
)
def test_list_item_continuations_are_not_reflowed(marker):
    text = f"{marker} A full width item\n  its wrapped continuation"
    block = paragraph(text)
    assert reflow_pdf_text(block, text) == text


@pytest.mark.parametrize(
    "tag", ["LI", "LBody", "ul", "ol", "ordered_list", "unordered_list"]
)
def test_semantic_lists_without_visible_markers_keep_breaks(tag):
    block = paragraph()
    block.metadata["tag"] = tag
    assert reflow_pdf_text(block, block.text.raw_text) == block.text.raw_text


def test_code_fence_spanning_blocks_protects_code_but_not_following_prose():
    block = paragraph()
    opening = BlockIR(
        id="open", type=BlockType.TEXT, page_number=1, text=TextContent("```text")
    )
    closing = BlockIR(
        id="close", type=BlockType.TEXT, page_number=1, text=TextContent("```")
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
                blocks=[opening, block, closing, paragraph()],
            ),
        ],
    )
    exported = MarkdownExporter().export(doc)
    assert exported.count("A full width line\ncontinues here") == 1
    assert exported.count("A full width line continues here") == 1


def test_short_heading_in_block_does_not_disable_following_prose_reflow():
    text = "Abstract\nA full width line\ncontinues here"
    block = paragraph(text, [(0, 0, 60, 10), (0, 24, 400, 34), (0, 38, 180, 48)])
    assert reflow_pdf_text(block, text) == "Abstract\nA full width line continues here"


def test_pdf_export_keeps_initial_code_indentation():
    block = paragraph("    first indented statement\n    second indented statement")
    doc = DocumentIR(
        id="d",
        source_name="test.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(id="p", page_number=1, width=600, height=800, blocks=[block]),
        ],
    )
    assert block.text.raw_text in MarkdownExporter().export(doc)
