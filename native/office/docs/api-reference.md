# Python／CLI API reference — 0.2.0

供使用 `rust_office` 與 `office-cli` 的整合者查詢。已安裝 0.2.0 wheel 後可使用本頁 API；建置見 [README](../README.md)。

## API 概覽

`version_info()` 回傳 `(package_version, contract)`；`capabilities()` 回傳支援格式、模式與安全邊界；`schema()` 回傳完整事件 JSON Schema。
`detect_format(path)` 檢查副檔名與容器 signature，不代表已完成內容解析。

使用範例（Example）：

```python
from io import BytesIO
from pathlib import Path
import rust_office

source = Path("fixtures/smoke.xlsx")
stream = rust_office.open(
    BytesIO(source.read_bytes()), source_name="smoke.xlsx",
    options={"include_hidden": False},
    cell_range="A1:C20", sparse=True, batch_rows=500,
    limits={"max_input_bytes": 20_000_000, "max_cells": 100_000},
)
for event in stream:
    print(event["event"])
```

`open(source, options=None, *, source_name=None, **overrides)` 接收路徑、`bytes` 或 binary file-like object。
file-like 從目前游標讀取，不關閉呼叫者的 stream；bytes 必須提供帶副檔名的 `source_name`，file-like 可使用其字串 `.name`。
關鍵字 overrides 覆蓋 options 同名值；`limits` 是整個物件覆蓋，再由 Rust 對缺欄位套預設。
回傳 `OfficeEventStream`；`.metadata` 等於第一筆事件，`.remaining()` 包含尚未取出的 start／end。

## 選項

| 選項 | 預設 | 語意 |
| --- | --- | --- |
| `extract_images` | `True` | 是否輸出圖片 asset；結構中仍可能有圖片引用 |
| `asset_mode` | `inline` | `inline` 含 Base64；`metadata` 只含 hash、MIME、來源與大小 |
| `include_hidden` | `False` | 排除隱藏工作表／列／欄、隱藏投影片與 Word hidden runs |
| `revision_mode` | `final` | DOCX `final` 排除刪除／moveFrom；`original` 排除新增／moveTo；`all` 保留兩者的文字 |
| `formula_mode` | `formula_and_cached_value` | `formula` 讓 cell `value` 使用公式；`cached_value` 隱藏公式欄；兩者都不執行公式，raw cached value 仍可供查核 |
| `external_links` | `metadata_only` | `ignore` 排除外部 URL metadata；任何模式皆不連網 |
| `sheets` | `[]` | XLS／XLSX 工作表名稱清單；空清單代表全部，未知名稱回傳 `SHEET_NOT_FOUND` |
| `cell_range` | `None` | XLS／XLSX A1 或 A1:B20 範圍；每張選定工作表套同一範圍 |
| `batch_rows` | `0` | 每個工作表 table block 的列跨度；0 表示不分批，最大 1048576 |
| `sparse` | `False` | 稀疏輸出以 `cell_records` 為準，`Table.rows=[]`，空白列不配置 |
| `limits` | 見下表 | 未知欄位、非物件、零或負數會被拒絕 |

`batch_rows` 切分 IR table blocks，不改變「一張工作表一個 unit event」；`sheets` 與 `cell_range` 控制選取，仍會掃描必要的來源結構。

## 資源限制

| 欄位 | 預設 |
| --- | ---: |
| `max_input_bytes` | 536870912 |
| `max_parts` | 100000 |
| `max_part_bytes` | 268435456 |
| `max_uncompressed_bytes` | 2147483648 |
| `max_compression_ratio` | 100 |
| `max_cells` | 10000000 |
| `max_shapes` | 1000000 |
| `max_text_chars` | 200000000 |
| `max_xml_depth` | 128，最大可設 256 |

ZIP limits 在容器完整性檢查時執行。文字／cells／shapes 於解析中累計；XML 結構 parts 另有深度及局部文字限制。
XLS 在 calamine 開啟前檢查 BIFF cell count 與 dense dimensions；因此小輸出範圍不會繞過 XLS 底層分配限制。

## 回應與重要資料欄位

- `metadata.cell_records`：`address`、`row`、`column`、`data_type`、`raw_value`、`value`、`display_text`、`display_exact`、`number_format`、`date_system`、`date_iso`、`formula`、`cache_status`、`source_ref`。
- `raw_value` 保留數字／布林／字串／null；`value` 是相容的字串／null；`display_exact=false` 表示不支援該 Excel 格式，不能當成畫面完全相同的文字。
- `cache_status` 為 `not_applicable`、`missing`、`present_unverified`。無快取發出 `FORMULA_CACHE_MISSING`，有快取也不保證新鮮。
- DOCX/PPTX `metadata.cells` 具一基底 `row`／`column`、`rowspan`／`colspan`、`text`、`source_ref`；合併覆蓋格為 null。DOCX 還有 `paragraphs` 與 `nested_table_ids`。
- XLSX `unit.metadata.tables` 為 Excel Tables；document `defined_names` 保留名稱及來源公式，並不執行動態 named formula。
- 圖片使用 `asset_ids` 或 worksheet `images[].asset_id` 連結，XLSX images 包含 anchor，Word／PPTX 保留可得的 alt text。
- `annotation_refs` 連到 Word annotation block 的 `annotation_key`。註解作者與日期保留 metadata。
- `graphics` 保留圖表標題、series、分類、儲存數值與來源公式，以及 SmartArt 文字；不渲染、不執行嵌入 workbook、不補算缺失 cache。

## 錯誤與診斷

```python
try:
    rust_office.open("missing.docx")
except rust_office.OfficeError as error:
    assert isinstance(error, ValueError)
    print(error.code, error.message, error.recoverable, error.context)
```

模式、範圍、limits 錯誤使用 `INVALID_OPTIONS`；IO 使用 `OFFICE_OPEN_FAILED`；來源內容問題保留明確格式／ZIP／XML 錯誤碼。
`recoverable` 是分類資訊，不代表應無條件切換解析器。
近似格式、推定座標與未完整理解的 Word 結構會產生 warnings；warnings 不會將結果冒稱為完全保真。

## CLI

```powershell
cargo run -p office-cli -- --help
cargo run -p office-cli -- parse --help
cargo run -p office-cli -- schema
cargo run -p office-cli -- parse fixtures/smoke.xlsx --sparse --batch-rows 500 --options-json '{"limits":{"max_cells":100000}}'
```

`parse` 支援 `--include-hidden`、`--no-images`、`--sheet`（可重複）、`--range`、`--batch-rows`、`--sparse`、模式參數、常用限制與 `--options-json`。
`--output` 指定輸出，省略為 stdout；`--format` 為 `jsonl`／`markdown`／`csv`。
CSV 採 `unit,table,row,column,value` 長表，保留工作表儲存格的絕對 row／column；Markdown 是文字／表格匯出，不是版面渲染。
`--assets-dir` 需要 inline assets，輸出 SHA-256 命名檔與 manifest，避免 Office 內的檔名成為任意寫入路徑。

`batch DIRECTORY --output-dir DIR [--recursive] [--export-assets]` 依排序逐檔執行、保留相對目錄、不跟隨符號連結；成功與失敗列於 `batch-report.json`。
單檔失敗不產生該檔解析輸出；輸出過程的 IO 失敗仍可能留下未完成檔案。

## 相容性與維護

適用 0.2.0／`office-layout-v1`。支援值以 `capabilities()` 與解析器驗證為準；加入新欄位時同步更新 Schema、`types.py` 與 tests。
限制及遷移見 [migration guide](migration-0.2.md)，發布證據見 [validation](validation.md)。
