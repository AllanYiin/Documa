# rust-office-parser

以 Rust 為核心的本機 Office 結構化抽取器，供 Documa 與文件檢索流程使用。
目前套件版本 **0.2.0**，事件契約維持 `office-layout-v1`。
本文供整合 Python／CLI 的開發者入門；參數與資料語意見 [API reference](docs/api-reference.md)。

## 功能概覽

- DOCX：段落、runs、標題、修訂檢視、表格跨度與巢狀表格、頁首頁尾、註腳／尾註／註解關聯、超連結與圖片關聯。
- XLS（BIFF8）／XLSX：型別值、顯示文字、數字格式、日期系統、公式與快取狀態、合併範圍、隱藏工作表／列／欄、範圍選取與列批次。XLSX 額外提供 Tables、defined names、圖片 anchor 與圖表資料。
- PPTX：實際投影片順序、隱藏狀態、relationships 備註配對、文字／表格／圖片 bbox、圖表儲存數據與 SmartArt 文字。
- 共用：穩定來源引用、JSON Schema、可設定資源限制、具錯誤碼的 Python 例外。
- CLI：單檔／批次解析、JSONL／Markdown／CSV、圖片匯出、基礎 benchmark。

## 安裝與前置條件（Prerequisites / Requirements）

Python 3.9 以上；原始碼建置依 `rust-toolchain.toml` 使用 Rust 1.96.0，另宣告 MSRV 1.88 並由 CI 檢查。
Windows 原始碼建置需要 MSVC C++ Build Tools。命令於本 repo 根目錄執行。

```powershell
python -m pip install maturin pytest jsonschema psutil xlwt xlrd python-docx openpyxl python-pptx
maturin build --release --locked --out dist/v0.2.0
python -m pip install --force-reinstall dist/v0.2.0/rust_office_parser-0.2.0-cp39-abi3-win_amd64.whl
```

上例 wheel 檔名適用 Windows x64；Linux／macOS 使用當次建置產生的 wheel 路徑。

## Quick Start — 使用方式

```python
import rust_office

assert rust_office.version_info() == ("0.2.0", "office-layout-v1")
for event in rust_office.open("fixtures/smoke.xlsx", sparse=True, batch_rows=500):
    print(event["event"])
```

預期依序取得 `document_start`、一或多筆 `unit`、選用的 `asset` 與 `document_end`。
解析錯誤會在 `open()` 回傳事件流之前拋出，不交付部分成功文件。

```powershell
cargo run -p office-cli -- parse fixtures/smoke.xlsx --range A1:C10 --format csv --output tmp/cells.csv
cargo run -p office-cli -- batch fixtures --output-dir tmp/export --format markdown
cargo run -p office-cli -- parse fixtures/pptx-image-link.pptx --assets-dir tmp/images --output tmp/slides.jsonl
```

`fixtures` 含刻意損壞的測試檔，所以上述 batch 範例會產生成功檔案及 `batch-report.json`，並以 exit code 2 回報未全部成功。
輸出檔案若已存在會覆寫，請使用專用輸出目錄。

## 大型文件與能力邊界

- 所有解析純本機，不執行巨集、不啟動 Office、不取得外部連結。
- Word 使用 logical flow、工作表使用 cell grid，沒有假造的渲染頁碼；PPTX 使用 points bbox。
- XLSX 的 `sparse=True` 避免建立大面積空白矩陣；`sheets`／`cell_range` 控制輸出，`batch_rows` 切分 table blocks。
- `asset_mode="metadata"` 保留雜湊與來源、不展開及 Base64 編碼圖片到輸出；ZIP 完整性驗證仍會讀取解壓縮串流。
- 採「原子解析、延遲事件序列化」，仍會保留完成的 IR；不是常數記憶體或隨解析即交付的串流。
- XLS 仍由 calamine 建立 workbook；對 BIFF 的稀疏模式只減少輸出資料，不移除底層 workbook 分配。
- 公式不重算，cached value 的新鮮度無法保證；完整 Excel 顯示格式、圖形渲染、任意 Word 排版皆不保證。近似／缺失資訊有 flags 或 warnings。
- DOC、PPT、macro-enabled OOXML 及加密文件不支援。表單／工作表編輯保護不等於內容加密。

## 驗證

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build -p office-cli --locked
python -m pytest -q tests
python scripts/benchmark_scaling.py --out reports/scaling.json
python scripts/corpus.py check --require-real-world
```

最後一條命令要求已授權且經核對 golden 的真實文件。公開 repo 僅包含 24 件合成 fixtures；私人語料以 `DOCUMA_OFFICE_CORPUS_DIR` 指定本機目錄，未提供時此必要關卡會失敗。原始文件、golden、授權與執行報告均不納入公開提交。
三平台 CI 會保存 wheel、pytest XML 與 scaling 報告，詳見 [驗證與發布](docs/validation.md)。

## 常見錯誤與維護

- `INVALID_OPTIONS`：檢查拼字、支援的模式、正數 limits 與 A1 範圍。
- `INPUT_LIMIT_EXCEEDED`／`PARSE_LIMIT_EXCEEDED`：縮小範圍、使用 sparse，或依可信輸入調整明確 limits；XLS 的底層分配限制仍會生效。
- Cargo registry 寫入遭拒：可將 `CARGO_HOME` 設為工作區內可寫目錄，使用已有的可信 Cargo cache；不需放寬全域權限。
- CLI 失敗以 JSON 寫入 stderr、exit code 2；批次每檔獨立，失敗不阻止其他檔案。

維護者修改 IR／選項時，應同步更新 [IR 契約](docs/office-layout-v1.md)、[API reference](docs/api-reference.md)、Schema 與回歸測試。
0.1 使用者請先閱讀 [0.2 遷移說明](docs/migration-0.2.md)。
在 Documa monorepo 中，本 workspace 位於 `native/office`，facade 同步於 `src/rust_office`；版本與 error/options 轉接詳見 [整合說明](../../docs/RUST_OFFICE_0_2_SYNC_20261012.md)。
