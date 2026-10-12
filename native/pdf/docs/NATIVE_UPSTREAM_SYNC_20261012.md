# Documa／Rust 源頭同步稽核（2026-10-12）

## 結果

確實存在只在 Documa 內嵌副本修正、未回補 `rust-pdf_parser` 的解析缺陷。
本次已將這些修正及對應測試回補至本機源頭；本輪 `Differences` 間接參照
修正也已同步至 Rust PDF 源頭及 Documa。沒有以舊下游副本覆蓋源頭的 OCR 功能。

| 流向／項目 | 稽核前 | 本次結果 |
|---|---|---|
| SkillNow → Documa → Rust PDF：字型 `Encoding/Differences` 間接參照 | 只有 SkillNow 固定 sdist 已修 | 三層原始碼已一致，新增 Documa adapter 整合測試 |
| Documa → Rust PDF：CTM／Form 矩陣組合順序 | 只在 Documa 副本修正 | 已回補 `graphics.rs`、Form／geometry 測試 |
| Documa → Rust PDF：英文位置式空白與黏字 | 只在 Documa 副本修正 | 已回補 `layout.rs`、`layout_ir.rs`、閱讀序測試 |
| Documa → Rust PDF：旋轉旁註干擾正文分段 | 只在 Documa 副本修正 | 已回補 `reading_order.rs` 與旋轉旁註測試 |
| Documa → Rust PDF：窄欄／字形實際尺度／段落與圖說分隔 | 只在 Documa 副本修正 | 已回補 `reading_order.rs` 與欄／段落測試 |
| Documa → LingXi：編譯版本 `__version__` 匯出 | 只在 Documa binding 與 Python wrapper 增補 | 尚保留為下游版本識別整合；不是分詞／摘要演算法修正，本輪未擴改 LingXi API |
| Documa → Rust Office | 已檢查的 16 個 native／manifest 檔及 Python wrapper 相同 | 未發現下游獨有的程式修正 |

### 保留的差異

- Rust PDF 源頭有直接影像 OCR、CLI／Python／WASM OCR 入口與影像 mask metadata；
  Documa 副本尚未納入這組功能。同步後剩餘 10 個共享檔差異及 4 個源頭獨有檔案，
  均屬此功能落差或 Cargo 打包範圍。這些不能反向用 Documa 副本覆蓋源頭。
- Documa 只打包 Rust core／Python binding；LingXi 只打包 core／Python binding
  及核准模型。CLI／WASM／FFI／訓練或開發測試的省略是封裝範圍差異。
- Documa 的 HTTP(S)／Drive 接入、`source_url` 收據、遠端原始頁連結與無文字
  讀取狀態已在 Documa Python 源頭，SkillNow 固定 sdist 還未同步：5 個既有檔差異，
  且缺少 `src/documa/sources.py`。這是源頭更新尚未往下游發布的反向落差。
- 本輪保留 Documa 原有的未提交 Python 遠端來源功能；未用 SkillNow 的舊副本覆蓋。

## 修正與驗證

本次寫入 Rust PDF 源頭 9 個檔案（5 個實作／4 個測試）；Documa 的 `font.rs`
和 native regression fixture 同步修正，另加 `tests/test_rust_pdf_encoding_differences.py`。

- 獨立源頭 `cargo test --locked --workspace`：236 passed、1 個既有手動 benchmark ignored。
- 源頭 `cargo fmt --all -- --check` 與 workspace all-target Clippy `-D warnings`：PASS。
- Documa 源頭 Rust adapter、native binding、字型參照、Markdown reflow、標題結構：81 passed。
- Python 模組直接由 `rust-pdf_parser` 源頭編譯，沒有沿用前輪下游模組。
- Documa 源頭實跑使用者原始 PDF：`rust_pdf`、19 頁、0 個 U+FFFD、warnings 空；
  原檔 SHA-256 不變。測試使用 `keyword_provider=ngram`，不呼叫模型。
- 回補後，`font.rs`／座標／分段修正與對應 core 測試的正規化內容一致；
  SkillNow sdist 的 native runtime source 亦已與 Documa 相符。

原始修改前檔案與逐檔 SHA-256 收據、Rust logs、源頭編譯模組及原 PDF 驗證收據
位於 SkillNow platformdirs 資料目錄的 `diagnostics/upstream-sync-20261012`。
未修改使用者 PDF 或既有專案資料；未更新已安裝 runtime／App、未重打包、未提交／推送。
獨立 `rust-pdf_parser` 目前不是 Git repository；「源頭已修」指本機 canonical source。

## 範圍與可重跑證據

本次涵蓋 Documa 內嵌 PDF／Office／LingXi 的 Rust 程式、原生測試、Cargo manifests、
Python wrappers，以及 SkillNow 固定 Documa sdist 的 `src/`／`native/` 原始碼。
換行以 LF 正規化後逐檔雜湊，差異附 unified diff；沒有只依版號判斷。
未宣稱檢查所有第三方套件、所有歷史版本、SkillOps skills 或跨平台安裝產物。

在 SkillNow 工作區執行：

```text
python scripts/audit_native_upstream.py --documa <Documa-root> --pdf <rust-pdf-root> --office <rust-office-root> --lingxi <rust-Lingxi-root> --archive <SkillNow-source/vendor/documa/documa-0.9.0.tar.gz> --output <report.json>
```

完整逐檔證據：`docs/evidence/upstream-audit-20261012-before.json` 與
`docs/evidence/upstream-audit-20261012-after.json`。後者另補齊來源獨有檔案及 Python
wrapper 比對，因此整體列數較前者多；共享檔案的前後雜湊仍可直接比較。
