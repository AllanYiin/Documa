"""Golden-file regression tests over the full pipeline IR output.

Each real fixture PDF is parsed and run through the default pipeline; the
serialized IR is normalized (random document id, path separators, float
rounding) and compared against a committed snapshot. Regenerate snapshots
with ``pytest --force-regen`` only for intended output changes, and say so in
the commit message.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from documa.adapters.base import ParseOptions
from documa.adapters.registry import adapter_for_source
from documa.core.ir import to_plain_data
from documa.pipeline import run_default_pipeline

REAL_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "pdf" / "real"
REAL_FIXTURE_NAMES = ["annual-report", "two-column-article", "mixed-media-brief"]


def _round_floats(value, digits: int = 2):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: _round_floats(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item, digits) for item in value]
    return value


def normalize_ir_payload(payload: dict) -> dict:
    """Strip nondeterminism so snapshots are stable across runs and platforms.

    - The random per-run document id (``doc_<uuid>``) also appears inside
      chunk and document-block ids, so it is replaced globally.
    - ``source_name`` keeps only the posix-style relative fixture path
      (``<parent_dir>/<filename>``).  The same path also appears verbatim
      inside block ``title`` and ``search_terms`` fields, so it is replaced
      everywhere using a recursive walk after JSON-decode.
    - Floats are rounded to 2 decimals to absorb geometry jitter across
      PyMuPDF builds.
    """
    # Normalise both forward- and back-slash separators so the result is
    # identical on Windows and Linux runners.
    posix_src = payload["source_name"].replace("\\", "/")
    # Keep only the last two parts of the path (fixture parent dir + filename)
    # so that snapshots are stable regardless of where the repo is checked out.
    src_parts = PurePosixPath(posix_src).parts
    normalized_source = "/".join(src_parts[-2:])

    # Replace the document id globally via the JSON text (it has no special
    # JSON characters, so a plain str.replace is safe).
    text = json.dumps(payload, ensure_ascii=False)
    text = text.replace(payload["id"], "doc_snapshot")
    data = json.loads(text)

    # Replace the absolute source path wherever it appears in string values
    # (block title, search_terms, …).  We normalise backslashes in each
    # candidate string before comparing so both slash styles are caught.
    def _replace_source_path(value: object) -> object:
        """Recursively replace the absolute source path with its normalized form.

        Compares after normalising backslashes so Windows and Linux paths are
        both caught.
        """
        if isinstance(value, str):
            norm = value.replace("\\", "/")
            if posix_src in norm:
                return norm.replace(posix_src, normalized_source)
            return value
        if isinstance(value, dict):
            return {k: _replace_source_path(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_replace_source_path(item) for item in value]
        return value

    data = _replace_source_path(data)
    data["source_name"] = normalized_source
    return _round_floats(data)


@pytest.mark.parametrize("fixture_name", REAL_FIXTURE_NAMES)
def test_pipeline_ir_matches_snapshot(fixture_name, tmp_path, data_regression):
    source = REAL_FIXTURES_DIR / f"{fixture_name}.pdf"
    document = adapter_for_source(str(source)).parse(
        str(source), ParseOptions(asset_dir=tmp_path / "assets")
    )
    run_default_pipeline(document)

    payload = normalize_ir_payload(to_plain_data(document))
    data_regression.check(payload, basename=f"ir_{fixture_name}")


def test_normalize_replaces_document_id_everywhere():
    payload = {
        "id": "doc_abc123",
        "source_name": "fixtures\\pdf\\real\\x.pdf",
        "chunks": [{"id": "chunk_doc_abc123_0001", "score": 1.23456}],
    }
    normalized = normalize_ir_payload(payload)
    assert normalized["id"] == "doc_snapshot"
    assert normalized["chunks"][0]["id"] == "chunk_doc_snapshot_0001"
    assert normalized["source_name"] == "real/x.pdf"
    assert normalized["chunks"][0]["score"] == 1.23
