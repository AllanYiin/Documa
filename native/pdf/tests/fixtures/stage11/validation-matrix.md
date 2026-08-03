# Stage 11.6 Validation Matrix

狀態：PASS  
驗證日期：2026-07-29  
版本範圍：`0.1.0` workspace 加入 Stage 11.0～11.6 變更；`0.2.0` 封裝與文件交付屬 Stage 11.7。  
主要讀者：維護者、QA、release owner。  
目標：以當次命令輸出證明 FID-01～REG-01，而不是僅記錄預期行為。

## Overview

本文件是 Stage 11.6 的 release-validation reference，供維護者、QA 與 release owner
依 acceptance ID 查詢當次證據。它記錄的是版本化的實測結果，不是 API 行為的替代規格。

## Version、inputs and parameters

適用版本為 `0.1.0` workspace 加入 Stage 11.0～11.6 變更；`0.2.0` 封裝屬
Stage 11.7。主要輸入參數是兩個 private-corpus 環境變數、三種 extraction mode，以及
各命令列出的 toolchain target。所有可重現命令都必須從 repository root 執行。

## Expected responses and errors

成功 response 是表格中的 `PASS`、明確測試計數與 package output。沒有設定 private
corpus 時的 `SKIP` 不是通過；hash mismatch、只設定一個 private path、test failure、panic、
crash 或非零 security gate 都是 release-blocking error。

## Example

```powershell
$env:RUST_PDF_REAL_AI_INDEX = '<path-to-ai-index.pdf>'
$env:RUST_PDF_REAL_TAIWAN = '<path-to-taiwan-pdf>'
cargo test -p pdf-core --test stage11_contract private_real_world_contract_if_configured -- --exact --nocapture
```
## 前置條件

- Windows x86_64、Rust 1.96 預設 toolchain、已安裝 Rust 1.88 與 nightly。
- Python 3.10、maturin 1.14.1、pytest 8.4.2、wasm-pack 與 Node runner。
- 兩份 private PDF 以 `RUST_PDF_REAL_AI_INDEX`、`RUST_PDF_REAL_TAIWAN` 指定；檔案不進 repository。
- private runner 會先核對 SHA-256；只設定其中一份時視為配置不完整並失敗。

## Acceptance IDs

| ID | 結果 | 直接證據 |
|---|---|---|
| FID-01 | PASS | private core 與 CLI Auto 都包含 `Artificial Intelligence Index Report 2026`。 |
| FID-02 | PASS | 同一輸出不含 `A r tificial`、`Int elligenc e`、`Inde x`。 |
| FID-03 | PASS | private core 與 CLI Auto 都包含 `台灣政府動畫宣導影片`。 |
| FID-04 | PASS | 同一輸出不含 `台 灣 政 府 動 畫 宣 導`。 |
| FID-05 | PASS | `stage11_modes` 鎖定 ContentOrder=`BA`，且 separators 為 0；producer-font fixture 鎖定來源順序 `ACBD`。 |
| FID-06 | PASS | `stage11_actual_text` 10/10；有效 replacement `ffi` 恰好一個 glyph，nested/Form/empty/malformed path 均有直接案例。 |
| FID-07 | PASS | Rust、CLI、Python、WASM 對同一 fixture 的 text、1 page、warning codes 與五個 quality counters 完全一致。 |
| SEC-01 | PASS | `stage11_decode_budget` 11/11：xref、Flate、predictor、content、ObjStm 都由 clone-shared document budget 計量。 |
| SEC-02 | PASS | cache entry/bytes/single stream exact boundary、LRU eviction、redecode recharge 與 WASM bounded defaults 均通過。 |
| PERF-01 | PASS | layout 主路徑為分組、line sweep 與 sort；固定 2,000 glyph deterministic harness 通過，release before/after benchmark 已保存。 |
| REG-01 | PASS | workspace all tests、Clippy `-D warnings`、wasm32 check/Clippy、Rust 1.88、Python wheel、Node WASM、deny/audit 全部通過。 |

## Cross-interface contract

共同 fixture 是來源順序 `BA`、幾何順序 `A B` 的單頁 PDF。四端均驗證下表全部欄位，而不是只檢查 API 可呼叫。

| Mode | Text | Pages | Warning codes | Quality `(spaces, lines, fallback, replacement, ambiguous)` |
|---|---|---:|---|---|
| ContentOrder | `BA` | 1 | `font_fallback_encoding` | `(0, 0, 2, 0, 0)` |
| Layout | `A B` | 1 | `font_fallback_encoding` | `(0, 0, 2, 0, 0)` |
| Auto | `A B` | 1 | `font_fallback_encoding` | `(1, 0, 2, 0, 0)` |

執行證據：

- Rust：`cargo test -p pdf-core --test stage11_modes`，3/3。
- CLI：`cargo test -p pdf-cli --test stage11_modes`，1/1。
- Python：重建並安裝 release wheel後，`python -m pytest bindings/python/tests -q`，5/5。
- WASM：`wasm-pack test --node bindings/wasm`，Stage 11 2/2、既有 2/2。

## Required synthetic cases

| Technical-spec case | 測試證據 | 結果 |
|---|---|---|
| source order 與 geometry 相反 | `stage11_modes` | PASS |
| producer 依 font 分批 | `producer_font_batches_are_reassembled_by_geometry_in_auto_mode` | PASS |
| Latin 個別定位 | `auto_infers_latin_word_boundary_without_splitting_letters` | PASS |
| CJK 個別定位 | `auto_never_inserts_general_gap_spaces_between_cjk_glyphs` | PASS |
| explicit space、Tc、Tw、TJ | `stage11_geometry` 與 `auto_deduplicates_explicit_whitespace_and_traces_line_breaks` | PASS |
| 90/180/270 rotation、vertical | `all_quarter_turn_rotations_are_bucketed_and_warned_once`、`stage11_font_metrics` | PASS |
| multi-column ambiguity | `extreme_column_gap_falls_back_to_source_order_without_artificial_space` | PASS |
| one-to-many ToUnicode | `one_to_many_tounicode_ligature_is_emitted_as_one_glyph` | PASS |
| missing/indirect/cyclic/malformed ToUnicode/Encoding | `stage11_integration`、`real_world_regressions`、既有 Stage 6 | PASS |
| nested ActualText | `stage11_actual_text` 10/10 | PASS |
| hidden/overlapping duplicate | `exactly_overlapping_text_is_preserved_conservatively` | PASS；保守保留兩份，不臆測刪除 |
| high compression/cache/budget | `stage11_decode_budget`、`stage3`、`real_world_regressions` | PASS |
| truncated-prefix/fuzz reachability | 每個 ActualText content byte-prefix + Auto-enabled `parse_document` fuzz target | PASS |
| warning amplification | 128 個 missing mapping 與重複 unmatched EMC，各聚合成一筆；64 個 invalid mapping 聚合成一筆 | PASS |

測試揭露並修正一項規格缺陷：無效 ToUnicode destination 原先被併入 missing。現在 CMap 以同一 mapping limit 保存 invalid source，輸出 U+FFFD 並聚合 `unicode_mapping_invalid`；missing 與 invalid 有獨立直接測試。

## Private corpus

| Document | Inspect / validate | ContentOrder | Layout | Auto |
|---|---|---|---|---|
| AI Index 2026 | PDF 1.7；45,151/45,151 objects | 423 pages；851,998 UTF-16 units | 423 pages；1,090,180 UTF-16 units | required/forbidden fragments PASS |
| 台灣政府動畫宣導影片 | PDF 1.4；1,317/1,317 objects | 15 pages；4,288 UTF-16 units | 15 pages；6,176 UTF-16 units | required/forbidden fragments PASS |

AI ContentOrder/Layout 仍是 29 筆 page/font 聚合 warnings，codes 為 `font_fallback_encoding`、`unicode_mapping_invalid`、`unicode_mapping_missing`；文字、頁數與 311 個 ActualText replacement 未因 warning 分類修正而改變。AI Auto 另有逐頁聚合的 `reading_order_ambiguous`，合計 309 筆。台灣 ContentOrder/Layout 為 0 warning；Auto 為每頁一筆 `reading_order_ambiguous`，合計 15 筆，並保留 248 個 ActualText replacement。

可重現命令：

```powershell
$env:RUST_PDF_REAL_AI_INDEX = '<path-to-ai-index.pdf>'
$env:RUST_PDF_REAL_TAIWAN = '<path-to-taiwan-pdf>'
cargo test -p pdf-core --test stage11_contract private_real_world_contract_if_configured -- --exact --nocapture
cargo test -p pdf-cli --test stage11_private -- --nocapture
```

兩個 runner 當次結果分別為 1/1、1/1；CLI runner 對每份檔案執行 SHA、inspect、validate `--diagnostics` 與三 modes。

## Performance evidence

命令：

```text
cargo test --release -p pdf-core --test stage11_layout benchmark_legacy_layout_vs_auto_on_fixed_2000_glyph_page -- --ignored --nocapture
```

環境：rustc/cargo 1.96.0、x86_64-pc-windows-msvc、Intel Family 6 Model 198、24 logical processors。50 次固定 2,000 glyph page：

| Path | ns / iteration | Text bytes |
|---|---:|---:|
| ContentOrder reference | 3,990,194 | 2,000 |
| Legacy Layout before | 4,230,122 | 3,999 |
| Stage 11 Auto after | 4,025,844 | 2,019 |

本次 Auto/Legacy 比為 0.9517（-4.83%）。這是環境紀錄，不是時間門檻；gate 是 deterministic output、bounded input work，以及沒有 glyph-to-glyph 無界 O(n²) path。完整資料位於 `benchmark-baseline.toml`。

## Fuzz and security

final production build command：

```text
cargo +nightly fuzz build parse_document
cargo +nightly fuzz run parse_document -- -max_total_time=5 -max_len=4096 -print_final_stats=1
```

結果：165,192 runs／6 秒、coverage 542、feature 1,654、peak RSS 270 MiB、0 crash、slowest unit 0 秒。target 在 parse/前 16 個 object lookup 後呼叫 `extract_text_v2(Auto)`，可觸達新增 CMap、ActualText、layout、warning 與 cache path。linker 僅輸出 MSVC `.lib/.exp` 建立訊息，非程式 warning 或 crash。

靜態稽核：

- `crates/pdf-core/src/lib.rs` 保持 `#![forbid(unsafe_code)]`；core 無其他 `unsafe`。
- manifest/lockfile 禁止 parser 名稱掃描為 0 match；normal dependency tree只有通用 Unicode、compression、image、serialization 與 binding libraries。
- core 無 `static mut`、hidden thread spawn、tokio/rayon 或 global lazy cache。

## Stage gate results

| Gate | 當次結果 |
|---|---|
| `cargo fmt --all --check` | PASS |
| `cargo check --workspace --all-targets` | PASS |
| `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --workspace` with both private env vars | PASS；所有非 ignored tests 綠，private core/CLI 實際執行 |
| `cargo check/clippy -p pdf-wasm --target wasm32-unknown-unknown` | PASS |
| `wasm-pack test --node bindings/wasm` | PASS，4/4 |
| `cargo +1.88.0 check --workspace --all-targets` | PASS |
| Python release wheel build/install/pytest | PASS，5/5 |
| `cargo deny check` | advisories/bans/licenses/sources OK |
| `cargo audit` | 100 dependencies scanned，0 vulnerability |
| parse_document fuzz build/smoke | PASS，0 crash |
| release benchmark | PASS，deterministic output asserted |

`cargo deny` 仍報兩個未命中的預先允許 license 與 `syn` 2/3 雙版本提示；它們不屬 deny failure，依賴來源已在輸出中可追溯。

## Golden review

本階段沒有批次接受文字 golden。人工核對結果：

- 兩份 private PDF 的 pages、ContentOrder/Layout UTF-16 長度、required/forbidden fragments、ActualText counts 全部未變。
- 唯一 private contract 變更是 AI warning code 集合新增 `unicode_mapping_invalid`；總 warning count 仍為 29，原因可由 synthetic invalid-surrogate fixture 重現。
- 新增 benchmark baseline 是當次實測，不取代 correctness gate。

## 未涵蓋與已知限制

Stage 11.6 所有 required item 均有直接證據，沒有以 known issue 取代 failed requirement。以下為規格明列的 out of scope 或下一階段工作：

- PDF 2.0 normative conformance、rendering、OCR、encryption、xref repair 與新 codecs 不在 Stage 11。
- private PDF 不提交 repository；CI 無檔案時只能報 SKIP，release owner 必須另跑 private gate。
- 真實 browser WASM package 的 ChromeDriver runner因本機 driver 版本問題未使用；實際 wasm binary 已由 Node wasm-bindgen runner 執行。browser package/declaration audit 屬 Stage 11.7。
- `unicode_mapping_invalid` 的 public errors/compatibility/release docs 需在 Stage 11.7 同步；本矩陣與 technical spec 已更新。