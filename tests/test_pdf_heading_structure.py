import copy

from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentIR,
    PageIR,
    SpanIR,
    TextContent,
    DocumentBlockType,
)
from documa.pipeline import (
    LayoutClassificationStage,
    ParagraphGroupingStage,
    BlockTreeBuildingStage,
    ChunkingStage,
)
from documa.exporters import MarkdownExporter


def block(identity, text, y=150, sizes=None):
    lines = text.splitlines()
    sizes = sizes or [10] * len(lines)
    spans = [
        SpanIR(
            id=f"{identity}s{i}",
            text=TextContent(line),
            font_size=size,
            bbox=(
                50,
                y + i * 24,
                50 + min(400, len(line) * size * 0.5),
                y + i * 24 + size,
            ),
        )
        for i, (line, size) in enumerate(zip(lines, sizes))
    ]
    return BlockIR(
        id=identity,
        type=BlockType.TEXT,
        page_number=1,
        text=TextContent(text),
        spans=spans,
        bbox=(50, y, max(s.bbox[2] for s in spans), spans[-1].bbox[3]),
        source_refs=[f"pdf-node:{identity}"],
    )


def document(blocks):
    return DocumentIR(
        id="doc",
        source_name="paper.pdf",
        parser="rust_pdf",
        pages=[
            PageIR(id="p1", page_number=1, width=600, height=800, blocks=blocks),
        ],
    )


def build(doc):
    LayoutClassificationStage().run(doc)
    ParagraphGroupingStage().run(doc)
    BlockTreeBuildingStage().run(doc)
    ChunkingStage().run(doc)
    return doc


def test_title_abstract_and_numbered_sections_are_structural_not_export_only():
    doc = build(
        document(
            [
                block("title", "A Study of Document Understanding", 90, [18]),
                block("abstract", "Abstract", 160, [12]),
                block(
                    "abody", "We propose a method to understand source documents.", 190
                ),
                block(
                    "intro",
                    "1 Introduction\nThe introduction explains the research problem.",
                    270,
                    [12, 10],
                ),
                block(
                    "method",
                    "2 Methods\nWe evaluate a useful method over many examples.",
                    370,
                    [12, 10],
                ),
                block(
                    "detail",
                    "2.1 Setup\nThe experiment uses a fixed random seed.",
                    470,
                    [11, 10],
                ),
            ]
        )
    )
    md = MarkdownExporter().export(doc)
    assert md.count("\n# ") == 0 and md.startswith(
        "# A Study of Document Understanding\n"
    )
    assert "## Abstract\n\nWe propose" in md
    assert "## 1 Introduction\n\nThe introduction" in md
    assert "### 2.1 Setup\n\nThe experiment" in md
    nodes = {node.title: node for node in doc.document_blocks if node.title}
    assert nodes["A Study of Document Understanding"].type == DocumentBlockType.DOCUMENT
    assert nodes["Abstract"].parent_id == nodes["A Study of Document Understanding"].id
    assert (
        nodes["1 Introduction"].parent_id
        == nodes["A Study of Document Understanding"].id
    )
    assert nodes["2.1 Setup"].parent_id == nodes["2 Methods"].id
    abstract_leaf = next(
        node for node in doc.document_blocks if node.source_block_ids == ["abody"]
    )
    page_parent = next(
        node for node in doc.document_blocks if node.id == abstract_leaf.parent_id
    )
    assert page_parent.parent_id == nodes["Abstract"].id
    assert any(chunk.heading_path == ["Abstract"] for chunk in doc.chunks)


def test_text_matrix_scaled_fonts_support_headings_without_mutating_font_data():
    title = block("title", "A Document Title", 90, [20])
    chapter = block("chapter", "1) Methods", 180, [14])
    body = block(
        "body", "The document body describes the methods used in this study.", 220, [10]
    )
    for item in (title, chapter, body):
        for span in item.spans:
            span.font_size = 1.0
    doc = build(document([title, chapter, body]))
    assert doc.metadata["document_title"] == "A Document Title"
    assert "## 1) Methods" in MarkdownExporter().export(doc)
    assert all(
        span.font_size == 1.0 for p in doc.pages for b in p.blocks for span in b.spans
    )


def test_literal_hash_comments_in_pdf_body_do_not_create_h1():
    item = block("code", "# Example comment\nvalue = 1", 200)
    item.metadata["tag"] = "Code"
    doc = build(document([item]))
    md = MarkdownExporter().export(doc)
    assert sum(line.startswith("# ") for line in md.splitlines()) == 1
    assert "\\# Example comment" in md


def test_multiline_document_title_is_one_root_and_one_markdown_h1():
    doc = build(
        document(
            [
                block("t1", "A Long Document Title", 90, [18]),
                block("t2", "Continued on the Next Line", 112, [18]),
                block("body", "The body starts below the complete title.", 180, [10]),
            ]
        )
    )
    expected = "A Long Document Title Continued on the Next Line"
    assert doc.metadata["document_title"] == expected
    root = next(
        node for node in doc.document_blocks if node.type == DocumentBlockType.DOCUMENT
    )
    assert root.title == expected and root.source_block_ids == ["t1", "t2"]
    assert MarkdownExporter().export(doc).startswith(f"# {expected}\n")
    assert not any(
        node.type == DocumentBlockType.SECTION for node in doc.document_blocks
    )


def test_numbered_chapter_is_not_mistaken_for_document_title():
    doc = build(
        document(
            [
                block("intro", "1 Introduction", 90, [18]),
                block(
                    "body",
                    "This paragraph describes the paper and its methods.",
                    160,
                    [12],
                ),
            ]
        )
    )
    assert "document_title" not in doc.metadata
    assert MarkdownExporter().export(doc).startswith("# paper.pdf\n")
    assert "## 1 Introduction" in MarkdownExporter().export(doc)


def test_split_preserves_full_source_text_ranges_and_span_identity():
    original = block(
        "mixed", "1 Introduction\nThe body remains unchanged.", 200, [12, 10]
    )
    raw = copy.deepcopy(original)
    doc = document(
        [original, block("other", "Other body words for the font baseline.", 300)]
    )
    LayoutClassificationStage().run(doc)
    parts = [
        b
        for b in doc.pages[0].blocks
        if b.metadata.get("heading_source_range", {}).get("block_id") == "mixed"
    ]
    assert len(parts) == 2
    assert "".join(b.text.raw_text for b in parts) == raw.text.raw_text
    assert [s.id for b in parts for s in b.spans] == [s.id for s in raw.spans]
    snapshot = doc.metadata["pdf_heading_sources"]["mixed"]
    assert snapshot["text"]["raw_text"] == raw.text.raw_text
    for part in parts:
        ref = part.metadata["heading_source_range"]
        assert (
            raw.text.raw_text[ref["raw_start"] : ref["raw_end"]] == part.text.raw_text
        )
        assert part.source_refs == raw.source_refs
    before = copy.deepcopy(doc)
    LayoutClassificationStage().run(doc)
    assert doc == before


def test_abstract_prefix_requires_typographic_evidence():
    merged = block(
        "a", "Abstract\nWe propose a method for document analysis.", 180, [12, 10]
    )
    merged.text = TextContent("Abstract We propose a method for document analysis.")
    doc = build(
        document(
            [
                merged,
                block("body", "More body text supports the normal font baseline.", 300),
            ]
        )
    )
    assert "## Abstract\n\nWe propose" in MarkdownExporter().export(doc)
    ordinary = block(
        "ordinary", "Abstract thought is discussed in this paragraph.", 300
    )
    doc = document([ordinary])
    LayoutClassificationStage().run(doc)
    assert ordinary.type == BlockType.TEXT


def test_code_and_explicit_lists_are_not_promoted_to_sections():
    code = block("code", "1 Introduction\nbody", 200, [12, 10])
    code.metadata["tag"] = "Code"
    item = block("list", "1 Important item\nits continuation", 300, [12, 10])
    item.metadata["tag"] = "LI"
    doc = document([code, item])
    LayoutClassificationStage().run(doc)
    assert all(b.type == BlockType.TEXT for b in doc.pages[0].blocks)


def test_numbered_parent_wins_over_pdf_column_order_and_missing_parent_is_explicit():
    items = []
    for index, (text, level) in enumerate(
        [("3 Method", 2), ("4 Analysis", 2), ("3.4 Loss", 3), ("5.1 Setup", 3)]
    ):
        item = block(str(index), text)
        item.type = BlockType.HEADING
        item.order_index = index
        item.metadata["heading_level"] = level
        items.append(item)
    doc = document(items)
    BlockTreeBuildingStage().run(doc)
    nodes = {node.title: node for node in doc.document_blocks if node.title}
    assert nodes["3.4 Loss"].parent_id == nodes["3 Method"].id
    assert nodes["5.1 Setup"].parent_id == nodes["paper.pdf"].id
    assert nodes["5.1 Setup"].metadata["hierarchy_unresolved"] == {
        "missing_numbered_parent": "5"
    }


def test_authored_markdown_hierarchy_is_not_reparented_by_pdf_number_rules():
    parent = block("parent", "3.1 Authored Parent")
    child = block("child", "3 Authored Child")
    for item, level in ((parent, 1), (child, 2)):
        item.type = BlockType.HEADING
        item.metadata["heading_level"] = level
    doc = document([parent, child])
    doc.parser = "markdown"
    BlockTreeBuildingStage().run(doc)
    nodes = {node.title: node for node in doc.document_blocks if node.title}
    assert nodes["3 Authored Child"].parent_id == nodes["3.1 Authored Parent"].id


def test_skipped_heading_levels_are_siblings_by_level_not_stack_length():
    items = []
    for index, (text, level) in enumerate(
        [("Start", 1), ("A", 3), ("B", 3), ("Deep", 5), ("Next", 2)]
    ):
        item = block(str(index), text)
        item.type = BlockType.HEADING
        item.order_index = index
        item.metadata["heading_level"] = level
        items.append(item)
    doc = document(items)
    doc.parser = "markdown"
    BlockTreeBuildingStage().run(doc)
    nodes = {node.title: node for node in doc.document_blocks if node.title}
    assert nodes["A"].parent_id == nodes["Start"].id
    assert nodes["B"].parent_id == nodes["Start"].id
    assert nodes["Deep"].parent_id == nodes["B"].id
    assert nodes["Next"].parent_id == nodes["Start"].id


def test_body_size_subsection_uses_established_heading_font():
    main = block("main", "2 Methods", 170, [12])
    sub = block("sub", "2.1 Setup", 270, [10])
    for item in (main, sub):
        item.spans[0].font_name = "HeadingFont"
    body = block(
        "body", "The experiment describes the detailed baseline in ordinary prose.", 310
    )
    body.spans[0].font_name = "BodyFont"
    doc = build(document([main, sub, body]))
    nodes = {node.title: node for node in doc.document_blocks if node.title}
    assert nodes["2.1 Setup"].parent_id == nodes["2 Methods"].id


def test_transformed_or_outside_page_chart_labels_are_not_headings():
    compressed = block("chart", "Image Input", 300, [22])
    compressed.bbox = compressed.spans[0].bbox = (100, 300, 145, 305)
    outside = block("outside", "Output Embeddings", 350, [22])
    outside.bbox = outside.spans[0].bbox = (800, 350, 980, 372)
    body = block(
        "body", "Ordinary body text provides the baseline for this document.", 400
    )
    doc = document([compressed, outside, body])
    LayoutClassificationStage().run(doc)
    assert compressed.type == outside.type == BlockType.TEXT


def test_chart_dialogue_and_decimal_values_are_not_section_numbering():
    items = []
    for i, text in enumerate(
        [
            "I am thinking of an animal",
            "A novel feature of the model",
            "10 rounds of dialogue are provided",
            "0.261 – 0.559",
        ]
    ):
        item = block(f"label{i}", text, 200 + i * 30, [22])
        item.bbox = item.spans[0].bbox = (100, 200 + i * 30, 300, 205 + i * 30)
        items.append(item)
    doc = document(
        [
            *items,
            block("body", "Normal document body provides a stable font baseline.", 400),
        ]
    )
    LayoutClassificationStage().run(doc)
    assert not any(b.type == BlockType.HEADING for b in doc.pages[0].blocks)


def test_same_row_chart_label_is_not_part_of_section_title():
    mixed = block(
        "mixed",
        "3 Method Tokenizer\nThe method explains how to process the document.",
        200,
        [12, 10],
    )
    mixed.spans = [
        SpanIR(
            id="number", text=TextContent("3"), font_size=12, bbox=(50, 200, 56, 212)
        ),
        SpanIR(
            id="title",
            text=TextContent("Method"),
            font_size=12,
            bbox=(65, 200, 100, 212),
        ),
        SpanIR(
            id="chart",
            text=TextContent("Tokenizer"),
            font_size=18,
            bbox=(160, 201, 190, 206),
        ),
        mixed.spans[-1],
    ]
    doc = build(
        document(
            [
                mixed,
                block(
                    "body", "Additional ordinary body text for the font baseline.", 400
                ),
            ]
        )
    )
    titles = [
        node.title
        for node in doc.document_blocks
        if node.type == DocumentBlockType.SECTION
    ]
    assert (
        "3 Method" in titles
        and "3 Method Tokenizer" not in titles
        and "Tokenizer" not in titles
    )


def test_forced_tree_rebuild_removes_old_generated_relations():
    from documa.pipeline import PipelineContext

    doc = build(
        document(
            [block("a", "Abstract", 150, [12]), block("b", "Some body text.", 190)]
        )
    )
    old_ids = {
        node.id
        for node in doc.document_blocks
        if node.type == DocumentBlockType.SECTION
    }
    doc.pages[0].blocks = [b for b in doc.pages[0].blocks if b.id != "a"]
    BlockTreeBuildingStage().run(
        doc, PipelineContext(settings={"force_rebuild_blocks": True})
    )
    assert not any(
        r.from_id in old_ids or r.to_id in old_ids
        for r in doc.relations
        if r.metadata.get("stage") == "block_tree"
    )
