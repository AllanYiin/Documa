# rust-office-parser

以 Rust 為核心、供 Documa 使用的 Office 文件抽取器。公開契約為
`office-layout-v1`，正式支援 DOCX、XLS（BIFF8）、XLSX 與 PPTX。

## 能力邊界

- 純本機解析，不執行巨集、不啟動外部程式、不存取網路。
- DOCX 使用 logical-flow；worksheet 使用 cell-grid；PPTX 使用 slide-points。
- 公式只保留公式與檔案內 cached value，不重新計算。
- DOC、PPT、加密文件與 macro-enabled OOXML 會回傳穩定錯誤。
- 圖表、SmartArt、VBA 與嵌入物件只保留 inventory/metadata。

## 開發

```powershell
cargo fmt --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
maturin develop
python -c "import rust_office; print(rust_office.version_info())"
```

CLI：

```powershell
cargo run -p office-cli -- capabilities
cargo run -p office-cli -- parse sample.docx
```

## Python 契約

```python
import rust_office

assert rust_office.version_info() == ("0.1.0", "office-layout-v1")
for event in rust_office.open("sample.xlsx"):
    print(event)
```

`open()` 預設抽取圖片、排除 hidden units、使用 final revision view、回傳公式及檔案內 cached value，external links 僅保留 metadata。事件在 Python 端逐筆解碼，不建立第二份完整 JSON payload。

建立 wheel：

```powershell
maturin build --release --out dist
python -m pip install dist\rust_office_parser-0.1.0-cp39-abi3-win_amd64.whl
```

## Documa provider

配對的 Documa checkout 提供 `office_provider="auto" | "rust" | "python"`：

- `rust`：嚴格使用此 binding，任何錯誤都不回退。
- `python`：只支援既有 DOCX/PPTX adapters。
- `auto`：Rust-first；只有 binding/contract/能力明確缺失時，DOCX/PPTX 才回退 Python。
- XLS/XLSX 無 Python fallback；DOC/PPT 永遠回傳 `LEGACY_OFFICE_NOT_SUPPORTED`。
- 損壞、加密、路徑穿越與資源限制錯誤禁止 fallback。

Provider 實際選擇、fallback 與原因會寫入 `document.metadata.office_provider`；provider 或 binding 版本改變會使 search sidecar generation digest 改變。

## Fixtures 與 fuzz

```powershell
$env:PYTHONPATH=(Resolve-Path .fixture-deps).Path
python scripts\generate_fixtures.py
python scripts\benchmark_release.py --documa-root D:\PycharmProjects\Documa
cargo check --manifest-path fuzz\Cargo.toml --bins
cargo fuzz run ooxml -- -max_total_time=10
```

Fixture generator 會正規化 OOXML ZIP entry 與 core timestamps，確保重建 hash
穩定。目前 committed corpus 為 24 件，每種格式各 6 件，涵蓋基本結構、
Unicode、表格/公式、資產/連結，以及損壞與路徑穿越案例。manifest 同時記錄
SHA-256、coverage、provenance、license 與預期錯誤。

本機 Windows release report 位於
[`reports/office-v1-release.md`](reports/office-v1-release.md)：DOCX/PPTX
共同文字能力的 normalized character F1 均為 1.0，median parse time 與 peak
RSS gates 均通過。這份報告不替代 Windows/Linux/macOS CI 的實際執行，也不
替代日後加入的授權 real-world corpus。

契約與錯誤碼見 [Office Layout IR v1](docs/office-layout-v1.md)。

直接依賴與 fixture/benchmark 工具的授權盤點見 [Dependency license inventory](docs/dependency-licenses.md)。


