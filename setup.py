"""Build Documa with its in-repository Rust parsers and LingXi runtime."""

from pathlib import Path
from runpy import run_path

from setuptools import setup
from setuptools_rust import Binding, RustExtension


run_path(str(Path(__file__).parent / "native/lingxi/verify.py"))["verify"]()

setup(
    rust_extensions=[
        RustExtension(
            "rust_pdf._native",
            path="native/pdf/bindings/python/Cargo.toml",
            binding=Binding.PyO3,
        ),
        RustExtension(
            "rust_office._core",
            path="native/office/crates/office-py/Cargo.toml",
            binding=Binding.PyO3,
        ),
        RustExtension(
            "documa._vendor.lingxi._core",
            path="native/lingxi/crates/lingxi-py/Cargo.toml",
            binding=Binding.PyO3,
        ),
    ],
    zip_safe=False,
)
