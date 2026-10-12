"""Fetch pinned external OCR artifacts and verify the CLI against a public scan.

Requires onnx==1.18.0 for the explicit fixed-height recognition model preparation.
Models/runtime stay under target/; no model or DLL is bundled in product packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/ocr/onnx-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def checked_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ValueError("artifact names must be single path components")
    return path


def asset(root: Path, entry: dict, download: bool) -> Path:
    path = checked_path(root, entry["name"])
    if (
        path.is_file()
        and path.stat().st_size == entry["bytes"]
        and sha256(path) == entry["sha256"]
    ):
        return path
    if not download:
        raise ValueError(f"missing or mismatched artifact: {path.name}; run fetch")
    temporary = checked_path(root, entry["name"] + ".part")
    count = 0
    with (
        urllib.request.urlopen(entry["url"], timeout=60) as response,
        temporary.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > entry["bytes"]:
                raise ValueError("download exceeds pinned size")
            output.write(chunk)
    if count != entry["bytes"] or sha256(temporary) != entry["sha256"]:
        raise ValueError(f"download size/hash mismatch: {path.name}")
    # Both checked absolute paths are immediate children of the requested output directory.
    temporary.replace(path)
    return path


def prepare(root: Path, manifest: dict, download: bool) -> dict[str, Path]:
    import onnx

    if onnx.__version__ != manifest["onnx_tool_version"]:
        raise ValueError(
            f"model preparation requires onnx=={manifest['onnx_tool_version']}"
        )
    system = platform.system().lower()
    if system not in manifest["runtime_assets"] or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise ValueError("the pinned runtime fixtures cover Windows/Linux x64 only")
    root.mkdir(parents=True, exist_ok=True)
    paths = [asset(root, entry, download) for entry in manifest["assets"]]
    archive = asset(root, manifest["runtime_assets"][system], download)
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    libraries = []
    if system == "windows":
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if "/lib/" in member.filename and member.filename.endswith(".dll"):
                    path = checked_path(runtime, Path(member.filename).name)
                    path.write_bytes(package.read(member))
                    libraries.append(path)
    else:
        with tarfile.open(archive) as package:
            for member in package.getmembers():
                if member.isfile() and "/lib/" in member.name and ".so" in member.name:
                    path = checked_path(runtime, Path(member.name).name)
                    with package.extractfile(member) as source:
                        path.write_bytes(source.read())
                    libraries.append(path)
    library = next(
        path
        for path in libraries
        if path.name in {"onnxruntime.dll", "libonnxruntime.so.1.27.1"}
    )
    dictionary = checked_path(root, manifest["prepared"]["dictionary"]["name"])
    dictionary.write_bytes(paths[2].read_bytes().rstrip(b"\r\n") + b"\n \n")
    recognition = checked_path(root, manifest["prepared"]["recognition"]["name"])
    model = onnx.load_model_from_string(paths[1].read_bytes())
    model.graph.input[0].type.tensor_type.shape.dim[2].dim_value = 48
    onnx.checker.check_model(model)
    recognition.write_bytes(model.SerializeToString(deterministic=True))
    for key, path in (("recognition", recognition), ("dictionary", dictionary)):
        if sha256(path) != manifest["prepared"][key]["sha256"]:
            raise ValueError(f"prepared artifact hash differs: {key}")
    fixture = MANIFEST.parent / manifest["fixture"]["pdf"]
    if sha256(fixture) != manifest["fixture"]["pdf_sha256"]:
        raise ValueError("public scan fixture hash differs")
    return {
        "detection": paths[0],
        "recognition": recognition,
        "raw_recognition": paths[1],
        "dictionary": dictionary,
        "raw_dictionary": paths[2],
        "runtime": library,
        "fixture": fixture,
    }


def verify(cli: Path, root: Path, manifest: dict, paths: dict[str, Path]) -> dict:
    environment = dict(os.environ, ORT_DYLIB_PATH=str(paths["runtime"]))
    base = [
        str(cli),
        "ocr",
        str(paths["fixture"]),
        "--det-model",
        str(paths["detection"]),
        "--rec-model",
        str(paths["recognition"]),
        "--dictionary",
        str(paths["dictionary"]),
    ]
    outcomes = []

    def run(
        name: str, command: list[str], code: str | None = None, env: dict | None = None
    ) -> dict:
        started = time.perf_counter()
        result = subprocess.run(
            command,
            env=env or environment,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if code is None:
            if result.returncode != 0:
                raise AssertionError(
                    f"{name}: {result.stderr.decode(errors='replace')}"
                )
            value = json.loads(result.stdout)
            text = value["text"]
            blocks = (
                value["pages"][0]["blocks"] if "pages" in value else value["blocks"]
            )
            assert text.strip() == manifest["fixture"]["text"], (name, text)
            assert blocks and all(
                block["confidence"] >= manifest["fixture"]["min_confidence"]
                for block in blocks
            )
            for block in blocks:
                box = block["bbox"]
                assert (
                    0 <= box["x0"] < box["x1"] <= 100
                    and 0 <= box["y0"] < box["y1"] <= 100
                )
        else:
            assert result.returncode != 0, name
            # The stable CLI envelope is the last JSON line; native runtimes may log diagnostics.
            value = json.loads(result.stderr.decode().strip().splitlines()[-1])
            assert value["error"]["code"] == code, (name, value)
        outcomes.append(
            {
                "case": name,
                "passed": True,
                "expected_error": code,
                "seconds": round(time.perf_counter() - started, 4),
            }
        )
        return value

    good = run("recognition_and_layout_mapping", base + ["--json"])
    run("streamed_page_output", base + ["--ndjson"])
    missing_runtime = dict(
        environment, ORT_DYLIB_PATH=str(root / "missing-runtime.dll")
    )
    run(
        "missing_runtime_is_structured", base + ["--json"], "ocr_model", missing_runtime
    )
    mismatch = list(base)
    mismatch[mismatch.index("--dictionary") + 1] = str(paths["raw_dictionary"])
    run("dictionary_class_mismatch", mismatch + ["--json"], "ocr_model")
    dynamic = list(base)
    dynamic[dynamic.index("--rec-model") + 1] = str(paths["raw_recognition"])
    run("dynamic_recognition_height_rejected", dynamic + ["--json"], "ocr_model")
    wrong_model = list(base)
    wrong_model[wrong_model.index("--det-model") + 1] = str(paths["recognition"])
    run("swapped_model_shape_rejected", wrong_model + ["--json"], "ocr_model")
    budget = root / "zero-block-budget.json"
    budget.write_text('{"max_total_blocks":0}', encoding="utf-8")
    run(
        "document_budget",
        base + ["--json", "--ocr-limits", str(budget)],
        "limit_exceeded",
    )
    return {
        "schema_version": 1,
        "status": "PASS",
        "human_gold": False,
        "fixture_text": good["text"],
        "confidence": good["pages"][0]["blocks"][0]["confidence"],
        "runtime_version": manifest["runtime_version"],
        "manifest_sha256": sha256(MANIFEST),
        "platform": platform.system(),
        "cases": outcomes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fetch", "verify"])
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "target/ocr-integration"
    )
    parser.add_argument(
        "--cli",
        type=Path,
        default=ROOT
        / (
            "target/release/rust-pdf.exe"
            if os.name == "nt"
            else "target/release/rust-pdf"
        ),
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = arguments.directory.resolve()
    paths = prepare(root, manifest, arguments.action == "fetch")
    result = (
        {"status": "READY", "manifest_sha256": sha256(MANIFEST)}
        if arguments.action == "fetch"
        else verify(arguments.cli.resolve(), root, manifest, paths)
    )
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
