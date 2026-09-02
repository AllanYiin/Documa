"""Assemble the locally validated 0.8.0 wheel and host wrappers for handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

from package_plugins import ZIPPED_PLUGINS, build_zip_bytes
from verify_release_artifacts import verify


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(evidence: Path) -> Path:
    dist = ROOT / "dist"
    wheel = dist / "documa-0.8.0-cp310-cp310-win_amd64.whl"
    sdist = dist / "documa-0.8.0.tar.gz"
    verified = verify(wheel, sdist)
    smoke = json.loads((evidence / "mcp-smoke.log").read_text(encoding="utf-8"))
    doctor = json.loads((evidence / "doctor.json").read_text(encoding="utf-8"))
    security = json.loads((evidence / "plugin-security.json").read_text(encoding="utf-8"))
    pytest_log = (evidence / "pytest-final.log").read_text(encoding="utf-8")
    rust_log = (evidence / "lingxi-core-tests.log").read_text(encoding="utf-8")
    assert smoke["status"] == "PASS" and smoke["lingxi"] == "0.4.5" and smoke["external_lingxi"] is False
    assert doctor["summary"]["failed"] == doctor["summary"]["warnings"] == 0
    assert security["decision"] == "PASS"
    assert "failed," not in pytest_log and "443 passed" in pytest_log
    assert "57 passed; 0 failed" in rust_log
    payloads = [wheel]
    for name in ZIPPED_PLUGINS:
        source = ROOT / "plugins" / f"{name}.zip"
        assert source.read_bytes() == build_zip_bytes(ROOT / "plugins" / name), source
        destination = dist / f"{name}-0.8.0.zip"
        shutil.copyfile(source, destination)
        payloads.append(destination)
    npm = dist / "documa-openclaw-documa-0.8.0.tgz"
    with tarfile.open(npm, "r:gz") as archive:
        metadata = json.load(archive.extractfile("package/package.json"))
        assert metadata["version"] == "0.8.0"
        assert "package/index.js" in archive.getnames()
    payloads.append(npm)
    install = dist / "INSTALL-0.8.0.md"
    shutil.copyfile(ROOT / "docs/documa/install-0.8.0.md", install)
    payloads.append(install)
    report = {
        "decision": "PASS", "scope": "local-windows-cp310-candidate",
        "version": "0.8.0", "lingxi": "0.4.5",
        "validation": {
            "archives": verified, "python_tests": re.findall(r"443 passed[^\n]*", pytest_log)[-1],
            "lingxi_core_tests": "57 passed; 0 failed", "doctor": doctor["summary"],
            "mcp": {key: value for key, value in smoke.items() if not key.endswith("_path")},
            "plugin_security_static": security, "plugin_zip_source_match": "PASS",
        },
        "artifacts": [{"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)} for path in [*payloads, sdist]],
        "authorization": "Local packaging and isolated tests only; no publish or global install.",
        "rollback": "Existing 0.7.0 artifacts and global environment retained; no user-store migration.",
        "not_performed": ["Linux/macOS wheels", "four-host live install/reload", "registry publication", "dependency CVE audit"],
        "limits": ["Other Python dependencies are not bundled", "Existing native PDF quality gate remains unresolved"],
    }
    report_path = dist / "documa-0.8.0-release-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payloads.append(report_path)
    sums = "".join(f"{sha256(path)}  {path.name}\n" for path in payloads)
    bundle = dist / "documa-0.8.0-win_amd64-bundle.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        entries = {path.name: path.read_bytes() for path in payloads}
        entries["SHA256SUMS.txt"] = sums.encode("utf-8")
        entries["INSTALL.md"] = entries.pop(install.name)
        # The in-bundle checksum manifest names the files exactly as shipped.
        entries["SHA256SUMS.txt"] = sums.replace(install.name, "INSTALL.md").encode("utf-8")
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    (dist / "documa-0.8.0-SHA256SUMS.txt").write_text(
        sums + f"{sha256(sdist)}  {sdist.name}\n{sha256(bundle)}  {bundle.name}\n", encoding="utf-8",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        for line in archive.read("SHA256SUMS.txt").decode("utf-8").splitlines():
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "build/release-0.8.0")
    print(package(parser.parse_args().evidence))
