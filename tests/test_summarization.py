from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from documa import SummaryError, SummaryOptions, summarize_document, summarize_text
from documa.cli import main
from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    PageIR,
    TextContent,
    to_plain_data,
)
from documa.interfaces import call_documa_tool
from documa.interfaces import token_counting
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.summarization import load_lingxi_summary_provider


@dataclass
class _Row:
    text: str
    start: int
    end: int
    index: int
    clause_index: int
    weight: float
    explainability: float
    novelty: float
    coverage_gain: float
    proper_noun_count: int = 0
    negation_count: int = 0
    emphasis_count: int = 0
    list_item: bool = False
    object_name_count: int = 0
    date_count: int = 0
    number_count: int = 0
    quantity_count: int = 0
    acronym_count: int = 0


class _FakeLingxi:
    name = "lingxi"
    version = "0.3.0-test"

    def extract_summary(self, text: str, top_k: int, **options):
        assert options["similarity"] in {"bm25", "lexical"}
        spans = []
        start = 0
        for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
            value = match.group(0)
            if not value.strip():
                continue
            start = match.start()
            spans.append(
                _Row(
                    text=value,
                    start=start,
                    end=match.end(),
                    index=len(spans),
                    clause_index=len(spans),
                    weight=1.0 / (len(spans) + 1),
                    explainability=0.9,
                    novelty=1.0,
                    coverage_gain=0.5,
                    number_count=int(any(char.isdigit() for char in value)),
                )
            )
        return spans[:top_k]


class _CharCounter:
    name = "test:characters"

    def count(self, text: str) -> int:
        return len(text)

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        return text[:max_tokens], len(text) > max_tokens


def _document() -> DocumentIR:
    first = BlockIR(
        id="source-1",
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent("第一章說明本地摘要。這句不會改寫原文。"),
        order_index=1,
    )
    second = BlockIR(
        id="source-2",
        type=BlockType.PARAGRAPH,
        page_number=2,
        text=TextContent("第二章保留2026年數字事實。最後一句提供結論。"),
        order_index=2,
    )
    root = DocumentBlockIR(
        id="db_doc_root",
        type=DocumentBlockType.DOCUMENT,
        title="測試報告",
        child_ids=["db_doc_sec1", "db_doc_sec2"],
        order_index=0,
    )
    section_one = DocumentBlockIR(
        id="db_doc_sec1",
        type=DocumentBlockType.SECTION,
        title="第一章",
        parent_id=root.id,
        child_ids=["db_doc_leaf1"],
        order_index=1,
    )
    leaf_one = DocumentBlockIR(
        id="db_doc_leaf1",
        type=DocumentBlockType.PARAGRAPH,
        parent_id=section_one.id,
        source_block_ids=[first.id],
        page_refs=[1],
        order_index=2,
    )
    section_two = DocumentBlockIR(
        id="db_doc_sec2",
        type=DocumentBlockType.SECTION,
        title="第二章",
        parent_id=root.id,
        child_ids=["db_doc_leaf2"],
        order_index=3,
    )
    leaf_two = DocumentBlockIR(
        id="db_doc_leaf2",
        type=DocumentBlockType.PARAGRAPH,
        parent_id=section_two.id,
        source_block_ids=[second.id],
        page_refs=[2],
        order_index=4,
    )
    return DocumentIR(
        id="doc",
        source_name="report.pdf",
        pages=[
            PageIR(id="page-1", page_number=1, width=400, height=600, blocks=[first]),
            PageIR(id="page-2", page_number=2, width=400, height=600, blocks=[second]),
        ],
        document_blocks=[root, section_one, leaf_one, section_two, leaf_two],
    )


def test_plain_text_summary_is_source_preserving_and_zero_llm_tokens():
    text = "第一句包含事實。第二句提供補充。第三句形成結論。"
    result = summarize_text(
        text,
        SummaryOptions(top_k=2, min_sentence_chars=1),
        provider=_FakeLingxi(),
        token_counter=_CharCounter(),
    )

    assert result.summary == "第一句包含事實。\n第二句提供補充。"
    assert all(text[item.start : item.end] == item.text for item in result.sentences)
    assert result.extractive is True
    assert result.uses_llm is False
    assert result.llm_tokens_used == 0
    assert result.input_tokens == len(text)
    assert result.tokens_saved == len(text) - len(result.summary)


def test_document_summary_maps_scope_to_block_and_page_evidence():
    result = summarize_document(
        _document(),
        SummaryOptions(top_k=1, min_sentence_chars=1),
        scope_block_id="db_doc_sec2",
        provider=_FakeLingxi(),
    )

    assert result.document_id == "doc"
    assert result.scope_block_id == "db_doc_sec2"
    assert result.sentences[0].text == "第二章保留2026年數字事實。"
    assert result.sentences[0].block_ids == ["db_doc_leaf2"]
    assert result.sentences[0].source_block_ids == ["source-2"]
    assert result.sentences[0].page_refs == [2]
    assert result.sentences[0].page == "PDF p.2"


def test_document_summary_explicitly_preserves_raw_or_normalized_text():
    document = _document()
    document.pages[1].blocks[0].text = TextContent(
        "第二章保留2026年數字事實。最後一句提供結論。",
        normalized_text="第二章保留2026年數字事實。最後一句提供結論。",
    )
    raw = summarize_document(
        document,
        SummaryOptions(top_k=1, min_sentence_chars=1, text_form="raw"),
        scope_block_id="db_doc_sec2",
        provider=_FakeLingxi(),
    )
    normalized = summarize_document(
        document,
        SummaryOptions(top_k=1, min_sentence_chars=1, text_form="normalized"),
        scope_block_id="db_doc_sec2",
        provider=_FakeLingxi(),
    )

    assert raw.text_form == "raw" and "數字" in raw.summary
    assert normalized.text_form == "normalized" and "數字" in normalized.summary
    assert raw.offset_space == normalized.offset_space == "summary_input_unicode_codepoint"


def test_long_text_uses_hierarchical_windows_without_losing_source_offsets():
    text = "".join(f"第{index}句保留完整原文。" for index in range(220))
    result = summarize_text(
        text,
        SummaryOptions(top_k=3, min_sentence_chars=1, max_window_chars=1000),
        provider=_FakeLingxi(),
    )

    assert result.strategy == "hierarchical_windows"
    assert result.window_count > 1
    assert result.selection_count == 3
    assert all(text[item.start : item.end] == item.text for item in result.sentences)


def test_lingxi_summary_loader_rejects_pre_summary_binding():
    load_lingxi_summary_provider.cache_clear()
    with patch("documa.summarization.distribution_version", return_value="0.2.1"):
        with pytest.raises(SummaryError, match="LingXi >= 0.3.0") as exc_info:
            load_lingxi_summary_provider()
    assert exc_info.value.code == "SUMMARY_PROVIDER_VERSION_UNSUPPORTED"
    load_lingxi_summary_provider.cache_clear()


def test_tool_cli_schema_and_short_block_contract(tmp_path: Path, capsys):
    ir_path = tmp_path / "documa.ir.json"
    ir_path.write_text(json.dumps(to_plain_data(_document()), ensure_ascii=False), encoding="utf-8")
    token_counting.set_token_counter(_CharCounter())
    try:
        with patch("documa.summarization.load_lingxi_summary_provider", return_value=_FakeLingxi()):
            called = call_documa_tool(
                "documa_summarize",
                {"ir_path": str(ir_path), "scope_block_id": "sec2", "top_k": 1},
            )
            exit_code = main(["summarize", str(ir_path), "--scope-block-id", "sec2", "--top-k", "1"])
    finally:
        token_counting.reset_token_counter()

    payload = called["structuredContent"]
    assert called["isError"] is False
    assert payload["block_id_prefix"] == "db_doc_"
    assert payload["scope_block_id"] == "sec2"
    assert payload["sentences"][0]["block_ids"] == ["leaf2"]
    assert payload["uses_llm"] is False and payload["llm_tokens_used"] == 0
    assert payload["text_form"] == "normalized"
    assert payload["token_counter"] == "test:characters"
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["summary"] == payload["summary"]

    schemas = {item["name"]: item for item in documa_tool_schemas(profile="agent")}
    assert "documa_summarize" in schemas
    assert schemas["documa_summarize"]["annotations"]["readOnlyHint"] is True
