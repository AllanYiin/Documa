# Dependency license inventory

Audit date: 2026-08-03

The project itself and all workspace crates are MIT licensed. Direct runtime
dependencies were selected from permissive-license projects:

| Dependency | Purpose | Declared license |
| --- | --- | --- |
| base64 | Asset event encoding | MIT OR Apache-2.0 |
| calamine | XLS workbook values/formulas and Excel date conversion | MIT |
| cfb | OLE compound-file inspection for BIFF8 | MIT |
| clap | Diagnostic CLI | MIT OR Apache-2.0 |
| PyO3 | ABI3 Python binding | MIT OR Apache-2.0 |
| quick-xml | Streaming OOXML parsing | MIT |
| serde / serde_json | Versioned event serialization | MIT OR Apache-2.0 |
| sha2 | Stable content hashes | MIT OR Apache-2.0 |
| thiserror | Internal error definitions | MIT OR Apache-2.0 |
| zip | Constrained OOXML package reader | MIT |

Fixture and benchmark tooling is not linked into the wheel:

| Dependency | Pinned version | Declared license |
| --- | ---: | --- |
| python-docx | 1.2.0 | MIT |
| openpyxl | 3.1.2 | MIT |
| python-pptx | 1.0.2 | MIT |
| xlwt | 1.3.0 | BSD |
| psutil | 6.0.0 | BSD-3-Clause |
| libfuzzer-sys | 0.4.x | MIT OR Apache-2.0 |

The exact resolved Rust dependency graph is locked in `Cargo.lock`. Before a
public binary release, the build owner must rerun automated transitive-license
and vulnerability review against that lockfile; this inventory is not legal
advice and does not replace upstream license texts.

## 0.2.0 additions (2026-10-12)

- `xlrd==2.0.1` (BSD): independent BIFF8 corpus comparison; test tooling only.
- `jsonschema` (MIT): nested IR validation in tests; not included in the wheel.
- Private regression documents are not distributed with this repository. License/permission records belong in the explicitly configured local corpus directory; the workspace MIT license does not apply to external documents.
- Runtime dependency families remain unchanged; shared SHA-256 and serde dependencies are reused by additional workspace crates.
