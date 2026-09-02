"""Private binding selection, v2 byte spans, and native artifact contracts."""

import json
from pathlib import Path
from runpy import run_path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from documa import SummaryError, SummaryOptions, summarize_document, summarize_text
from documa.adapters.lingxi_binding import lingxi_binding, load_segmenter
from documa.summarization import _LingxiProvider, _lingxi_v2_clauses


ROOT = Path(__file__).resolve().parents[1]


def _block(text, start=0, end=None, **overrides):
    encoded = text.encode("utf-8")
    end = len(encoded) if end is None else end
    value = encoded[start:end].decode("utf-8")
    values = dict(
        index=0, kind="paragraph", decision="select_exact",
        byte_start=start, byte_end=end, source_text=value, output_text=value,
        selected_spans=[(value, start, end, False)], children=[],
        signals=SimpleNamespace(negation_count=1),
        score=SimpleNamespace(final_score=0.8, signal=0.4, novelty=0.5, coverage_gain=0.6),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _v2(*blocks):
    return SimpleNamespace(schema_version=2, blocks=list(blocks))


def test_bundled_binding_precedes_external_distribution():
    module = SimpleNamespace(__version__="0.4.5")
    with patch("documa.adapters.lingxi_binding.import_module", return_value=module) as imports:
        with patch("documa.adapters.lingxi_binding.distribution_version") as external:
            assert lingxi_binding() == (module, "0.4.5")
    imports.assert_called_once_with("documa._vendor.lingxi")
    external.assert_not_called()


def test_source_checkout_can_use_legacy_external_lingxi():
    missing = ModuleNotFoundError(name="documa._vendor.lingxi._core")
    module = SimpleNamespace(__name__="lingxi")
    with patch("documa.adapters.lingxi_binding.import_module", side_effect=[missing, module]):
        with patch("documa.adapters.lingxi_binding.distribution_version", return_value="0.3.0"):
            assert lingxi_binding() == (module, "0.3.0")


@pytest.mark.parametrize("error", [ImportError("DLL failure"), ModuleNotFoundError(name="nested_dependency")])
def test_broken_bundle_does_not_silently_fall_back(error):
    with patch("documa.adapters.lingxi_binding.import_module", side_effect=error):
        with patch("documa.adapters.lingxi_binding.distribution_version") as external:
            with pytest.raises(ImportError):
                lingxi_binding()
    external.assert_not_called()


def test_wrong_compiled_version_is_rejected():
    with patch("documa.adapters.lingxi_binding.import_module", return_value=SimpleNamespace(__version__="0.3.0")):
        with pytest.raises(ImportError, match="0.4.5 required"):
            lingxi_binding()


def test_bundled_model_path_is_explicit_despite_global_environment(monkeypatch):
    monkeypatch.setenv("LINGXI_ASSETS", "unrelated-old-model")
    module = SimpleNamespace(__name__="documa._vendor.lingxi", __file__="private/lingxi/__init__.py")
    with patch.object(module, "load", create=True) as load:
        load_segmenter(module)
    load.assert_called_once_with(asset_dir=Path("private/lingxi/assets"))


def test_v2_uses_utf8_boundaries_and_preserves_score_semantics():
    text = "😀前言\n\n不可改寫原文。"
    start = len("😀前言\n\n".encode("utf-8"))
    rows = _lingxi_v2_clauses(_v2(_block(text, start)), text)
    row, = rows
    assert row.start == 5 and text[row.start:row.end] == row.text == "不可改寫原文。"
    assert row.weight == 0.8 and row.explainability == 0.4
    assert row.provider_schema_version == 2 and row.score_available


def test_v2_deduplicates_children_and_retains_unranked_context():
    text = "# Heading\n\n- item"
    child = _block(text, len("# Heading\n\n"), score=None, decision="preserve_exact")
    parent = _block(text, 0, len("# Heading"), children=[child], score=None, decision="context_only")
    rows = _lingxi_v2_clauses(_v2(parent, child), text)
    assert [row.text for row in rows] == ["# Heading", "- item"]
    assert all(row.weight == 0 and row.score_available is False for row in rows)


def test_v2_omitted_blocks_are_not_evidence():
    assert _lingxi_v2_clauses(_v2(_block("not selected", decision="omit")), "not selected") == []


@pytest.mark.parametrize("spans", [[("中", 1, 3, False)], [("wrong", 0, 3, False)], [("", 0, 0, False)]])
def test_v2_rejects_invalid_or_rewritten_spans(spans):
    with pytest.raises(SummaryError) as exc:
        _lingxi_v2_clauses(_v2(_block("中文", selected_spans=spans)), "中文")
    assert exc.value.code == "SUMMARY_PROVIDER_CONTRACT_MISMATCH"


def test_v2_rejects_unknown_schema():
    with pytest.raises(SummaryError, match="schema v2"):
        _lingxi_v2_clauses(SimpleNamespace(schema_version=3, blocks=[]), "text")


def test_v2_hierarchical_selection_can_span_candidate_separators():
    class Segmenter:
        def extract_summary(self, text, top_k, **options):
            return _v2(_block(text))

    text = "😀不可更改原文。" * 200
    result = summarize_text(
        text, SummaryOptions(top_k=1, max_window_chars=1000),
        provider=_LingxiProvider(Segmenter(), "0.4.5"),
    )
    assert result.window_count > 1
    assert all(text[row.start:row.end] == row.text for row in result.sentences)
    assert "".join(row.text for row in result.sentences) == text


def test_vendored_models_have_approved_hashes():
    run_path(str(ROOT / "native/lingxi/verify.py"))["verify"](ROOT)


def test_model_build_gate_rejects_changed_missing_and_extra_assets(tmp_path):
    verify = run_path(str(ROOT / "native/lingxi/verify.py"))["verify"]
    metadata = tmp_path / "native/lingxi"
    metadata.mkdir(parents=True)
    (metadata / "VENDOR.json").write_text(json.dumps({"models": {"dict.bin": "0" * 64}}), encoding="utf-8")
    assets = tmp_path / "src/documa/_vendor/lingxi/assets"
    assets.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="exactly"):
        verify(tmp_path)
    (assets / "dict.bin").write_bytes(b"wrong model")
    with pytest.raises(RuntimeError, match="SHA-256"):
        verify(tmp_path)
    (assets / "private-corpus.txt").write_text("must not ship", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly"):
        verify(tmp_path)


def test_packaging_includes_private_extension_and_not_public_dependency():
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"documa._vendor.lingxi" = ["assets/*.bin"]' in source
    assert '"native/lingxi/LICENSE"' in source
    assert '"native/lingxi/ASSETS.md"' in source
    assert '"lingxi>=' not in source and '"lingxi==' not in source
    assert '"documa._vendor.lingxi._core"' in (ROOT / "setup.py").read_text(encoding="utf-8")


def test_real_bundled_summary_keywords_and_document_evidence():
    pytest.importorskip("documa._vendor.lingxi._core")
    from documa.pipeline.block_keywords import _load_lingxi_segmenter
    from tests.test_summarization import _document

    segmenter, version = _load_lingxi_segmenter()
    assert version == "0.4.5"
    assert segmenter.extract_keywords("文件理解保留來源。人工智慧協助文件檢索。", 5)
    text = "😀第一章說明文件理解。\n\n本系統不得改寫原文。\n\n- 2026年保留全部來源。"
    result = summarize_text(text)
    assert result.provider_version == "0.4.5" and result.sentences
    assert all(text[row.start:row.end] == row.text for row in result.sentences)
    assert not result.uses_llm and result.llm_tokens_used == 0
    document = _document()
    result = summarize_document(document, scope_block_id="db_doc_sec2")
    assert result.sentences
    assert all(row.source_block_ids == ["source-2"] and row.page_refs == [2] for row in result.sentences)


def test_real_bundled_summary_long_unicode_text():
    pytest.importorskip("documa._vendor.lingxi._core")
    text = "\n\n".join(f"😀第{index}項，原始資料不得改寫，必須保留來源。" for index in range(80))
    result = summarize_text(text, SummaryOptions(top_k=2, max_window_chars=1000))
    assert result.strategy == "hierarchical_windows" and result.sentences
    assert all(text[row.start:row.end] == row.text for row in result.sentences)
