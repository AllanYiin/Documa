from unittest.mock import patch

import pytest

from documa.core.ir import BlockIR, BlockType, DocumentBlockIR, DocumentBlockType, DocumentIR, PageIR, TextContent
from documa.pipeline import BlockKeywordExtractionStage, PipelineContext
from documa.pipeline.block_keywords import _load_lingxi_segmenter
from documa.search.sidecar import source_digest


class _FakeLingxi:
    def extract_keywords(self, text, top_k, allow_tags):
        assert allow_tags is None
        return [("人工智慧", 1.0), ("文件理解", 0.8), ("台灣", 0.5)][:top_k]


def _document():
    source = BlockIR(
        id="source-1",
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent("人工智慧改善文件理解。人工智慧協助台灣文件檢索。"),
    )
    leaf = DocumentBlockIR(
        id="leaf-1",
        type=DocumentBlockType.PARAGRAPH,
        source_block_ids=["source-1"],
        parent_id="root",
    )
    root = DocumentBlockIR(
        id="root",
        type=DocumentBlockType.DOCUMENT,
        child_ids=["leaf-1"],
    )
    return DocumentIR(
        id="doc",
        source_name="fixture",
        pages=[
            PageIR(
                id="page-1",
                page_number=1,
                width=400.0,
                height=600.0,
                blocks=[source],
            )
        ],
        document_blocks=[root, leaf],
    )


def test_lingxi_is_default_and_preserves_ngram_new_word_metadata():
    _load_lingxi_segmenter.cache_clear()
    with patch("documa.pipeline.block_keywords._load_lingxi_segmenter", return_value=_FakeLingxi()):
        document = _document()
        result = BlockKeywordExtractionStage().run(document)
    leaf = next(block for block in document.document_blocks if block.id == "leaf-1")
    root = next(block for block in document.document_blocks if block.id == "root")
    assert leaf.metadata["keyword_provider"] == "lingxi"
    assert root.metadata["keyword_provider"] == "ngram"
    assert leaf.metadata["keyword_terms"][:2] == ["人工智慧", "文件理解"]
    assert isinstance(leaf.metadata["new_word_terms"], list)
    assert result.report["keyword_provider_requested"] == "lingxi"
    assert result.report["new_word_provider"] == "ngram_boundary_entropy"


def test_missing_lingxi_falls_back_to_ngram_and_reports_reason():
    _load_lingxi_segmenter.cache_clear()
    with patch("documa.pipeline.block_keywords._load_lingxi_segmenter", side_effect=ImportError("missing")):
        document = _document()
        result = BlockKeywordExtractionStage().run(
            document,
            PipelineContext(settings={"keyword_provider": "lingxi"}),
        )
    leaf = next(block for block in document.document_blocks if block.id == "leaf-1")
    assert leaf.metadata["keyword_provider"] == "ngram"
    assert leaf.metadata["keyword_provider_fallback"].startswith("ImportError:")
    assert result.report["keyword_provider_fallback"].startswith("ImportError:")


def test_lingxi_loader_requires_0_2_0_distribution():
    _load_lingxi_segmenter.cache_clear()
    with patch("documa.pipeline.block_keywords.distribution_version", return_value="0.1.0"):
        with pytest.raises(ImportError, match="LingXi 0.2.0 is required; found 0.1.0"):
            _load_lingxi_segmenter()
    _load_lingxi_segmenter.cache_clear()


def test_explicit_ngram_remains_available_for_rollback():
    document = _document()
    result = BlockKeywordExtractionStage().run(
        document,
        PipelineContext(settings={"keyword_provider": "ngram"}),
    )
    assert result.report["keyword_provider_counts"]["ngram"] == 2
    assert all(block.metadata["keyword_provider"] == "ngram" for block in document.document_blocks)


def test_sidecar_digest_changes_with_keyword_provider_and_terms():
    lingxi_document = _document()
    with patch("documa.pipeline.block_keywords._load_lingxi_segmenter", return_value=_FakeLingxi()):
        BlockKeywordExtractionStage().run(lingxi_document)
    ngram_document = _document()
    BlockKeywordExtractionStage().run(
        ngram_document,
        PipelineContext(settings={"keyword_provider": "ngram"}),
    )
    assert source_digest(lingxi_document) != source_digest(ngram_document)
