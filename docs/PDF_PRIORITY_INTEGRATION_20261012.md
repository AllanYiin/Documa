# PDF priority integration — 2026-10-12

The standalone Rust PDF work is integrated into `native/pdf`; the installable Python
API in `src/rust_pdf` is byte-identical to `native/pdf/bindings/python/python/rust_pdf`.
All PDF syntax remains in the safe Rust core. Documa provider defaults and its
renderer/OCR pipeline are unchanged. Human reading-order, table and image quality
acceptance remains deferred.

## Commit order

Commit timestamps reflect this submission; historical work dates are described in
messages, not backdated. Existing `12e4614` (Documa 0.9.0) is retained as the source
baseline, and remote main's two CI retention changes are merged without rewriting
history.

1. `6392378` — HTTP/Drive PDF access and OCR reading receipts (2026-10-08 work).
2. `d47452d` — indirect font Encoding/Differences repair and regressions.
3. `60d5c7e` — preserved upstream OCR and full Rust/CLI/Python/WASM workspace.
4. `9aa6c64` — bounded extra filters, vertical metrics, selection and OCR eligibility.
5. `c7a27b5` — lazy OCR budgets and ONNX model validation.
6. `278fff7` — reusable Python handles, GIL release and CLI/WASM selected streams.
7. `db1099a` — monorepo CI, fuzz, pinned ONNX checks, packaging and public receipts.
8. This handoff/evidence commit.

## Integration validation

| Check | Result |
|---|---|
| `cargo fmt --all --check` in `native/pdf` | PASS |
| `cargo clippy --workspace --all-targets --all-features --locked -- -D warnings` | PASS |
| `cargo test --workspace --all-features --locked` | 264 passed; 1 pre-existing manual benchmark ignored |
| Isolated current Documa source, `pytest tests -q` | 613 passed in final run |
| `python -m documa.cli doctor` | 9/9 passed |
| `python scripts/generate_schema.py --check` | PASS |
| `python scripts/validate_agent_plugins.py` | PASS |
| `python scripts/package_plugins.py --check` | PASS |
| Ruff on changed Python APIs, interfaces and regressions | PASS |
| Public fixture SHA-256 against files and Git blobs | PASS |
| Root Rust PDF workflow YAML and Python wrapper parity | PASS |
| `git diff --check` | PASS |

Python tests used an isolated copy of the current `src` files and the previously
verified CPython 3.10 ONNX-enabled PDF wheel, after confirming all imported Rust
sources and Python wrapper bytes match. Unchanged Office/LingXi native binaries
came from the existing installation; no global package was replaced. Explicit
`PYTHONPATH` was inherited by child processes, and the local `tests` namespace was
selected to avoid a third-party package with the same name.

One full run encountered the pre-existing Windows concurrent `Path.replace`
`WinError 5` documented in DEVNOTE; the final full rerun passed all 613 tests.
An earlier attempt also used an incorrect doctor module entry point; the correct
CLI invocation above passed. These environment/setup failures are not hidden as
product passes. Detailed local logs are under `build/pdf-priority-integration`.

The earlier standalone Python 19-test, Node WASM 14-test, pinned ONNX 7-case,
MSRV and 200,565-run fuzz evidence is retained in
`native/pdf/docs/priority-implementation-2026-10-12.md` and its public JSON receipts.
Those platform/feature checks were not repeated solely for the path relocation.
GitHub Actions results must be read from the submitted revision; this document
makes no claim that remote jobs have completed.

## Excluded local material

Build outputs, model/runtime downloads, private PDFs and review screenshots, caches,
and machine-local session files are not source changes. They remain local and are
not pushed. Native source-distribution exclusions cover fuzz outputs and private
corpus inputs. This is a source submission, with no tag, registry publication or
installed-runtime update.
