"""Citation / provenance tool tests (Stage 3).

Covers the full-scan guarantee (every chunk and document block with page_refs
can be cited), logical-grounding fallback for sources without bboxes, source
windows, citation verification, error codes, and MCP exposure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from documa.interfaces import call_documa_tool, list_documa_tools
from documa.interfaces.citation import (
    cite_block,
    cite_chunk,
    render_citation,
    source_window,
    verify_citations,
)
from documa.interfaces.tools import (
    cite_block_tool,
    load_document,
    process_document_tool,
    verify_citations_tool,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_FIXTURES = [
    REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf",
    REPO_ROOT / "fixtures" / "pdf" / "real" / "two-column-article.pdf",
    REPO_ROOT / "fixtures" / "pdf" / "real" / "mixed-media-brief.pdf",
]


@pytest.fixture(scope="module")
def processed_ir_path(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("citation_ir")
    payload = process_document_tool(source=str(REAL_FIXTURES[0]), out=str(out_dir))
    assert payload["status"] == "ok"
    return Path(payload["output_path"])


@pytest.fixture(scope="module")
def processed_document(processed_ir_path):
    return load_document(processed_ir_path)


class TestFullScanGuarantee:
    @pytest.mark.parametrize("source", REAL_FIXTURES, ids=lambda p: p.stem)
    def test_every_chunk_and_block_with_page_refs_is_citable(self, source, tmp_path):
        payload = process_document_tool(source=str(source), out=str(tmp_path / source.stem))
        assert payload["status"] == "ok"
        document = load_document(payload["output_path"])

        chunks_checked = 0
        for chunk in document.chunks:
            if not chunk.page_refs:
                continue
            result = cite_chunk(document, chunk.id)
            assert result["status"] == "ok", (chunk.id, result)
            assert result["page_label"]
            assert result["citation_string"]
            chunks_checked += 1
        assert chunks_checked > 0

        blocks_checked = 0
        for block in document.document_blocks:
            if not block.page_refs:
                continue
            result = cite_block(document, block.id)
            assert result["status"] == "ok", (block.id, result)
            assert result["grounding"] in {"visual", "logical"}
            blocks_checked += 1
        assert blocks_checked > 0


class TestCiteBlock:
    def test_page_level_block_cites_with_visual_grounding(self, processed_document):
        block = processed_document.pages[0].blocks[0]
        result = cite_block(processed_document, block.id)
        assert result["status"] == "ok"
        assert result["kind"] == "page_block"
        assert result["grounding"] == "visual"
        assert result["bboxes"][0]["page"] == block.page_number
        assert result["bboxes"][0]["x1"] >= result["bboxes"][0]["x0"]
        assert result["excerpt"]
        assert "bbox(" in result["citation_string"]

    def test_markdown_source_falls_back_to_logical_grounding(self, tmp_path):
        md = tmp_path / "note.md"
        md.write_text("# Title\n\nFirst paragraph of the note.\n\n## Section\n\nMore text here.\n", encoding="utf-8")
        payload = process_document_tool(source=str(md), out=str(tmp_path / "out"))
        assert payload["status"] == "ok"
        document = load_document(payload["output_path"])

        block = next(blk for blk in document.document_blocks if blk.page_refs and not blk.bbox_refs)
        result = cite_block(document, block.id)
        assert result["status"] == "ok"
        assert result["grounding"] == "logical"
        assert result["bboxes"] == []
        assert result["citation_string"]

    def test_unknown_block_id_returns_error_code(self, processed_document):
        result = cite_block(processed_document, "blk-does-not-exist")
        assert result["status"] == "error"
        assert result["code"] == "BLOCK_NOT_FOUND"

    def test_unknown_style_is_rejected(self, processed_document):
        block = processed_document.pages[0].blocks[0]
        result = cite_block(processed_document, block.id, style="apa")
        assert result["code"] == "UNSUPPORTED_CITATION_STYLE"

    def test_excerpt_is_bounded(self, processed_document):
        for block in processed_document.document_blocks:
            result = cite_block(processed_document, block.id)
            if result["status"] == "ok" and result["excerpt"]:
                assert len(result["excerpt"]) <= 400


class TestCiteChunk:
    def test_chunk_citation_expands_source_blocks(self, processed_document):
        chunk = next(c for c in processed_document.chunks if c.source_block_ids)
        result = cite_chunk(processed_document, chunk.id)
        assert result["status"] == "ok"
        assert len(result["source_blocks"]) == len(chunk.source_block_ids)
        assert all(source["exists"] for source in result["source_blocks"])

    def test_unknown_chunk_id_returns_error_code(self, processed_document):
        result = cite_chunk(processed_document, "chunk-does-not-exist")
        assert result["code"] == "CHUNK_NOT_FOUND"


class TestRenderCitation:
    def test_styles_produce_distinct_strings(self, processed_document):
        chunk = processed_document.chunks[0]
        rendered = {
            style: render_citation(processed_document, chunk.id, style)["citation_string"]
            for style in ("page-bbox", "markdown", "inline")
        }
        assert rendered["inline"].startswith("(")
        assert rendered["markdown"].startswith("[") and "](#" in rendered["markdown"]
        assert len(set(rendered.values())) == 3

    def test_resolves_block_ids_too(self, processed_document):
        block = processed_document.document_blocks[0]
        result = render_citation(processed_document, block.id, "inline")
        assert result["status"] == "ok"

    def test_unknown_ref_returns_reference_not_found(self, processed_document):
        result = render_citation(processed_document, "nope")
        assert result["code"] == "REFERENCE_NOT_FOUND"


class TestSourceWindow:
    def test_window_offsets_are_centered_on_target(self, processed_document):
        blocks = [blk for page in processed_document.pages for blk in page.blocks if blk.order_index is not None]
        target = blocks[2]
        result = source_window(processed_document, target.id, before=1, after=1)
        assert result["status"] == "ok"
        offsets = [item["offset"] for item in result["window"]]
        assert 0 in offsets
        assert offsets == sorted(offsets)
        assert all(-1 <= offset <= 1 for offset in offsets)
        center = next(item for item in result["window"] if item["offset"] == 0)
        assert center["block_id"] == target.id

    def test_document_block_window_uses_document_block_order(self, processed_document):
        ordered = [blk for blk in processed_document.document_blocks if blk.order_index is not None]
        result = source_window(processed_document, ordered[1].id, before=1, after=1)
        assert result["status"] == "ok"
        assert result["kind"] == "document_block"
        assert len(result["window"]) >= 2


class TestVerifyCitations:
    def test_mixed_ids_report_flags_and_overall_invalid(self, processed_document):
        real_block = processed_document.document_blocks[0]
        result = verify_citations(processed_document, [real_block.id, "blk-9999"])
        by_id = {item["block_id"]: item for item in result["items"]}
        assert by_id[real_block.id]["exists"] is True
        assert by_id["blk-9999"]["exists"] is False
        assert result["overall_valid"] is False

    def test_all_real_ids_with_pages_is_valid(self, processed_document):
        ids = [blk.id for blk in processed_document.document_blocks if blk.page_refs][:3]
        result = verify_citations(processed_document, ids)
        assert result["overall_valid"] is True

    def test_empty_list_is_not_valid(self, processed_document):
        assert verify_citations(processed_document, [])["overall_valid"] is False


class TestToolLayer:
    def test_tool_wrapper_reports_load_failure(self):
        result = cite_block_tool(ir_path="does-not-exist.json", block_id="x")
        assert result["code"] == "IR_LOAD_FAILED"

    def test_verify_citations_tool_accepts_comma_separated_ids(self, processed_ir_path, processed_document):
        block_id = processed_document.document_blocks[0].id
        result = verify_citations_tool(ir_path=str(processed_ir_path), block_ids=f"{block_id},blk-9999")
        assert len(result["items"]) == 2

    def test_new_tools_are_listed_and_callable_via_mcp(self, processed_ir_path, processed_document):
        names = {tool["name"] for tool in list_documa_tools()}
        expected = {
            "documa_cite_block",
            "documa_cite_chunk",
            "documa_render_citation",
            "documa_source_window",
            "documa_verify_citations",
            "documa_validate_ir",
        }
        assert expected <= names

        chunk_id = processed_document.chunks[0].id
        result = call_documa_tool(
            "documa_cite_chunk", {"ir_path": str(processed_ir_path), "chunk_id": chunk_id}
        )
        assert result["isError"] is False
        assert result["structuredContent"]["citation_string"]

    def test_validate_ir_tool_flags_invalid_payload(self, tmp_path):
        bad = tmp_path / "bad.ir.json"
        bad.write_text(json.dumps({"source_name": "x", "ir_version": "0.2"}), encoding="utf-8")
        result = call_documa_tool("documa_validate_ir", {"ir_path": str(bad)})
        assert result["isError"] is False
        assert result["structuredContent"]["status"] == "invalid"
        assert result["structuredContent"]["valid"] is False

    def test_citation_payloads_exclude_timing_diagnostics(self, processed_ir_path, processed_document):
        result = cite_block_tool(
            ir_path=str(processed_ir_path), block_id=processed_document.document_blocks[0].id
        )
        assert result["status"] == "ok"
        assert "timing_ms" not in result
