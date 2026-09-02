# Documa internal native parsers

This directory vendors the Rust sources that are compiled into the
`documa` Python distribution:

- `pdf/`: `rust-pdf-parser` 0.2.0 core and Python binding.
- `office/`: `rust-office-parser` 0.1.0 core, format crates, Python binding,
  CLI, deterministic fixtures, and the `office-layout-v1` contract.
- `lingxi/`: rust-Lingxi 0.4.5 core and Python binding, built privately as
  `documa._vendor.lingxi._core`. The three approved model binaries live in
  `src/documa/_vendor/lingxi/assets/` and ship in both wheel and sdist. No
  separate LingXi package, public download, or runtime model fetch is needed.
  `lingxi/VENDOR.json` records provenance and approved hashes; `lingxi/ASSETS.md`
  retains the upstream model redistribution statement. Builds verify every
  model hash before producing metadata or artifacts. Optional sentiment data,
  external corpora, and training artifacts are not included.

They remain separate Cargo workspaces because their release versions, PyO3 ABI
choices, and format semantics differ. Documa unifies them at
`documa.adapters.native_binding`, which validates identity, required calls,
capabilities, and the JSON native-error envelope before either format adapter
maps events into `DocumentIR`.

The source was imported from the local sibling projects named above. Generated
`target/`, wheels, temporary Documa outputs, caches, private corpora, benchmark
artifacts and generated WASM packages were intentionally excluded. PDF CLI/WASM
source is retained only because core contract tests statically audit those front
ends; they are not members of Documa's internal PDF build workspace.
The original licenses and third-party notice are retained in each subtree.

Build all three extensions as part of Documa:

```powershell
python -m build --no-isolation
```

For Rust-only validation:

```powershell
cargo fmt --manifest-path native/pdf/Cargo.toml --all --check
cargo test --manifest-path native/pdf/Cargo.toml --workspace
cargo fmt --manifest-path native/office/Cargo.toml --all --check
cargo test --manifest-path native/office/Cargo.toml --workspace
```
