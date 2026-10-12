"""Import explicitly licensed real-world files and check reproducible parser expectations.

Imports are local only. No network fetching, private data discovery, or implied licensing.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CORPUS = Path(os.environ.get("DOCUMA_OFFICE_CORPUS_DIR", str(ROOT / ".local-corpus"))).resolve()
MANIFEST = CORPUS / "manifest.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def projection(events):
    return [
        {
            "label": e["unit"]["label"],
            "hidden": e["unit"]["hidden"],
            "blocks": [{"kind": b["kind"], "text": b["text"], "source_refs": b["source_refs"], "bbox": b["bbox"]} for b in e["unit"]["blocks"]],
            "tables": [{"rows": t["rows"], "source_refs": t["source_refs"]} for t in e["unit"]["tables"]],
        }
        for e in events if e["event"] == "unit"
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("path", type=Path)
    add.add_argument("--license", required=True, help="SPDX ID or an explicit permission identifier")
    add.add_argument("--source", required=True, help="Source URL or auditable local provenance")
    add.add_argument("--permission-file", required=True, type=Path, help="License/permission evidence for the declared usage scope")
    add.add_argument("--reviewed", action="store_true", required=True, help="Confirm you reviewed content and authorization for the declared scope")
    add.add_argument("--scope", choices=["local-testing", "redistribution"], default="local-testing")
    check = commands.add_parser("check")
    check.add_argument("--require-real-world", action="store_true")
    args = parser.parse_args()
    import rust_office
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {"schema_version": 1, "fixtures": []}
    if args.command == "add":
        suffix = args.path.suffix.lower()
        if suffix not in {".docx", ".xls", ".xlsx", ".pptx"}:
            parser.error("Expected DOCX/XLS/XLSX/PPTX")
        if not args.permission_file.is_file():
            parser.error("Permission evidence file is missing")
        sha = digest(args.path)
        target = CORPUS / f"{sha}{suffix}"
        if any(c["sha256"] == sha for c in manifest["fixtures"]):
            parser.error("This document is already in the corpus")
        # Verify parsing before recording a corpus entry. Review the proposed golden separately.
        result = list(rust_office.open(args.path, extract_images=False))
        CORPUS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.path, target)
        permission = CORPUS / f"{sha}.license.txt"
        shutil.copyfile(args.permission_file, permission)
        golden = CORPUS / f"{sha}.golden.json"
        golden.write_text(json.dumps(projection(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["fixtures"].append({"path": target.name, "sha256": sha, "format": suffix[1:], "source": args.source, "license": args.license, "scope": args.scope, "permission_file": permission.name, "golden": golden.name, "golden_reviewed": False})
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Imported {target.name}; inspect the golden against the original and set golden_reviewed=true only after review.")
        return 0
    failures = []
    for case in manifest["fixtures"]:
        path = CORPUS / case["path"]
        if not path.is_file() or digest(path) != case["sha256"]:
            failures.append(f"Hash/missing file: {case['path']}")
            continue
        if not case.get("source") or not case.get("license") or not (CORPUS / case["permission_file"]).is_file():
            failures.append(f"Missing provenance/license: {case['path']}")
        if not case.get("golden_reviewed"):
            failures.append(f"Golden requires review: {case['path']}")
        expected = json.loads((CORPUS / case["golden"]).read_text(encoding="utf-8"))
        actual = projection(list(rust_office.open(path, extract_images=False)))
        if actual != expected:
            failures.append(f"Structural regression: {case['path']}")
    if args.require_real_world and not manifest["fixtures"]:
        failures.append("No authorized real-world files supplied; release gate remains blocked.")
    report = {"fixture_count": len(manifest["fixtures"]), "real_world_ready": bool(manifest["fixtures"]) and not failures, "failures": failures}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
