from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import rust_office


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
CASES = MANIFEST["fixtures"]
POSITIVE_CASES = [case for case in CASES if "expected_error" not in case]
NEGATIVE_CASES = [case for case in CASES if "expected_error" in case]


def events(name: str, **options):
    return list(rust_office.open(FIXTURES / name, **options))


def case_id(case: dict[str, object]) -> str:
    return str(case["path"])


def test_contract_and_capabilities():
    assert rust_office.version_info() == ("0.1.0", "office-layout-v1")
    capabilities = rust_office.capabilities()
    assert capabilities["formats"]["docx"]["supported"] is True
    assert capabilities["formats"]["doc"]["supported"] is False


def test_manifest_release_gate_and_provenance():
    gate = MANIFEST["quality_gate"]
    assert gate == {
        "required_committed_fixtures": 24,
        "current_committed_fixtures": 24,
        "status": "complete",
    }
    assert len(CASES) == 24
    assert len({case["path"] for case in CASES}) == 24
    assert {case["format"] for case in CASES} == {"docx", "xls", "xlsx", "pptx"}
    for case in CASES:
        path = FIXTURES / case["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
        assert case["coverage"]
        assert case["provenance"]
        assert case["license"] == "MIT"


@pytest.mark.parametrize("case", POSITIVE_CASES, ids=case_id)
def test_structured_events_are_searchable(case: dict[str, object]):
    name = str(case["path"])
    payload = events(name)
    assert rust_office.detect_format(FIXTURES / name) == case["format"]
    assert payload[0]["event"] == "document_start"
    assert payload[-1] == {"event": "document_end", "status": "ok"}
    assert case["expected_needle"] in str(payload)


@pytest.mark.parametrize("case", NEGATIVE_CASES, ids=case_id)
def test_security_and_corrupt_fixtures_have_stable_errors(case: dict[str, object]):
    with pytest.raises(ValueError, match=str(case["expected_error"])):
        rust_office.open(FIXTURES / str(case["path"]))


@pytest.mark.parametrize("case", POSITIVE_CASES, ids=case_id)
def test_event_ids_source_refs_and_output_are_deterministic(case: dict[str, object]):
    name = str(case["path"])
    first = events(name)
    second = events(name)
    assert first == second
    identifiers = []
    for event in first:
        if event["event"] == "unit":
            unit = event["unit"]
            identifiers.append(unit["id"])
            for collection in ("blocks", "tables"):
                for item in unit[collection]:
                    identifiers.append(item["id"])
                    assert item["source_refs"]
        elif event["event"] == "asset":
            identifiers.append(event["asset"]["id"])
            assert event["asset"]["source_ref"]
    assert len(identifiers) == len(set(identifiers))


def test_hidden_units_are_opt_in():
    default_payload = events("xlsx-hidden.xlsx")
    expanded_payload = events("xlsx-hidden.xlsx", include_hidden=True)
    assert "隱藏工作表" not in str(default_payload)
    assert "隱藏工作表" in str(expanded_payload)


def unit_for(name: str) -> dict[str, object]:
    return next(event["unit"] for event in events(name) if event["event"] == "unit")


@pytest.mark.parametrize(
    ("name", "address", "data_type", "formula", "value"),
    [
        ("xls-formula.xls", "C2", "string", "A2*B2", ""),
        ("xlsx-unicode-formula.xlsx", "D2", "empty", "B2*C2", None),
    ],
)
def test_formula_cell_contract(
    name: str,
    address: str,
    data_type: str,
    formula: str,
    value: str | None,
):
    unit = unit_for(name)
    records = unit["blocks"][0]["metadata"]["cell_records"]
    record = next(item for item in records if item["address"] == address)
    assert record == {
        "address": address,
        "column": ord(address[0]) - ord("A") + 1,
        "data_type": data_type,
        "formula": formula,
        "row": int(address[1:]),
        "value": value,
    }


def test_xlsx_merged_range_and_pptx_bbox_are_exact():
    worksheet = unit_for("xlsx-merged-link.xlsx")
    assert worksheet["blocks"][0]["metadata"]["merged_ranges"] == ["A1:C1"]

    slide = unit_for("pptx-image-link.pptx")
    link = next(block for block in slide["blocks"] if block["text"] == "外部參考")
    image = next(block for block in slide["blocks"] if block["kind"] == "image")
    assert link["bbox"] == pytest.approx([144.0, 108.0, 288.0, 165.6], abs=0.1)
    assert image["bbox"] == pytest.approx([72.0, 108.0, 108.0, 144.0], abs=0.1)


def test_legacy_format_has_stable_error(tmp_path: Path):
    for suffix in (".doc", ".ppt"):
        path = tmp_path / f"old{suffix}"
        path.write_bytes(b"not a document")
        with pytest.raises(ValueError, match="LEGACY_OFFICE_NOT_SUPPORTED"):
            rust_office.open(path)


def test_macro_and_encrypted_formats_have_stable_errors(tmp_path: Path):
    macro_path = tmp_path / "macro.docm"
    macro_path.write_bytes(b"not a document")
    with pytest.raises(ValueError, match="MACRO_ENABLED_OFFICE_NOT_SUPPORTED"):
        rust_office.open(macro_path)

    encrypted_path = tmp_path / "encrypted.docx"
    encrypted_path.write_bytes(
        bytes.fromhex("D0CF11E0A1B11AE1") + b"encrypted package placeholder"
    )
    with pytest.raises(ValueError, match="ENCRYPTED_OFFICE_NOT_SUPPORTED"):
        rust_office.open(encrypted_path)
