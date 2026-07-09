"""IR schema generation, validation, and 0.1 -> 0.2 compatibility tests.

The compatibility contract: minor ir_version bumps are strictly additive, so
a frozen 0.1 payload must keep loading, and every 0.1 field must still exist
in the generated schema.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.core.ir import to_plain_data
from documa.core.schema_validation import (
    MAX_NESTING_DEPTH,
    build_documa_schema,
    validate_document_payload,
)
from documa.interfaces.tools import load_document
from documa.pipeline import run_default_pipeline

REPO_ROOT = Path(__file__).resolve().parent.parent
V01_FIXTURE = REPO_ROOT / "fixtures" / "ir" / "v0_1_document.ir.json"
REAL_PDF = REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf"


def _valid_payload() -> dict:
    document = adapter_for_source(str(REAL_PDF)).parse(str(REAL_PDF), ParseOptions())
    run_default_pipeline(document)
    return to_plain_data(document)


class V01CompatibilityTests(unittest.TestCase):
    def test_v0_1_payload_loads_with_new_fields_defaulting_to_none(self):
        document = load_document(V01_FIXTURE)

        self.assertEqual(document.ir_version, "0.1")
        self.assertIsNone(document.producer_version)
        self.assertIsNone(document.adapter_version)
        self.assertIsNone(document.pipeline_profile)
        self.assertGreater(len(document.chunks), 0)

    def test_v0_1_payload_still_validates_against_current_schema(self):
        payload = json.loads(V01_FIXTURE.read_text(encoding="utf-8"))
        result = validate_document_payload(payload)
        self.assertTrue(result["valid"], result["violations"][:3])

    def test_every_v0_1_top_level_field_still_exists_in_schema(self):
        payload = json.loads(V01_FIXTURE.read_text(encoding="utf-8"))
        schema_properties = set(build_documa_schema()["properties"])
        self.assertLessEqual(set(payload), schema_properties)


class SchemaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_payload = _valid_payload()

    def test_current_pipeline_output_is_valid(self):
        result = validate_document_payload(self.base_payload)
        self.assertTrue(result["valid"], result["violations"][:3])

    def test_corrupt_payloads_are_detected(self):
        corruptions = {
            "missing_required_id": lambda p: p.pop("id"),
            "wrong_type_pages": lambda p: p.__setitem__("pages", {"not": "a list"}),
            "illegal_enum_confidence": lambda p: p["pages"][0]["blocks"][0].__setitem__("confidence", "banana"),
            "negative_page_number": lambda p: p["pages"][0].__setitem__("page_number", -1),
            "inverted_bbox": lambda p: p["pages"][0]["blocks"][0].__setitem__("bbox", [100.0, 100.0, 50.0, 50.0]),
            "unknown_major_version": lambda p: p.__setitem__("ir_version", "9.0"),
        }
        for name, corrupt in corruptions.items():
            with self.subTest(corruption=name):
                payload = copy.deepcopy(self.base_payload)
                corrupt(payload)
                result = validate_document_payload(payload)
                self.assertFalse(result["valid"], f"{name} was not detected")
                self.assertGreater(len(result["violations"]), 0)

    def test_violations_carry_json_pointer_paths(self):
        payload = copy.deepcopy(self.base_payload)
        payload["pages"][0]["page_number"] = -1
        result = validate_document_payload(payload)
        self.assertIn("/pages/0/page_number", [v["path"] for v in result["violations"]])

    def test_depth_limit_rejects_hostile_nesting(self):
        payload: dict = {"id": "x", "source_name": "x", "ir_version": "0.2"}
        node: dict = payload
        for _ in range(MAX_NESTING_DEPTH + 5):
            node["metadata"] = {}
            node = node["metadata"]
        result = validate_document_payload(payload)
        self.assertFalse(result["valid"])
        self.assertIn("nesting depth", result["violations"][0]["message"])

    def test_non_object_payload_is_rejected(self):
        result = validate_document_payload(["not", "an", "object"])
        self.assertFalse(result["valid"])


class SchemaSyncTests(unittest.TestCase):
    def test_committed_schema_matches_generated_schema(self):
        committed = json.loads((REPO_ROOT / "schema" / "documa.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, build_documa_schema())


class ValidateIrCliTests(unittest.TestCase):
    def test_cli_exit_codes(self):
        ok = subprocess.run(
            [sys.executable, "-m", "documa.cli", "validate-ir", str(V01_FIXTURE)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

        missing = subprocess.run(
            [sys.executable, "-m", "documa.cli", "validate-ir", "does-not-exist.json"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(missing.returncode, 1)


if __name__ == "__main__":
    unittest.main()
