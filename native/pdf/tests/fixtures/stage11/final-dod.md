# Stage 11 最終 Definition of Done

狀態：PASS  
驗證日期：2026-07-29  
版本：`0.2.0`／`stage-11`

## Overview

本文件逐條關閉 [`agent-plan.md`](../../../docs/specs/stage-11/agent-plan.md) 的
Stage 11.7 與最終 Definition of Done。功能證據以
[`validation-matrix.md`](validation-matrix.md) 為準；封裝雜湊、安裝測試與供應鏈結果以
[`docs/release.md`](../../../docs/release.md) 為準。

## Version、inputs and parameters

- Rust workspace、Python wheel 與 browser WASM package 版本皆為 `0.2.0`；四端
  `version_info` 回報 `stage-11`。
- 私有 corpus 僅由 `RUST_PDF_REAL_AI_INDEX` 與 `RUST_PDF_REAL_TAIWAN` 注入，未複製進
  repository。
- extraction mode 為 `content-order`、`layout`、`auto`；既有未指定 mode 的 API 維持
  legacy layout 行為。

## Expected responses and errors

成功時四端回傳一致的 V2 text、spans、warnings 與 quality metadata。無效 mode 回傳穩定
`invalid_option`；缺少與無效 Unicode mapping 分別回報 `unicode_mapping_missing` 與
`unicode_mapping_invalid`。資源上限由 machine-readable errors/warnings 表達，不會無界解碼。

## Final acceptance evidence

| 驗收條件 | 結果 | 直接證據 |
|---|---|---|
| FID-01～REG-01 全通過 | PASS | validation matrix 逐項記錄 FID、ACT、SEC、PERF、REG 結果與可重跑命令。 |
| Auto 修正兩份實檔空白 artifact | PASS | core 與 CLI private gates 均找到 AI Index 英文標題、台灣中文標題，且禁止片段不存在。 |
| legacy 保持；V2 四端一致 | PASS | Rust、CLI、Python、WASM parity regressions 與四端 `0.2.0/stage-11` version tests 通過。 |
| ActualText、font、layout、budget、cache 有 valid/invalid/boundary/fuzz 證據 | PASS | `stage11_actual_text`、`stage11_font_metrics`、`stage11_layout`、`stage11_decode_budget` 與 60 秒 fuzz gate 通過。 |
| 私有 PDF 未進 repository | PASS | repository filename/path scan 為 0；manifest 只保存 SHA、metrics 與文字條件。 |
| MSRV、workspace、wheel、WASM、security 全通過 | PASS | Rust 1.88 check、workspace fmt/check/Clippy/tests、wheel 5/5、Node WASM 4/4、cargo-deny 與 cargo-audit 通過。 |
| 公開文件與 DEVNOTE 同步 | PASS | README、architecture、compatibility、errors、security、release、text-fidelity、migration 與 fixture docs 均通過結構及本機連結稽核。 |

## Stage 11.7 delivery evidence

| 交付物 | 結果 | 可用性證據 |
|---|---|---|
| Rust library | PASS | `pdf-core-0.2.0.crate` 已由 `cargo package --allow-dirty --offline` 解包、編譯及驗證。 |
| CLI | PASS | Windows x86_64 archive 內含 release binary、README、LICENSE、notices；version 與 public fixture extraction 通過。 |
| Python | PASS | CPython 3.10 wheel 強制取代 0.1.0 後，import、version 與 5 項 API tests 通過。 |
| browser WASM | PASS | web-target package 已產生；Node wasm-bindgen 4/4、web module 直接載入、文字抽取及六個 declarations 稽核通過。 |

Stage 11.7 的文件/API 一致、legacy 升級指南、四種可建立交付物、dependency/security/MSRV
gates 與 DEVNOTE handoff 五項 DoD 全部有直接證據。

## Reproducible command example

```powershell
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo test --workspace --doc
cargo +1.88.0 check --workspace --all-targets
cargo deny check
cargo audit
```

私有 release gate 需在同一程序先設定兩個 corpus 環境變數；若缺檔，測試的 `SKIP` 不得記為
private PASS。

## Known limitations

`0.2.0` 不包含頁面渲染、OCR、加密 PDF、damaged-xref repair 或 PDF 2.0 normative
conformance。一般 stream filter 只支援 Flate；JPEG Image XObject 由 codec 驗證並保留。
ChromeDriver 151 在本機建立測試視窗時回傳 404，因此瀏覽器目標以實際 web package、WASM
binary、declarations 與 DOM-free Node runner 驗證，沒有宣稱完成瀏覽器 UI/E2E 渲染測試。

## Completion decision

Stage 11.0～11.7 沒有剩餘未通過的規格內驗收條件；以上限制都屬明列的非目標，而非未完成
工作。因此 Stage 11 可以結案，下一個功能 stage 必須另立新規格，不得把 rendering、OCR 或
PDF 2.0 偷渡進本 release candidate。