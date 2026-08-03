"""Build Documa and its two in-repository Rust parser extensions."""

from setuptools import setup
from setuptools_rust import Binding, RustExtension


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
    ],
    zip_safe=False,
)
