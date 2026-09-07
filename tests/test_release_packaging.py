import hashlib
import zipfile

import pytest

from scripts.package_release import bundle_zip, installed_evidence


def test_installed_evidence_rejects_an_old_native_build(tmp_path):
    wheel = tmp_path / "documa-0.9.0-cp310-cp310-manylinux_2_28_aarch64.whl"
    modules = ["rust_pdf._native", "rust_office._core", "documa._vendor.lingxi._core"]
    with zipfile.ZipFile(wheel, "w") as archive:
        for module in modules:
            archive.writestr(module.replace(".", "/") + ".so", b"new-native")
    smoke = {"status": "PASS", "documa": "0.9.0", "lingxi": "0.4.5", "external_lingxi": False, "rust_pdf_spacing": "PASS",
             "native_sha256": {m: hashlib.sha256(b"new-native").hexdigest() for m in modules}}
    installed_evidence(wheel, smoke)
    smoke["native_sha256"]["rust_pdf._native"] = hashlib.sha256(b"old-native").hexdigest()
    with pytest.raises(AssertionError):
        installed_evidence(wheel, smoke)


def test_bundle_has_only_selected_platform_and_verifiable_relative_paths(tmp_path):
    selected = tmp_path / "runtime/linux-arm64/runtime.whl"
    selected.parent.mkdir(parents=True)
    selected.write_bytes(b"arm-native")
    unrelated = tmp_path / "runtime/windows/runtime.whl"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"windows-native")
    one, two = tmp_path / "one.zip", tmp_path / "two.zip"
    bundle_zip(one, tmp_path, [selected])
    bundle_zip(two, tmp_path, [selected])
    assert one.read_bytes() == two.read_bytes()
    with zipfile.ZipFile(one) as archive:
        assert set(archive.namelist()) == {"runtime/linux-arm64/runtime.whl", "SHA256SUMS.txt"}
        digest, name = archive.read("SHA256SUMS.txt").decode().strip().split("  ")
        assert hashlib.sha256(archive.read(name)).hexdigest() == digest
