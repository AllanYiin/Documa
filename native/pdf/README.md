# rust-pdf-parser

從零實作、唯讀、以「正確抽取內文」為第一優先的 Rust PDF parser。核心不依賴
`lopdf`、`pdf-rs`、PDFium、MuPDF、Poppler 或其他 PDF-aware parser；同一份 safe Rust
`pdf-core` 支援 Rust library、`rust-pdf` CLI、Python wheel 與 browser WASM package。

目前版本：`0.2.0`（Stage 11）。適合需要 Unicode 內文、來源／閱讀順序、文字品質
metadata 或 Image XObject 擷取，但不需要頁面渲染、OCR、加密或編輯 PDF 的開發者。

## Overview／概覽

0.2.0 的主要 API 是 V2 文字抽取契約：

- `content-order`：保留 content-stream traversal order，不加入 geometry separator。
- `layout`：保留 0.1.x 的既有座標排序，供 legacy compatibility。
- `auto`：用 font metrics、script、rotation 與有界 ambiguity fallback 重建可讀文字。
- 有效 `/ActualText` 優先於 ToUnicode；無效或缺失 mapping 會輸出可追蹤 warning。
- V2 結果包含 pages、spans、positioned glyphs、separator provenance、warnings，以及可選
  quality counters。

四個 front end 只轉換 options、DTO 與 errors；所有 PDF-aware 規則、limits 與
machine-readable codes 都由 `pdf-core` 擁有。

Stage 12 的 Documa 替換計畫目前已完成 Stage 0–5 與 Stage 6A–6D：包含 opt-in
shadow adapter、page-local semantics、真正 lazy 的 `native_events_v2`、terminal patch
stream，以及 Documa `compact_trace_v1` metadata。三次正式量測的完整 adapter RSS 已降至
PyMuPDF 的 1.056367x，通過 1.2x memory gate；Rust Documa 為 34.704637 pages/s，約快
5.807007x。Stage 7.2 已拆開 raw parser 與 Documa table rewriting，raw
character/bigram F1 為 0.998954/0.996075，文字完整性 gate 已通過；舊有 adapter
character F1 0.960813 不再當作 parser text truth。預設 provider 仍未切換，因為私有
human-order 雙人 gold 尚未完成、tagged-order proxy 0.940546 僅屬診斷證據，且私有
table/image gold 尚未滿足 Go/No-Go 條件。Stage 7.3 的 validator/scorer、公開 gold 與
7 文件／28 頁私人審閱包及本機 annotation workbench 均已完成；工作台提供鎖定的
Reviewer A／B 獨立標註、點選閱讀順序、頁首頁尾／頁碼分類、manifest 匯入合併與
coded adjudication，draft 正確回報 `human_order_review_incomplete`；Stage 7.4 禁止開始。

## Prerequisites／Requirements／前置條件

- Rust stable 1.88 以上；repository 預設 toolchain 記錄於 `rust-toolchain.toml`。
- 建立 Python package 時需要 Python 3.9+、maturin 與 pytest。
- 建立 browser package 時需要 `wasm32-unknown-unknown` target、wasm-pack 與 Node。
- 本 parser 將所有 PDF bytes 視為不可信輸入；解析外部檔案時仍建議使用低權限 process。

## Installation 與五分鐘 Quick Start

從 CLI 以 Auto mode 抽取純文字：

```powershell
cargo run -p pdf-cli -- extract path/to/input.pdf --mode auto
```

取得完整 V2 JSON：

```powershell
cargo run -p pdf-cli -- extract path/to/input.pdf --mode auto --json
```

成功時，純文字寫到 stdout；JSON 頂層包含 `mode`、`text`、`pages`、`warnings`、
`glyphs`、`separators` 與 `quality`。失敗時 stderr 為含 `code`、`offset`、`message`
的 JSON，process exit code 非零。

其他 CLI usage：

```powershell
cargo run -p pdf-cli -- inspect path/to/input.pdf --json
cargo run -p pdf-cli -- object path/to/input.pdf 12 --generation 0 --json
cargo run -p pdf-cli -- validate path/to/input.pdf --json --diagnostics
cargo run -p pdf-cli -- version --json
```

## Usage：Rust library

V2 Auto extraction：

```rust,no_run
use pdf_core::{ExtractionMode, PdfDocument, TextExtractionOptionsV2};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = std::fs::read("input.pdf")?;
    let document = PdfDocument::parse(&bytes)?;
    let result = document.extract_text_v2(TextExtractionOptionsV2 {
        normalize_unicode: false,
        mode: ExtractionMode::Auto,
        include_quality_metadata: true,
    })?;

    println!("{}", result.text);
    if let Some(quality) = result.quality {
        eprintln!("inserted spaces: {}", quality.inserted_spaces);
    }
    for warning in result.warnings {
        eprintln!("{}: {}", warning.code, warning.message);
    }
    Ok(())
}
```

`normalize_unicode: false` 保留 ToUnicode mapping 的原始 Unicode；只有 caller 明確設為
`true` 才套用 NFC。Auto 可在可讀文字中做 CJK compatibility decomposition，但
`glyphs[].unicode` 保留原始值。

0.1.x legacy API 保留原行為：

```rust,no_run
use pdf_core::{PdfDocument, TextExtractionOptions};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = std::fs::read("input.pdf")?;
    let document = PdfDocument::parse(&bytes)?;
    let legacy = document.extract_text(TextExtractionOptions {
        normalize_unicode: false,
        layout: true,
    })?;
    println!("{}", legacy.text);
    Ok(())
}
```

`layout: false` 對應 V2 `ContentOrder`；`layout: true` 對應 V2 `Layout`，不會自動切換
成 `Auto`。

擷取影像而不渲染頁面：

```rust,no_run
use pdf_core::PdfDocument;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = std::fs::read("input.pdf")?;
    let document = PdfDocument::parse(&bytes)?;
    for image in document.extract_images()? {
        println!("page={} name={} {}x{} format={:?}",
            image.page_index + 1, image.resource_name,
            image.width, image.height, image.format);
    }
    Ok(())
}
```

## Usage：CLI

`--mode` 啟用 V2；未提供 `--mode` 時維持 legacy output shape。`--mode` 與
`--no-layout` 互斥：

```powershell
rust-pdf extract input.pdf --mode content-order --json
rust-pdf extract input.pdf --mode layout --json
rust-pdf extract input.pdf --mode auto --normalize-unicode --json
rust-pdf layout input.pdf --json
```

plain mode 的文字只寫 stdout，warnings 寫 stderr，適合 pipeline。`validate
--diagnostics` 可輸出文件級 decode/cache metrics，但不會記錄 PDF 內文。

## Usage：Python

建立 release wheel：

```powershell
maturin build --release --manifest-path bindings/python/Cargo.toml
```

安裝後呼叫 V2：

```python
from pathlib import Path
import rust_pdf

data = Path("input.pdf").read_bytes()
result = rust_pdf.extract_v2(
    data,
    mode="auto",
    normalize_unicode=False,
    quality=True,
)
print(result["text"])
print(result["quality"])

layout = rust_pdf.extract_layout(data)
assert layout["schema_version"] == 1
assert layout["coordinate_space"] == "layout_unrotated_top_left"
# Tagged PDFs may expose author structure order independently of source order.
print(layout["pages"][0]["orders"]["tagged_order"])

# 避免同時保留整份 Layout JSON 與 Python dict；每次迭代只解碼一頁。
stream = rust_pdf.extract_layout_stream(data)
assert stream.metadata["coordinate_space"] == "layout_unrotated_top_left"
for page in stream:
    print(page["page_number"])
```

Legacy `rust_pdf.extract_text(data)` 與 `rust_pdf.extract(data, layout=True)` 保持 0.1.x
行為。`rust_pdf.PdfParseError` 的 message 是含 `code`、`offset`、`message` 的 JSON；
`rust_pdf.extract_images(data)` 回傳 image metadata 與 bytes。
目前 `extract_layout_stream()` 使用真正逐頁的 `native_events_v2`；頁面迭代結束後
`stream.metadata` 才是最終值，跨頁 furniture patches 可由 `stream.finalizations()`
逐頁取出。完整 adapter RSS 與品質 gate 尚未通過，因此這不代表可切換預設 provider。
## Usage：Browser WASM

建立 web-target package：

```powershell
rustup target add wasm32-unknown-unknown
wasm-pack build bindings/wasm --target web --release
```

```javascript
import init, {
  extractWithOptions,
  extractText,
  extractImages,
  extractLayout,
  versionInfo,
} from "./pkg/pdf_wasm.js";

await init();
const bytes = new Uint8Array(await file.arrayBuffer());
const result = extractWithOptions(bytes, {
  mode: "auto",
  normalizeUnicode: false,
  quality: true,
});
console.log(result.text, result.quality, versionInfo());

const layout = extractLayout(bytes, {});
console.log(layout.schema_version, layout.coordinate_space);
console.log(layout.pages[0].orders.tagged_order);

// 0.1.x legacy signature remains available.
console.log(extractText(bytes, false, true));
```

WASM 不做檔案 I/O，caller 必須傳入 `Uint8Array`。預設 limits 與 native core 相同，
無 hidden thread、global cache 或 telemetry。

## ActualText、warnings 與 quality

Unicode precedence：有效 `/ActualText` → 有效 ToUnicode → 明示 font fallback → U+FFFD。
V2 `quality` 有五個穩定 counters：

| Field | Meaning |
|---|---|
| `inserted_spaces` | Auto 根據 geometry 合成的空白數 |
| `inserted_line_breaks` | Auto 合成的換行數 |
| `fallback_glyphs` | 使用 font fallback 的 glyph 數 |
| `replacement_characters` | U+FFFD 或 ActualText replacement 的計數 |
| `ambiguous_boundaries` | 無法高信心排序、已局部回退的邊界數 |

可回復 fidelity 問題透過 stable warning code 回報，例如
`actual_text_invalid`、`font_fallback_encoding`、`unicode_mapping_invalid`、
`unicode_mapping_missing` 與 `reading_order_ambiguous`。程式必須依 `code` 分支，不可依賴
human-readable message。

## 支援範圍、limits 與重要限制

已實作：

- basic object、classic/incremental/xref stream、hybrid xref、object stream。
- FlateDecode、TIFF Predictor 2、PNG Predictor 10–15。
- page tree、inherited resources、text operators、nested Form XObject。
- ToUnicode `bfchar`／`bfrange`、simple／CID font metrics、positioned glyphs。
- marked content、direct／named／indirect property list 與 `/ActualText` replacement。
- bounded tagged structure: StructTreeRoot/K/Pg, RoleMap, ParentTree Nums/Kids, MCID, Alt, and Artifact metadata.
- 三種 extraction modes、script-aware Auto、warnings 與 quality metadata。
- 文件生命週期 DecodeBudget、clone-shared bounded object-stream LRU cache。
- JPEG Image XObject validation／原始位元流擷取與 Flate raw samples。
- schema-versioned Layout IR、四種明示 order、table/cell topology、painted image
  placements、Figure/Caption 關聯，以及 Link/destination/outline metadata。

所有 input-derived file/object/stream/page/content/CMap/text/image 數量與 decoded output 都受
`ParseLimits` 約束。預設單一 decoded stream 上限 256 MiB、文件總 decoded bytes 512 MiB、
object-stream cache 64 MiB／256 entries；完整欄位見
[compatibility reference](docs/compatibility.md#decode-與-cache-limits)。

明確不支援：

- 頁面渲染、OCR、PDF 編輯。
- 加密或密碼保護 PDF。
- 損毀 xref 的猜測式 repair。
- LZW、ASCII85、RunLength、CCITT、JBIG2、JPEG 2000 解碼。
- complete PDF 2.0 tagged-PDF normative conformance, OBJR ordering, and stream-associated MCR ordering.

layout/Auto 是文字抽取 heuristic，不是渲染結果；重疊文字、多欄、混合方向或缺少字型
資訊時可能回 warning 並保守保留來源內容。

## 從 0.1.x 升級

Legacy Rust、CLI、Python 與 WASM 呼叫仍維持原 signature 與 output shape。要取得改善後的
內文，請明示使用 V2 `Auto`。Rust 端若以完整 struct literal 建立 `ParseLimits`，需補上
`max_cached_object_stream_bytes`、`max_cached_object_streams`，或改用
`..ParseLimits::default()`。完整步驟與回退方式見
[0.2.0 migration guide](docs/migration-0.2.md)。

## 驗證與開發

完整 release gate：

```powershell
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo test --workspace --doc
cargo check -p pdf-wasm --target wasm32-unknown-unknown
cargo clippy -p pdf-wasm --target wasm32-unknown-unknown -- -D warnings
wasm-pack test --node bindings/wasm
cargo deny check advisories bans licenses sources
cargo audit
```

Fuzzing 需要 nightly：

```powershell
cargo +nightly fuzz build parse_document --fuzz-dir fuzz
cargo +nightly fuzz run parse_document --fuzz-dir fuzz -- -max_total_time=60
```

Stage 11 acceptance、private corpus、benchmark、fuzz、MSRV 與 supply-chain 證據見
[Stage 11.6 validation matrix](tests/fixtures/stage11/validation-matrix.md)。私有 PDF 只透過環境
變數載入，不會提交 repository。

## Workspace

| Path | Responsibility |
|---|---|
| `crates/pdf-core` | 所有 PDF syntax、xref、page、font、text、image 與 limits 規則 |
| `crates/pdf-cli` | 檔案 I/O、參數與 plain／JSON 輸出 |
| `bindings/python` | Python type、error、result 轉換 |
| `bindings/wasm` | JavaScript type、error、result 轉換 |
| `tests/fixtures` | 可散佈 fixtures、manifest 與 release evidence |
| `tests/real-world` | private corpus contract，不含 PDF 本體 |
| `fuzz` | untrusted-input fuzz targets |
| `docs` | 架構、相容性、安全、錯誤、文字 fidelity、migration 與 release 文件 |

更多資訊：[architecture](docs/architecture.md)、[text fidelity](docs/text-fidelity.md)、
[errors](docs/errors.md)、[security](docs/security.md)、[release runbook](docs/release.md)。