"""Quality metric tests (Stage 6): TEDS/TEDS-S, reading-order NED, IR diff,
and the quality benchmark mode."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from documa.quality.benchmark import BenchmarkOptions, run_fixture_benchmark
from documa.quality.ir_diff import diff_documents
from documa.quality.metrics_reading_order import reading_order_score
from documa.quality.metrics_table_teds import (
    score_table,
    table_tree_from_html,
    table_tree_from_rows,
    teds,
    tree_size,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestTeds:
    ROWS = [["Region", "Revenue"], ["Asia", "412.5"], ["Europe", "301.2"]]
    HTML = "<table><tr><td>Region</td><td>Revenue</td></tr><tr><td>Asia</td><td>412.5</td></tr><tr><td>Europe</td><td>301.2</td></tr></table>"

    def test_identical_table_scores_one(self):
        result = score_table(self.ROWS, self.HTML)
        assert result == {"teds": 1.0, "teds_s": 1.0}

    def test_content_error_lowers_teds_but_not_teds_s(self):
        rows = copy.deepcopy(self.ROWS)
        rows[1][1] = "999.9"
        result = score_table(rows, self.HTML)
        assert result["teds"] < 1.0
        assert result["teds_s"] == 1.0

    def test_missing_row_lowers_both(self):
        result = score_table(self.ROWS[:2], self.HTML)
        assert result["teds"] < 1.0
        assert result["teds_s"] < 1.0

    def test_none_cells_match_empty_html_cells(self):
        rows = [["A", None], [None, "B"]]
        html = "<table><tr><td>A</td><td></td></tr><tr><td></td><td>B</td></tr></table>"
        assert score_table(rows, html) == {"teds": 1.0, "teds_s": 1.0}

    def test_single_cell_and_empty_tables(self):
        assert score_table([["only"]], "<table><tr><td>only</td></tr></table>")["teds"] == 1.0
        assert teds(table_tree_from_rows([]), table_tree_from_html("<table></table>")) == 1.0

    def test_th_cells_and_tbody_wrappers_are_flattened(self):
        html = "<table><thead><tr><th>Region</th><th>Revenue</th></tr></thead><tbody><tr><td>Asia</td><td>412.5</td></tr><tr><td>Europe</td><td>301.2</td></tr></tbody></table>"
        assert score_table(self.ROWS, html) == {"teds": 1.0, "teds_s": 1.0}

    def test_tree_size_counts_all_nodes(self):
        tree = table_tree_from_rows([["a", "b"]])  # table + tr + 2 td
        assert tree_size(tree) == 4

    def test_completely_different_tables_score_low(self):
        result = score_table([["x"]], self.HTML)
        assert result["teds_s"] < 0.5


class TestReadingOrder:
    def test_perfect_order_scores_one(self):
        gold = ["alpha", "beta", "gamma"]
        actual = ["alpha one", "beta two", "gamma three"]
        assert reading_order_score(gold, actual)["score"] == 1.0

    def test_reversed_order_scores_near_zero(self):
        gold = ["alpha", "beta", "gamma", "delta"]
        actual = ["delta x", "gamma x", "beta x", "alpha x"]
        result = reading_order_score(gold, actual)
        assert result["score"] <= 0.35

    def test_unmatched_prefixes_count_against_score(self):
        gold = ["alpha", "beta", "not-present"]
        actual = ["alpha", "beta"]
        result = reading_order_score(gold, actual)
        assert result["matched"] == 2
        assert result["score"] < 1.0

    def test_empty_gold_is_perfect_by_definition(self):
        assert reading_order_score([], ["anything"])["score"] == 1.0

    def test_matching_is_case_and_whitespace_insensitive(self):
        assert reading_order_score(["Hello  World"], ["hello world again"])["score"] == 1.0


class TestIrDiff:
    def _payload(self):
        return {
            "ir_version": "0.2",
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {"id": "p1_b1", "type": "heading", "text": {"raw_text": "Title"}, "order_index": 1, "bbox": [0, 0, 10, 10]},
                        {"id": "p1_b2", "type": "paragraph", "text": {"raw_text": "Body"}, "order_index": 2, "bbox": [0, 20, 10, 30]},
                    ],
                }
            ],
            "tables": [{"id": "t1", "rows": [["a", "b"], ["c", "d"]]}],
            "chunks": [],
        }

    def test_identical_documents_diff_clean(self):
        result = diff_documents(self._payload(), self._payload())
        assert result["identical"] is True

    def test_detects_text_order_and_cell_changes(self):
        actual = self._payload()
        actual["pages"][0]["blocks"][0]["text"]["raw_text"] = "Changed"
        actual["pages"][0]["blocks"][1]["order_index"] = 9
        actual["tables"][0]["rows"][1][1] = "X"
        result = diff_documents(actual, self._payload())
        assert result["identical"] is False
        assert result["blocks"]["text_changed"][0]["block_id"] == "p1_b1"
        assert result["blocks"]["reordered"][0]["block_id"] == "p1_b2"
        assert result["tables"][0]["cell_diffs"] == [{"row": 1, "col": 1, "actual": "X", "expected": "d"}]

    def test_detects_added_and_missing_blocks(self):
        actual = self._payload()
        actual["pages"][0]["blocks"].append(
            {"id": "p1_b3", "type": "paragraph", "text": {"raw_text": "New"}, "order_index": 3, "bbox": None}
        )
        expected = self._payload()
        expected["pages"][0]["blocks"].append(
            {"id": "p1_b9", "type": "paragraph", "text": {"raw_text": "Gone"}, "order_index": 4, "bbox": None}
        )
        result = diff_documents(actual, expected)
        assert result["blocks"]["added"] == ["p1_b3"]
        assert result["blocks"]["missing"] == ["p1_b9"]

    def test_refuses_major_version_mismatch(self):
        newer = self._payload()
        newer["ir_version"] = "1.0"
        result = diff_documents(newer, self._payload())
        assert result["code"] == "IR_MAJOR_VERSION_MISMATCH"


class TestQualityBenchmark:
    def test_quality_mode_scores_gold_cases(self):
        payload = run_fixture_benchmark(
            BenchmarkOptions(mode="quality", pdf_provider="pymupdf", keyword_provider="ngram")
        )
        assert payload["mode"] == "quality"
        by_id = {case["case_id"]: case for case in payload["cases"]}

        table_case = by_id["table-structure-001"]
        assert table_case["status"] == "passed"
        scores = next(c["details"] for c in table_case["checks"] if c["name"] == "quality_scores")
        for entry in scores.values():
            assert 0.0 <= entry["teds"] <= 1.0
            assert 0.0 <= entry["teds_s"] <= 1.0

        order_case = by_id["reading-order-multicolumn-001"]
        order_scores = next(c["details"] for c in order_case["checks"] if c["name"] == "quality_scores")
        assert 0.0 <= order_scores["reading_order"]["score"] <= 1.0

        # Cases without gold stay in readiness mode.
        readiness_case = by_id["rag-rlm-chunking-001"]
        assert readiness_case["checks"][0] == {"name": "mode", "status": "readiness", "details": {"gold": None}}

    def test_orphan_gold_directory_is_an_error(self, tmp_path):
        gold_dir = tmp_path / "gold"
        (gold_dir / "no-such-case-001").mkdir(parents=True)
        (gold_dir / "no-such-case-001" / "expected.partial.json").write_text("{}", encoding="utf-8")
        payload = run_fixture_benchmark(BenchmarkOptions(mode="quality", gold_dir=gold_dir))
        orphans = [case for case in payload["cases"] if case["case_id"] == "no-such-case-001"]
        assert orphans and orphans[0]["status"] == "error"
        assert payload["status"] == "failed"

    def test_readiness_mode_is_unchanged_by_gold_annotations(self):
        payload = run_fixture_benchmark(BenchmarkOptions())
        assert payload["mode"] == "readiness"
        assert all(
            all(check["name"] != "quality_scores" for check in case["checks"]) for case in payload["cases"]
        )

    def test_gold_files_reference_existing_manifest_cases(self):
        gold_dir = REPO_ROOT / "fixtures" / "pdf" / "gold"
        manifest = json.loads((REPO_ROOT / "fixtures" / "pdf" / "manifest.json").read_text(encoding="utf-8"))
        case_ids = {case["id"] for case in manifest["cases"]}
        for gold_case in gold_dir.iterdir():
            if gold_case.is_dir():
                assert gold_case.name in case_ids
                gold = json.loads((gold_case / "expected.partial.json").read_text(encoding="utf-8"))
                assert gold["case_id"] == gold_case.name
