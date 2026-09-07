"""Package host wrappers and runtimes after checking installed native hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

try:
    from .package_plugins import ZIPPED_PLUGINS, build_zip_bytes
    from .verify_release_artifacts import release_version, verify
except ImportError:  # Direct script execution.
    from package_plugins import ZIPPED_PLUGINS, build_zip_bytes
    from verify_release_artifacts import release_version, verify

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {"codex-documa": "codex", "claude-code-documa": "claude-code", "hermes-documa": "hermes"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installed_evidence(wheel: Path, smoke: dict) -> None:
    assert smoke["status"] == "PASS" and smoke["documa"] == release_version()
    assert smoke["lingxi"] == "0.4.5" and smoke["external_lingxi"] is False
    assert smoke["rust_pdf_spacing"] == "PASS"
    with zipfile.ZipFile(wheel) as archive:
        for module, digest in smoke["native_sha256"].items():
            prefix = module.replace(".", "/")
            name, = [n for n in archive.namelist() if n.startswith(prefix) and n.endswith((".so", ".pyd"))]
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest, (wheel, module)
    assert set(smoke["native_sha256"]) == {"rust_pdf._native", "rust_office._core", "documa._vendor.lingxi._core"}


def bundle_zip(path: Path, root: Path, payloads: list[Path]) -> None:
    sums = "".join(f"{sha256(p)}  {p.relative_to(root).as_posix()}\n" for p in sorted(payloads))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for source in sorted(payloads):
            info = zipfile.ZipInfo(source.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o644 << 16
            with source.open("rb") as stream, archive.open(info, "w") as target:
                shutil.copyfileobj(stream, target)
        info = zipfile.ZipInfo("SHA256SUMS.txt", date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o644 << 16
        archive.writestr(info, sums)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for line in archive.read("SHA256SUMS.txt").decode().splitlines():
            expected, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected


def package(artifacts: Path, evidence: Path, output: Path) -> Path:
    version = release_version()
    gates = json.loads((evidence / "gates.json").read_text(encoding="utf-8-sig"))
    assert gates["version"] == version
    for key in ("python_tests", "rust_tests", "plugins", "security", "doctor"):
        assert gates[key]["status"] == "PASS", key
    sdist = artifacts / f"documa-{version}.tar.gz"
    wheels = sorted(artifacts.rglob(f"documa-{version}-*.whl"))
    assert wheels, "No runtime wheels"
    assert len({wheel.name for wheel in wheels}) == len(wheels), "Duplicate wheels"
    required = {"cp310-cp310-win_amd64"}
    required.update(f"{abi}-{abi}-manylinux_2_28_{arch}" for abi in ("cp310", "cp311", "cp312") for arch in ("aarch64", "x86_64"))
    actual = {p.name.removeprefix(f"documa-{version}-").removesuffix(".whl") for p in wheels}
    assert actual == required, f"Release target matrix incomplete: {required - actual}; unexpected: {actual - required}"
    verified = []
    for wheel in wheels:
        report = verify(wheel, sdist)
        smoke = json.loads((evidence / f"{wheel.name}.smoke.json").read_text(encoding="utf-8-sig"))
        installed_evidence(wheel, smoke)
        check = (evidence / f"{wheel.name}.pip-check.txt").read_text(encoding="utf-8-sig")
        assert "No broken requirements found" in check
        verified.append({**report, "installed": smoke, "pip_check": "PASS"})
    for name in ZIPPED_PLUGINS:
        assert (ROOT / "plugins" / f"{name}.zip").read_bytes() == build_zip_bytes(ROOT / "plugins" / name)
    npm = artifacts / f"documa-openclaw-documa-{version}.tgz"
    with tarfile.open(npm, "r:gz") as archive:
        assert json.load(archive.extractfile("package/package.json"))["version"] == version
        assert "package/index.js" in archive.getnames()
    output.mkdir(parents=True, exist_ok=True)
    payloads = []

    def copy(source: Path, relative: str) -> Path:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        payloads.append(target)
        return target

    for name, host in HOSTS.items():
        copy(ROOT / "plugins" / f"{name}.zip", f"plugins/{host}/{name}-{version}.zip")
    copy(npm, f"plugins/openclaw/{npm.name}")
    copy(sdist, f"source/{sdist.name}")
    platforms = {}
    for wheel in wheels:
        platform = "linux-arm64" if "aarch64" in wheel.name else "linux-x86_64" if "x86_64" in wheel.name else "windows-x86_64"
        target = copy(wheel, f"runtime/{platform}/{wheel.name}")
        platforms.setdefault(platform, []).append(target)
    copy(ROOT / f"docs/documa/install-{version}.md", "INSTALL.md")
    copy(ROOT / "CHANGELOG.md", "CHANGELOG.md")
    copy(evidence / "portability.md", "HOST-CAPABILITIES.md")
    copy(evidence / "plugin-ir.json", "evidence/plugin-ir.json")
    copy(evidence / "lifecycle-plan.json", "evidence/lifecycle-plan.json")
    provenance = {"sdist_sha256": sha256(sdist), "linux": {}}
    for arch in ("x86_64", "aarch64"):
        folder = evidence / f"linux-{arch}"
        images = json.loads((folder / "container-image.json").read_text(encoding="utf-8"))
        provenance["linux"][arch] = {
            "image_digests": images[0]["RepoDigests"],
            "build_platform": (folder / "build-platform.txt").read_text(encoding="utf-8").strip(),
        }
    report = {
        "version": version, "status": "PASS", "scope": "local-artifact-release",
        "plan_id": json.loads((evidence / "lifecycle-plan.json").read_text(encoding="utf-8"))["plan_id"], "plan_version": 1,
        "host_packages": {**HOSTS, "openclaw-documa": "openclaw"},
        "plugin_architecture": "universal wrappers; separately installed native runtime required",
        "validation": gates, "runtime_validation": verified,
        "build_provenance": provenance,
        "artifacts": [{"path": p.relative_to(output).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in payloads],
        "not_performed": ["registry publication", "global runtime upgrade", "four-host live installation/reload", "physical ARM64 hardware test", "macOS wheels"],
        "rollback": "Existing 0.8.0 artifacts and installed runtime retained; rebuild derived document views when switching versions.",
        "limits": ["Linux ARM64 installed tests execute through QEMU", "Python dependencies require a package index", "Existing PDF full quality gate remains unresolved"],
    }
    report_path = output / "release-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payloads.append(report_path)
    common = [p for p in payloads if p.relative_to(output).parts[0] == "plugins" or p.parent == output]
    for platform, runtime in platforms.items():
        bundle = output / f"documa-{version}-{platform}-bundle.zip"
        bundle_zip(bundle, output, [*common, *runtime])
        payloads.append(bundle)
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(p)}  {p.relative_to(output).as_posix()}\n" for p in payloads), encoding="utf-8",
    )
    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / release_version())
    args = parser.parse_args()
    print(package(args.artifacts, args.evidence, args.output))
