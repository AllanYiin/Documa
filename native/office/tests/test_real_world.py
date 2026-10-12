from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import pytest
import rust_office
import jsonschema
from scripts.corpus import projection
from scripts.verify_real_world import verify

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path(os.environ.get("DOCUMA_OFFICE_CORPUS_DIR", str(ROOT / ".local-corpus"))).resolve()
CASES = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))["fixtures"] if (CORPUS / "manifest.json").is_file() else []

@pytest.mark.parametrize("case", CASES, ids=lambda c: c["format"])
def test_real_world_golden_and_independent_reader(case):
    path = CORPUS / case["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
    assert case["golden_reviewed"] and case["source"] and case["license"]
    assert (CORPUS / case["permission_file"]).is_file()
    expected = json.loads((CORPUS / case["golden"]).read_text(encoding="utf-8"))
    events = list(rust_office.open(path, extract_images=False))
    validator = jsonschema.Draft202012Validator(rust_office.schema())
    for event in events:
        validator.validate(event)
    assert projection(events) == expected
    assert verify(case)["status"] == "PASS"
