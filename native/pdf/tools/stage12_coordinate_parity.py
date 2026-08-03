#!/usr/bin/env python3
"""Compare Rust page geometry with PyMuPDF without storing document content."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests" / "fixtures" / "stage12" / "baseline-contract.json"
DEFAULT_OUTPUT = ROOT / "target" / "stage12-coordinate-parity"
TOLERANCE_PT = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=os.getenv("RUST_PDF_STAGE12_CORPUS_DIR"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--rust-cli", type=Path, default=ROOT / "target" / "release" / "rust-pdf.exe")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def size(box: dict[str, Any]) -> tuple[float, float]:
    return float(box["x1"]) - float(box["x0"]), float(box["y1"]) - float(box["y0"])


def close(left: float, right: float, tolerance: float = TOLERANCE_PT) -> bool:
    return abs(left - right) <= tolerance


def self_test() -> None:
    assert size({"x0": 10, "y0": 20, "x1": 110, "y1": 220}) == (100.0, 200.0)
    assert close(100.0, 100.5)
    assert not close(100.0, 100.500_001)
    print("stage12 coordinate parity self-test: ok")


def rust_geometry(rust_cli: Path, pdf_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(rust_cli), "geometry", str(pdf_path), "--json"],
        check=True,
        capture_output=True,
    )
    return json.loads(completed.stdout.decode("utf-8", errors="strict"))


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.corpus_dir is None:
        raise SystemExit("set --corpus-dir or RUST_PDF_STAGE12_CORPUS_DIR")
    if not args.rust_cli.is_file():
        raise FileNotFoundError(args.rust_cli)

    import fitz

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "schema_version": 1,
        "private_corpus": True,
        "contains_extracted_content": False,
        "coordinate_space": "layout_unrotated_top_left",
        "tolerance_pt": TOLERANCE_PT,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pymupdf": getattr(fitz, "__doc__", "").splitlines()[0].strip(),
        },
        "documents": [],
    }
    total_pages = 0
    total_mismatches = 0
    max_layout_width_delta = 0.0
    max_layout_height_delta = 0.0
    max_display_width_delta = 0.0
    max_display_height_delta = 0.0

    for index, case in enumerate(contract["documents"], start=1):
        pdf_path = args.corpus_dir / case["file_name"]
        if pdf_path.stat().st_size != case["bytes"] or digest_file(pdf_path) != case["sha256"]:
            raise ValueError(f"corpus identity mismatch: {case['file_name']}")
        print(f"[{index}/{len(contract['documents'])}] {case['file_name']}", flush=True)
        rust = rust_geometry(args.rust_cli, pdf_path)
        if rust.get("coordinate_space") != "layout_unrotated_top_left":
            raise ValueError(f"Rust coordinate-space mismatch: {case['file_name']}")
        pdf = fitz.open(pdf_path)
        if len(rust["pages"]) != pdf.page_count or pdf.page_count != case["pages"]:
            raise ValueError(f"page-count mismatch: {case['file_name']}")
        mismatches: list[dict[str, Any]] = []
        document_max = {
            "layout_width_delta_pt": 0.0,
            "layout_height_delta_pt": 0.0,
            "display_width_delta_pt": 0.0,
            "display_height_delta_pt": 0.0,
        }
        for page_index, (rust_page, pymupdf_page) in enumerate(zip(rust["pages"], pdf), start=1):
            geometry = rust_page["geometry"]
            if geometry["coordinate_space"] != "layout_unrotated_top_left":
                mismatches.append({"page_number": page_index, "field": "coordinate_space"})
                continue
            rust_layout_width, rust_layout_height = size(geometry["layout_bounds"])
            rust_display_width, rust_display_height = size(geometry["display_bounds"])
            deltas = {
                "layout_width_delta_pt": abs(rust_layout_width - float(pymupdf_page.cropbox.width)),
                "layout_height_delta_pt": abs(rust_layout_height - float(pymupdf_page.cropbox.height)),
                "display_width_delta_pt": abs(rust_display_width - float(pymupdf_page.rect.width)),
                "display_height_delta_pt": abs(rust_display_height - float(pymupdf_page.rect.height)),
            }
            for key, value in deltas.items():
                document_max[key] = max(document_max[key], value)
            rotation_matches = int(geometry["rotation"]) == int(pymupdf_page.rotation)
            dimensions_match = all(value <= TOLERANCE_PT for value in deltas.values())
            if not rotation_matches or not dimensions_match:
                mismatches.append(
                    {
                        "page_number": page_index,
                        "rotation_match": rotation_matches,
                        **{key: round(value, 9) for key, value in deltas.items()},
                    }
                )
        pdf.close()
        total_pages += case["pages"]
        total_mismatches += len(mismatches)
        max_layout_width_delta = max(max_layout_width_delta, document_max["layout_width_delta_pt"])
        max_layout_height_delta = max(max_layout_height_delta, document_max["layout_height_delta_pt"])
        max_display_width_delta = max(max_display_width_delta, document_max["display_width_delta_pt"])
        max_display_height_delta = max(max_display_height_delta, document_max["display_height_delta_pt"])
        report["documents"].append(
            {
                "id": case["id"],
                "file_name": case["file_name"],
                "pages": case["pages"],
                "max_deltas": {key: round(value, 9) for key, value in document_max.items()},
                "mismatch_count": len(mismatches),
                "mismatches": mismatches[:20],
                "mismatch_list_truncated": len(mismatches) > 20,
            }
        )

    report["summary"] = {
        "documents": len(report["documents"]),
        "pages": total_pages,
        "mismatch_count": total_mismatches,
        "max_layout_width_delta_pt": round(max_layout_width_delta, 9),
        "max_layout_height_delta_pt": round(max_layout_height_delta, 9),
        "max_display_width_delta_pt": round(max_display_width_delta, 9),
        "max_display_height_delta_pt": round(max_display_height_delta, 9),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"report: {report_path}")
    if total_mismatches:
        print(f"coordinate parity failed on {total_mismatches} page(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
