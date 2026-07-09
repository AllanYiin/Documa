"""R-Stage 3 metric tests: span-aware TEDS, relation F1, layout roles, OCR recall,
and the quality-mode gold-field handling in the benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import documa.quality.benchmark as benchmark_module
from documa.quality.benchmark import BenchmarkOptions, run_fixture_benchmark
from documa.quality.metrics_layout_roles import header_footer_role_score, ocr_text_recall
from documa.quality.metrics_relations import relation_link_score
from documa.quality.metrics_table_teds import score_table, table_tree_from_html, tree_size

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSpanAwareTeds:
    MERGED_HTML = (
        '<table><tr><td>Zone</td><td colspan="2">Checks (passed / failed)</td><td>Status</td></tr>'
        "<tr><td>Zone A</td><td>58</td><td>2</td><td>Open</td></tr></table>"
    )

    def test_colspan_expands_to_extractor_grid_convention(self):
        tree = table_tree_from_html(self.MERGED_HTML)
        header = [td.text for td in tree.children[0].children]
        assert header == ["Zone", "Checks (passed / failed)", "", "Status"]

    def test_merged_cell_grid_scores_one_against_span_gold(self):
        rows = [
            ["Zone", "Checks (passed / failed)", None, "Status"],
            ["Zone A", "58", "2", "Open"],
        ]
        assert score_table(rows, self.MERGED_HTML) == {"teds": 1.0, "teds_s": 1.0}

    def test_rowspan_expands_downward(self):
        html = '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
        tree = table_tree_from_html(html)
        assert [td.text for td in tree.children[0].children] == ["A", "B"]
        assert [td.text for td in tree.children[1].children] == ["", "C"]
        assert tree_size(tree) == 7

    def test_real_merged_cells_fixture_matches_span_gold(self):
        from documa.interfaces.tools import process_document_tool

        payload = process_document_tool(source=str(REPO_ROOT / "fixtures/pdf/real/merged-cells-report.pdf"))
        rows = payload["document"]["tables"][0]["rows"]
        html = (
            '<table><tr><td>Zone</td><td colspan="2">Checks (passed / failed)</td><td>Status</td></tr>'
            "<tr><td>Zone A</td><td>58</td><td>2</td><td>Open</td></tr>"
            "<tr><td>Zone B</td><td>61</td><td>0</td><td>Closed</td></tr>"
            "<tr><td>Zone C</td><td>44</td><td>5</td><td>Open</td></tr></table>"
        )
        assert score_table(rows, html) == {"teds": 1.0, "teds_s": 1.0}


class TestRelationScore:
    def _document(self):
        return {
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {"id": "b1", "text": {"raw_text": "Revenue grew strongly"}},
                        {"id": "b2", "text": {"raw_text": "1 Revenue figures are unaudited"}},
                        {"id": "b3", "text": {"raw_text": "Image 1: Thermal distribution"}},
                    ],
                    "images": [{"id": "img1"}],
                }
            ],
            "relations": [
                {"type": "footnote_marker_to_body", "from_id": "b1", "to_id": "b2"},
                {"type": "caption_to_image", "from_id": "b3", "to_id": "img1"},
            ],
        }

    def test_reflexive_gold_scores_one(self):
        gold = [
            {"type": "footnote_marker_to_body", "from_text": "Revenue grew", "to_text": "1 Revenue figures"},
            {"type": "caption_to_image", "from_text": "Image 1:", "to_image_on_page": 1},
        ]
        result = relation_link_score(self._document(), gold)
        assert result["f1"] == 1.0 and result["missing"] == []

    def test_missing_relation_lowers_recall(self):
        document = self._document()
        document["relations"] = document["relations"][:1]  # drop the caption link
        gold = [
            {"type": "footnote_marker_to_body", "from_text": "Revenue grew", "to_text": "1 Revenue figures"},
            {"type": "caption_to_image", "from_text": "Image 1:", "to_image_on_page": 1},
        ]
        result = relation_link_score(document, gold)
        assert result["recall"] == 0.5
        assert len(result["missing"]) == 1

    def test_spurious_relation_lowers_precision(self):
        document = self._document()
        document["relations"].append({"type": "footnote_marker_to_body", "from_id": "b3", "to_id": "b1"})
        gold = [{"type": "footnote_marker_to_body", "from_text": "Revenue grew", "to_text": "1 Revenue figures"}]
        result = relation_link_score(document, gold)
        assert result["recall"] == 1.0
        assert result["precision"] == 0.5
        assert result["spurious"] == 1

    def test_empty_gold_is_perfect(self):
        assert relation_link_score(self._document(), [])["f1"] == 1.0


class TestLayoutRoles:
    def _document(self):
        return {
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {"id": "h", "type": "page_header", "text": {"raw_text": "Aurora Materials — Confidential"}},
                        {"id": "b", "type": "paragraph", "text": {"raw_text": "Body text here"}},
                        {"id": "f", "type": "text", "text": {"raw_text": "1"}},
                    ],
                    "images": [{"id": "img1", "metadata": {"ocr_text": "MAINTENANCE NOTICE"}}],
                }
            ]
        }

    def test_correctly_typed_header_scores(self):
        result = header_footer_role_score(self._document(), ["Aurora Materials"])
        assert result["score"] == 1.0

    def test_untyped_footer_lowers_score(self):
        result = header_footer_role_score(self._document(), ["Aurora Materials", "1"])
        assert result["score"] == 0.5
        assert result["correctly_typed"] == 1

    def test_unmatched_prefix_counts_as_miss(self):
        result = header_footer_role_score(self._document(), ["No such text"])
        assert result["score"] == 0.0
        assert result["unmatched_prefixes"] == ["No such text"]

    def test_ocr_recall_reads_blocks_and_image_metadata(self):
        result = ocr_text_recall(self._document(), ["maintenance notice", "body text"])
        assert result["score"] == 1.0
        missing = ocr_text_recall(self._document(), ["not present"])
        assert missing["score"] == 0.0 and missing["missing"] == ["not present"]


class TestBenchmarkGoldFields:
    def _gold_setup(self, tmp_path, gold: dict, case_id: str = "table-structure-001") -> BenchmarkOptions:
        gold_dir = tmp_path / "gold"
        case_dir = gold_dir / case_id
        case_dir.mkdir(parents=True)
        (case_dir / "expected.partial.json").write_text(json.dumps(gold), encoding="utf-8")
        return BenchmarkOptions(mode="quality", gold_dir=gold_dir)

    def test_invalid_threshold_is_a_case_error(self, tmp_path):
        options = self._gold_setup(tmp_path, {"threshold": 1.5, "reading_order": ["x"]})
        payload = run_fixture_benchmark(options)
        case = next(c for c in payload["cases"] if c["case_id"] == "table-structure-001")
        assert case["status"] == "error"
        assert "threshold" in case["message"]

    def test_per_case_threshold_override_applies(self, tmp_path):
        # An absurd anchor gives score 0; threshold 0 still passes the case.
        options = self._gold_setup(tmp_path, {"threshold": 0.0, "reading_order": ["zz-no-such-anchor"]})
        payload = run_fixture_benchmark(options)
        case = next(c for c in payload["cases"] if c["case_id"] == "table-structure-001")
        assert case["status"] == "passed"

    def test_ocr_gold_without_extra_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(benchmark_module, "_ocr_extra_available", lambda: False)
        options = self._gold_setup(tmp_path, {"ocr_expected_texts": ["MAINTENANCE"]}, case_id="ocr-scanned-001")
        payload = run_fixture_benchmark(options)
        case = next(c for c in payload["cases"] if c["case_id"] == "ocr-scanned-001")
        assert case["status"] == "skipped"
        assert payload["summary"]["skipped"] >= 1

    def test_quality_summary_reports_fallback_ratio(self, tmp_path):
        options = self._gold_setup(tmp_path, {"reading_order": ["Aurora Materials Annual Report"]})
        payload = run_fixture_benchmark(options)
        assert "fallback_block_ratio_max" in payload["summary"]
        assert 0.0 <= payload["summary"]["fallback_block_ratio_max"] <= 1.0


if __name__ == "__main__":
    import unittest

    unittest.main()
