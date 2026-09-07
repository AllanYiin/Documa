import copy

import pytest

from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentIR,
    PageIR,
    SpanIR,
    ImageIR,
    TextContent,
    DocumentBlockType,
)
from documa.exporters import MarkdownExporter
from documa.pipeline import BlockTreeBuildingStage, PipelineContext
from documa.pipeline.block_tree import document_block_text
from documa.pipeline.figures import classify_figure_regions


def block(identity, text, y, kind=BlockType.TEXT, page=1, size=10, font="F1"):
    box = (50, y, 450, y + 10)
    return BlockIR(
        id=identity,
        type=kind,
        page_number=page,
        text=TextContent(text),
        bbox=box,
        spans=[
            SpanIR(
                id=identity + "s",
                text=TextContent(text),
                bbox=box,
                font_size=size,
                font_name=font,
            )
        ],
    )


def test_paragraph_continues_across_figure_and_page_without_losing_sources():
    head = block("head", "We minimize distance between the outputs of", 650)
    figure = block("figure", "Figure material", 100, BlockType.IMAGE, page=2)
    tail = block(
        "tail", "the text encoder, preserving the original meaning.", 300, page=2
    )
    original = [head.text.raw_text, tail.text.raw_text]
    doc = DocumentIR(
        id="d",
        source_name="paper.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(id="p1", page_number=1, width=600, height=800, blocks=[head]),
            PageIR(
                id="p2", page_number=2, width=600, height=800, blocks=[figure, tail]
            ),
        ],
    )
    BlockTreeBuildingStage().run(doc)
    paragraph = next(
        b for b in doc.document_blocks if b.source_block_ids == ["head", "tail"]
    )
    assert paragraph.page_refs == [1, 2]
    assert doc.metadata["paragraph_continuations"][0]["bridges"][0][
        "skipped_block_ids"
    ] == ["figure"]
    assert "outputs of the text encoder" in document_block_text(doc, paragraph)
    md = MarkdownExporter().export(doc)
    assert md.count("the text encoder") == 1
    assert md.index("outputs of the text encoder") < md.index("Figure material")
    assert [head.text.raw_text, tail.text.raw_text] == original
    doc.pages[1].blocks.remove(tail)
    BlockTreeBuildingStage().run(
        doc, PipelineContext(settings={"force_rebuild_blocks": True})
    )
    assert not doc.metadata.get("paragraph_continuations")


@pytest.mark.parametrize(
    "barrier", ["heading", "list", "code", "font", "sentence", "indent"]
)
def test_continuation_does_not_cross_real_boundaries(barrier):
    head = block("head", "We refer to the outputs of", 500)
    middle = block("middle", "Figure material", 100, BlockType.IMAGE, page=2)
    tail = block("tail", "the text encoder continues here.", 300, page=2)
    if barrier == "heading":
        middle.type = BlockType.HEADING
        middle.text = TextContent("New section")
    elif barrier == "list":
        middle.type = BlockType.TEXT
        middle.text = TextContent("1. A list item")
    elif barrier == "code":
        middle.type = BlockType.TEXT
        middle.text = TextContent("result = 1")
    elif barrier == "font":
        tail.spans[0].font_name = "F2"
    elif barrier == "sentence":
        head.text = TextContent("A complete sentence.")
    else:
        tail.bbox = tail.spans[0].bbox = (80, 300, 450, 310)
    doc = DocumentIR(
        id="d",
        source_name="paper.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(id="p1", page_number=1, width=600, height=800, blocks=[head]),
            PageIR(
                id="p2", page_number=2, width=600, height=800, blocks=[middle, tail]
            ),
        ],
    )
    BlockTreeBuildingStage().run(doc)
    assert not doc.metadata.get("paragraph_continuations")


def test_sentences_in_one_known_paragraph_are_reflowed_together():
    from documa.exporters.pdf_reflow import reflow_pdf_text

    b = block("body", "One complete sentence.\nAnother sentence follows.", 100)
    b.type = BlockType.PARAGRAPH
    second = block("second", "Another sentence follows.", 114)
    b.spans[0].text = TextContent("One complete sentence.")
    b.spans.extend(second.spans)
    b.bbox = (50, 100, 450, 124)
    assert (
        reflow_pdf_text(b, b.text.raw_text)
        == "One complete sentence. Another sentence follows."
    )


def test_publication_line_and_small_bottom_note_do_not_replace_body_flow():
    from documa.pipeline import LayoutClassificationStage

    body = block("body", "Normal body text establishes the page typography.", 200)
    publication = block(
        "pub", "37th Conference on Learning Systems (2023).", 745, size=8
    )
    note = block("note", "1Source note with no separating space.", 710, size=8)
    doc = DocumentIR(
        id="d",
        source_name="paper.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(
                id="p",
                page_number=1,
                width=600,
                height=800,
                blocks=[body, note, publication],
            )
        ],
    )
    LayoutClassificationStage().run(doc)
    assert publication.type == BlockType.PAGE_FOOTER
    assert note.type == BlockType.FOOTNOTE


def test_figure_region_is_an_image_container_not_body_paragraphs():
    label1 = block("l1", "First diagram label", 60, size=20)
    label2 = block("l2", "Second diagram label", 80, size=20)
    for label in (label1, label2):
        label.bbox = label.spans[0].bbox = (60, label.bbox[1], 220, label.bbox[1] + 5)
    caption = block("cap", "Figure 1: The figure caption.", 170)
    body = block("body", "Ordinary body continues below.", 220)
    image = ImageIR(
        id="img", page_number=1, asset_ref="figure.png", bbox=(60, 110, 180, 150)
    )
    doc = DocumentIR(
        id="d",
        source_name="paper.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(
                id="p1",
                page_number=1,
                width=600,
                height=800,
                blocks=[label1, label2, caption, body],
                images=[image],
            )
        ],
    )
    original = copy.deepcopy([b.text.raw_text for b in doc.pages[0].blocks])
    assert classify_figure_regions(doc) == 1
    assert not body.metadata.get("figure_group_id")
    BlockTreeBuildingStage().run(doc)
    figure = next(b for b in doc.document_blocks if b.type == DocumentBlockType.IMAGE)
    assert figure.source_block_ids == ["cap"] and len(figure.child_ids) == 2
    md = MarkdownExporter().export(doc)
    assert (
        md.index("<details>")
        < md.index("First diagram label")
        < md.index("</details>")
        < md.index("Ordinary body")
    )
    assert md.count("![image](figure.png)") == 1
    assert [b.text.raw_text for b in doc.pages[0].blocks] == original
    doc.pages[0].blocks.remove(caption)
    assert classify_figure_regions(doc) == 0
    assert not label1.metadata.get("figure_group_id") and not image.metadata.get(
        "figure_group_id"
    )
