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
        block_id = found["results"][0]["id"]

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

        block_id = found["results"][0]["id"]
        cited = cite_block_tool(ir_path=ingested["document_id"], block_id=block_id)
        assert cited["status"] == "ok"
        assert cited["grounding"] in {"visual", "logical"}


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
