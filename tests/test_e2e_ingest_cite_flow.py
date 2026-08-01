"""End-to-end and boundary tests (Stage 7).

Full flow: ingest -> validate-ir -> search-blocks -> cite-block ->
verify-citations -> delete-document, all addressed by document_id. Plus
registry-scale performance, CLI refusal paths, and failure isolation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from documa.cli import main
from documa.collections.registry import document_id_for_hash, load_registry
from documa.interfaces.tools import (
    cite_block_tool,
    ingest_document_tool,
    load_document,
    search_blocks_tool,
    validate_ir_tool,
    verify_citations_tool,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_REPORT = REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf"
SCANNED_PDF = REPO_ROOT / "fixtures" / "pdf" / "real" / "scanned-note.pdf"

_HAS_OCR = True
try:  # noqa: SIM105
    import rapidocr_onnxruntime  # noqa: F401
except ImportError:
    _HAS_OCR = False


def _run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    exit_code = main(argv)
    output = json.loads(capsys.readouterr().out)
    return exit_code, output


class TestEndToEndFlow:
    def test_ingest_to_citation_to_delete_by_document_id(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        ingested = ingest_document_tool(source=str(ANNUAL_REPORT))
        assert ingested["status"] == "ok"
        document_id = ingested["document_id"]

        validated = validate_ir_tool(ir_path=ingested["ir_path"])
        assert validated["valid"] is True

        found = search_blocks_tool(ir_path=document_id, query="Revenue")
        assert found["status"] == "ok"
        assert found["results"], "expected search hits for 'Revenue'"
        block_id = found["results"][0]["block_id"]

        cited = cite_block_tool(ir_path=document_id, block_id=block_id)
        assert cited["status"] == "ok"
        assert cited["page_label"].startswith("PDF p.")
        assert cited["citation_string"]

        verified = verify_citations_tool(ir_path=document_id, block_ids=[block_id])
        assert verified["overall_valid"] is True

        exit_code, refused = _run_cli(["delete-document", document_id], capsys)
        assert exit_code == 1
        assert refused["status"] == "confirm_required"
        assert Path(ingested["ir_path"]).exists()

        exit_code, removed = _run_cli(["delete-document", document_id, "--yes"], capsys)
        assert exit_code == 0
        assert not Path(ingested["ir_path"]).exists()
        with pytest.raises(FileNotFoundError):
            load_document(document_id)

    @pytest.mark.ocr
    @pytest.mark.skipif(not _HAS_OCR, reason="documa[ocr] extra not installed")
    def test_scanned_pdf_flow_with_ocr(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ingested = ingest_document_tool(source=str(SCANNED_PDF), ocr=True)
        assert ingested["status"] == "ok"

        found = search_blocks_tool(ir_path=ingested["document_id"], query="maintenance")
        assert found["status"] == "ok"
        assert found["results"], "OCR text should be searchable"

        block_id = found["results"][0]["block_id"]
        cited = cite_block_tool(ir_path=ingested["document_id"], block_id=block_id)
        assert cited["status"] == "ok"
        assert cited["grounding"] in {"visual", "logical"}


class TestColumnFlowEndToEnd:
    def test_three_column_ingest_cite_window_evidence_flow(self, tmp_path, monkeypatch):
        from documa.interfaces.answer_support import build_evidence_bundle
        from documa.interfaces.tools import source_window_tool

        monkeypatch.chdir(tmp_path)
        source = REPO_ROOT / "fixtures" / "pdf" / "real" / "three-column-newsletter.pdf"
        ingested = ingest_document_tool(
            source=str(source), pdf_provider="pymupdf", keyword_provider="ngram"
        )
        assert ingested["status"] == "ok"
        document_id = ingested["document_id"]

        found = search_blocks_tool(ir_path=document_id, query="recycled feedstock")
        assert found["results"], "expected a hit in the third column"
        block_id = found["results"][0]["block_id"]

        cited = cite_block_tool(ir_path=document_id, block_id=block_id)
        assert cited["status"] == "ok"
        assert cited["grounding"] == "visual"

        window = source_window_tool(ir_path=document_id, block_id=block_id, before=1, after=1)
        assert window["status"] == "ok"
        offsets = [item["offset"] for item in window["window"]]
        assert offsets == sorted(offsets)

        document = load_document(document_id)
        bundle = build_evidence_bundle(document, [block_id])
        assert bundle.is_complete
        assert bundle.items[0].citation_string

        verified = verify_citations_tool(ir_path=document_id, block_ids=[block_id])
        assert verified["overall_valid"] is True

    def test_column_first_order_survives_the_full_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        source = REPO_ROOT / "fixtures" / "pdf" / "real" / "three-column-newsletter.pdf"
        ingested = ingest_document_tool(
            source=str(source), pdf_provider="pymupdf", keyword_provider="ngram"
        )
        document = load_document(ingested["document_id"])

        page = document.pages[0]
        texts_in_order = [b.text.raw_text for b in page.blocks if b.text]
        col1 = next(i for i, t in enumerate(texts_in_order) if t.startswith("Curing ovens"))
        col2 = next(i for i, t in enumerate(texts_in_order) if t.startswith("Field crews"))
        col3 = next(i for i, t in enumerate(texts_in_order) if t.startswith("The materials lab"))
        assert col1 < col2 < col3


class TestQualityDeterminism:
    def test_quality_benchmark_is_deterministic_across_runs(self, monkeypatch):
        import json as json_module

        from documa.quality.benchmark import BenchmarkOptions, run_fixture_benchmark

        monkeypatch.chdir(REPO_ROOT)
        first = run_fixture_benchmark(BenchmarkOptions(mode="quality"))
        second = run_fixture_benchmark(BenchmarkOptions(mode="quality"))
        assert json_module.dumps(first["summary"], sort_keys=True) == json_module.dumps(
            second["summary"], sort_keys=True
        )
        first_scores = {c["case_id"]: c["checks"] for c in first["cases"]}
        second_scores = {c["case_id"]: c["checks"] for c in second["cases"]}
        assert first_scores == second_scores


class TestStoreCliBoundaries:
    def test_doctor_with_store_dir_reports_health_via_cli(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        ingest_document_tool(source=str(ANNUAL_REPORT))
        exit_code, payload = _run_cli(
            ["doctor", "--no-benchmark", "--project-root", str(REPO_ROOT), "--store-dir", ".documa"],
            capsys,
        )
        assert exit_code == 0
        assert payload["store_health"]["document_count"] == 1

    def test_inspect_store_cli_flags_orphan_dirs(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        ingest_document_tool(source=str(ANNUAL_REPORT))
        (tmp_path / ".documa" / "documents" / "doc-orphan0000000000").mkdir()
        exit_code, payload = _run_cli(["inspect-store"], capsys)
        assert exit_code == 0
        assert payload["status"] == "warning"
        assert payload["orphan_dirs"] == ["doc-orphan0000000000"]


class TestRegistryScale:
    def test_lookup_stays_fast_with_ten_thousand_entries(self, tmp_path):
        store = tmp_path / ".documa"
        store.mkdir()
        documents = [
            {
                "document_id": document_id_for_hash(f"{i:016x}" + "0" * 48),
                "source_path": f"/data/file_{i}.pdf",
                "source_name": f"file_{i}.pdf",
                "content_hash": f"{i:016x}" + "0" * 48,
                "ir_path": f"documents/doc-{i:016x}/documa.ir.json",
                "status": "active",
                "superseded_by": None,
            }
            for i in range(10_000)
        ]
        (store / "registry.json").write_text(
            json.dumps({"registry_version": "1", "documents": documents}), encoding="utf-8"
        )

        started = time.perf_counter()
        registry = load_registry(store)
        target = document_id_for_hash(f"{9_999:016x}" + "0" * 48)
        hit = next(e for e in registry["documents"] if e["document_id"] == target)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert hit["source_name"] == "file_9999.pdf"
        assert elapsed_ms < 200, f"registry lookup took {elapsed_ms:.1f}ms"


class TestFailureIsolation:
    def test_failed_ingest_leaves_registry_untouched(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        good = ingest_document_tool(source=str(ANNUAL_REPORT))
        assert good["status"] == "ok"

        bad = ingest_document_tool(source="missing-file.pdf")
        assert bad["code"] == "SOURCE_NOT_FOUND"

        registry = load_registry(".documa")
        assert len(registry["documents"]) == 1

    def test_unsupported_source_error_does_not_corrupt_registry(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        weird = tmp_path / "binary.xyz"
        weird.write_bytes(b"\x00\x01\x02")
        result = ingest_document_tool(source=str(weird))
        assert result["status"] == "error"
        assert load_registry(".documa")["documents"] == []


class TestCliBoundaries:
    def test_diff_refuses_cross_major_versions(self, tmp_path, capsys):
        v0 = tmp_path / "v0.json"
        v1 = tmp_path / "v1.json"
        v0.write_text(json.dumps({"ir_version": "0.2", "pages": [], "tables": [], "chunks": []}), encoding="utf-8")
        v1.write_text(json.dumps({"ir_version": "1.0", "pages": [], "tables": [], "chunks": []}), encoding="utf-8")
        exit_code, payload = _run_cli(["diff", str(v0), str(v1)], capsys)
        assert exit_code == 1
        assert payload["code"] == "IR_MAJOR_VERSION_MISMATCH"

    def test_ingest_without_source_or_rebuild_flag_errors(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        exit_code, payload = _run_cli(["ingest"], capsys)
        assert exit_code == 1
        assert "source" in payload["message"]

    def test_validate_ir_cli_flags_corrupt_file(self, tmp_path, capsys):
        bad = tmp_path / "bad.ir.json"
        bad.write_text(json.dumps({"ir_version": "0.2"}), encoding="utf-8")
        exit_code, payload = _run_cli(["validate-ir", str(bad)], capsys)
        assert exit_code == 1
        assert payload["status"] == "invalid"

    def test_benchmark_quality_cli_reports_scores(self, capsys, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        exit_code, payload = _run_cli(["benchmark", "--mode", "quality"], capsys)
        assert payload["mode"] == "quality"
        scored = [
            case
            for case in payload["cases"]
            if any(check["name"] == "quality_scores" for check in case["checks"])
        ]
        assert len(scored) >= 2
