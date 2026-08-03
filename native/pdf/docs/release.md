# Release runbook

本文是 `0.2.x` 維護者的發佈 runbook。適用 Rust library、CLI、Python wheel 與 browser WASM。
任一必要 gate 失敗都停止發佈；`SKIP` private corpus 不等於 private gate passed。

## 1. Prerequisites／前置條件

- Rust stable 1.88+，含 rustfmt、Clippy、`wasm32-unknown-unknown` target。
- repository pinned toolchain、Rust 1.88 toolchain 與 nightly（cargo-fuzz）。
- Python 3.9+、maturin、pytest。
- wasm-pack、Node、cargo-deny、cargo-audit、cargo-fuzz。
- 兩份 private corpus 路徑與經驗證 SHA-256；檔案不得複製進 repository。
- clean release output directories，且不覆蓋前一版已發布 artifact。

## 2. Core、MSRV 與 documentation gate

```powershell
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo test --workspace --doc
cargo check -p pdf-wasm --target wasm32-unknown-unknown
cargo clippy -p pdf-wasm --target wasm32-unknown-unknown -- -D warnings
cargo +1.88.0 check --workspace --all-targets
```

預期：全部 exit 0、Clippy 無 warning、rustdoc example 可編譯。README 的 CLI example 另以
`tests/fixtures/valid/text-minimal.pdf` 執行；必須抽出 `Hello PDF text`。

## 3. Private corpus gate

```powershell
$env:RUST_PDF_REAL_AI_INDEX = '<path-to-ai-index.pdf>'
$env:RUST_PDF_REAL_TAIWAN = '<path-to-taiwan-pdf>'
cargo test -p pdf-core --test stage11_contract private_real_world_contract_if_configured -- --exact --nocapture
cargo test -p pdf-cli --test stage11_private -- --nocapture
```

兩個 runner 都必須 1/1 PASS，並先通過 SHA-256。CLI runner 必須覆蓋 inspect、validate
`--diagnostics` 與 ContentOrder／Layout／Auto。詳細 expected fragments 與 metrics 見
[`tests/fixtures/stage11/validation-matrix.md`](../tests/fixtures/stage11/validation-matrix.md)。

## 4. Supply-chain and policy gate

```powershell
cargo deny check advisories bans licenses sources
cargo audit
cargo tree --workspace
rg -n "lopdf|pdf-rs|pdfium|mupdf|poppler" Cargo.toml crates bindings Cargo.lock
rg -n "unsafe" crates/pdf-core/src
```

預期：四類 cargo-deny check 都是 OK、cargo-audit 無 vulnerability、dependency tree 與 manifests
不含 PDF-aware parser，core 除 `#![forbid(unsafe_code)]` 外沒有 `unsafe`。未命中的預先允許 license
或已人工確認的 duplicate transitive version 可記錄為 warning，但不得掩蓋 deny failure。

## 5. Fuzz and performance gate

```powershell
cargo +nightly fuzz build parse_document --fuzz-dir fuzz
cargo +nightly fuzz run parse_document --fuzz-dir fuzz -- -max_total_time=60 -max_len=4096 -print_final_stats=1
cargo test --release -p pdf-core --test stage11_layout benchmark_legacy_layout_vs_auto_on_fixed_2000_glyph_page -- --ignored --nocapture
```

正式 release fuzz 不得少於 60 秒；CI/local bounded smoke 可為 5 秒，但需標示用途。預期無 crash、
ASan report、timeout artifact 或 panic。benchmark 必須記錄 toolchain、hardware、deterministic output
與相對結果；不以不穩定的絕對時間作唯一 gate。

## 6. Rust library and CLI package gate

```powershell
cargo build --release --workspace
cargo package -p pdf-core --allow-dirty --offline
cargo package -p pdf-cli --allow-dirty --list
cargo run --release -p pdf-cli -- version --json
cargo run --release -p pdf-cli -- extract tests/fixtures/valid/text-minimal.pdf --mode auto --json
```

預期 `pdf-core-0.2.0.crate` 完成 Cargo unpack/verify build，CLI release binary 回
`0.2.0`／`stage-11` 並抽出 `Hello PDF text`。Windows RC 將 `rust-pdf.exe`、README、LICENSE 與
THIRD_PARTY_NOTICES 封裝為 `rust-pdf-cli-0.2.0-windows-x86_64.zip`。

`pdf-cli` manifest 以 `pdf-core = { version = "0.2.0", path = ... }` 保持本機開發與 registry 發布
相容。因尚未把 core RC 發布到 crates.io，CLI crate 在本階段只做 packaged-file list audit；若未來
要發布 CLI crate，順序必須是先發布 core、確認 registry 可解析，再跑 `cargo package -p pdf-cli`
完整 verify。這不影響本階段要求的 standalone CLI binary/archive。

## 7. Python wheel gate

```powershell
maturin build --release --manifest-path bindings/python/Cargo.toml --out target/stage11-wheels
python -m venv .stage11-python
$wheel = (Get-ChildItem target/stage11-wheels/rust_pdf_parser-0.2.0-*.whl).FullName
& ./.stage11-python/Scripts/python.exe -m pip install --force-reinstall $wheel
& ./.stage11-python/Scripts/python.exe -m pytest bindings/python/tests -q
& ./.stage11-python/Scripts/python.exe -c "import rust_pdf; print(rust_pdf.version_info())"
```

預期 wheel 可在隔離環境安裝，所有 tests 通過，version tuple 為 `('0.2.0', 'stage-11')`。

## 8. Browser WASM package gate

```powershell
wasm-pack test --node bindings/wasm
wasm-pack build bindings/wasm --target web --release --out-dir pkg-stage11-rc
rg -n "versionInfo|inspect|extractText|extractWithOptions|extractImages|extract" bindings/wasm/pkg-stage11-rc/pdf_wasm.d.ts
```

預期 Node wasm-bindgen tests 全部通過，`bindings/wasm/pkg-stage11-rc/package.json` version 是 0.2.0，
TypeScript declarations 含六個 public exports；`extractWithOptions` 的 options 是 JavaScript object，
result 含 V2 DTO。WASM package 不做 file I/O。

## 9. 文件、version 與 fixture audit

1. `Cargo.toml`、`Cargo.lock` workspace crates、`pyproject.toml`、WASM package metadata 都是 0.2.0。
2. `version_info()` 四端回 `stage-11`，並有直接 test。
3. README 四端 examples 與實際 signature 一致。
4. architecture、compatibility、errors、security、text-fidelity、migration、release 與 DEVNOTE 同步。
5. fixture manifest SHA-256 與 public fixture 一致；private PDFs 不在 repository。
6. technical-documentation audit 通過；validation matrix 保留 command、result、evidence 與 limits。

## 10. Release evidence template

完成當次驗證後記錄：

| Item | Result | Artifact or evidence |
|---|---|---|
| Rust core crate | PASS | `target/package/pdf-core-0.2.0.crate`；72,298 bytes；SHA-256 `770a84fe0a7e7acac6dabddee2f878c8c8bca530aa3b01ce55bfefcf1d121d95`；Cargo verify build passed |
| CLI archive | PASS | `.stage11-dist/rust-pdf-cli-0.2.0-windows-x86_64.zip`；793,624 bytes；SHA-256 `fedb181174ae867f87716183fd4a1ad999c41947252518939b5a632e7b1c990d`；binary version/example passed |
| Python wheel | PASS | `target/stage11-wheels/rust_pdf_parser-0.2.0-cp310-cp310-win_amd64.whl`；705,071 bytes；SHA-256 `47720f3df384fbd39c57f3c2a0834a68acfee56d774b69c6354cbadd3d0b9d7a`；0.1.0→0.2.0 install + 5/5 passed |
| Browser WASM | PASS | `bindings/wasm/pkg-stage11-rc/`；WASM 967,315 bytes；SHA-256 `4d232cda576c8758addb7d25372bf8894dc8bd0d5138d0b9b405dcb8652545be`；Node 4/4 + generated web-module smoke passed |
| Full Stage 11 gate | PASS | workspace/private/MSRV/security/docs/fuzz/benchmark gates passed；詳見 DEVNOTE 與 Stage 11.6 matrix |

當次 release fuzz：61 秒、1,162,516 runs、coverage 674、features 2,368、0 crash、slowest unit 0 秒、
peak RSS 602 MiB。固定 2,000-glyph benchmark：ContentOrder 4,146,392 ns/iteration、legacy Layout
4,465,646、Auto 4,291,616；Auto/legacy = 0.9610（約 -3.90%）。timing 只作同機證據。

## 11. Rollback／回退

發佈前保留上一版 artifacts 與 lockfile。若 package smoke 或 downstream integration 失敗，不要覆蓋
既有 tag/artifact；保留 failure output，修正最小責任模組並補 regression，然後從 Core gate 重跑。
若尚未公開發佈，可恢復 version metadata；不可刪除揭露缺陷的測試或降低 limits 以偽造通過。

## Symptoms／告警

任一 gate 非零、Clippy warning、fuzz artifact、private hash/fragment failure、wheel import failure、WASM
export 缺漏、fixture hash 不符、dependency advisory，或文件 signature 漂移都是停止發佈的 alert。

## Diagnosis／診斷

先保存失敗命令與完整 output，定位 core、private corpus、supply chain、fuzz、Rust package、Python、
WASM 或 docs。verify 同一命令可在乾淨 output directory 重現，並比對 lockfile、toolchain、target、
artifact metadata 與 `version_info()`。

## Remediation／修復

修正最小責任模組，補 direct regression，再從 Core gate 開始重跑全部步驟。若文件測試揭露真實
API mismatch，可修 API 或文件；不得只修改 expected output 來掩蓋行為差異。

## Escalation／升級通報

若 crash 涉及不可信輸入、resource limit bypass 或可能的 memory-safety 問題，停止公開發佈並依
[`security.md`](security.md) vulnerability reporting 交接。若 private corpus provenance 或 hash 無法
確認，不得自行更新 baseline；交由 corpus owner 驗證。