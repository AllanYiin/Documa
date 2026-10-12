# Office Layout IR v1

`rust_office.version_info()` 必須回傳套件版本與固定契約 `office-layout-v1`。事件順序為：

1. 一個 `document_start`
2. 零或多個 `unit`
3. 零或多個 `asset`
4. 一個成功的 `document_end`

每個 block 都有穩定 `id`、`order_index`、`source_refs`、`confidence` 與來源 metadata。所有 ID 與閱讀順序必須由文件內容及結構決定，不得依平行執行或雜湊表順序改變。

## 座標與引用

| 格式 | logical unit | coordinate space | citation |
| --- | --- | --- | --- |
| DOCX | 整份文件 flow | `logical_flow` | structural |
| XLS/XLSX | worksheet | `cell_grid` | worksheet/range structural |
| PPTX | slide | `slide_points` | slide + points bbox |

Word 與 worksheet 不具有可靠的渲染頁碼，因此不得產生虛構 bbox。PPTX bbox 為 `[x0,y0,x1,y1]`，單位 points。

## 安全與資源限制

Parser 不執行巨集、不啟動外部程序、不存取網路，external relationships 僅保留 metadata。預設限制涵蓋輸入大小、ZIP part 數、單一與累積展開大小、壓縮比、XML/文字大小、cell 與 shape 數量。超限、損壞、加密與不支援格式皆回傳穩定錯誤且不得部分成功。

主要錯誤碼：

- `LEGACY_OFFICE_NOT_SUPPORTED`：DOC/PPT。
- `MACRO_ENABLED_OFFICE_NOT_SUPPORTED`：DOCM/XLSM/PPTM。
- `ENCRYPTED_OFFICE_NOT_SUPPORTED`：內容加密；編輯保護不等同加密。
- `XLS_BIFF_VERSION_NOT_SUPPORTED`：非 BIFF8 XLS。
- `INPUT_LIMIT_EXCEEDED`、`ZIP_LIMIT_EXCEEDED`、`ZIP_PATH_TRAVERSAL`：資源/容器限制。
- `OOXML_*_INVALID`、`SPREADSHEET_OPEN_FAILED`：損壞或結構不合法。


## 0.2 擴充與序列化

事件結構維持 v1，完整 Schema 由 `rust_office.schema()` 或 CLI `schema` 提供；原始定義位於 `crates/office-core/src/schema.json`。
Python native stream 在完整解析成功後，逐筆序列化事件；不預先建立全部 JSON 字串。損壞來源或超限仍在第一筆事件交付前失敗。
CLI 亦逐筆轉換事件，單檔輸出 IO 失敗不能被視為成功完成。

- 合併表格的覆蓋格為 null；跨度與每格來源存於 `metadata.cells`。
- `sparse=true` 的 worksheet table `rows=[]`；實際值與來源存於 block `metadata.cell_records`。
- `batch_rows` 只切分 table blocks，不改變 unit event 的工作表語意。
- 圖片 metadata mode 保留 hash 與來源，`data_base64=""` 並設 `metadata.data_omitted=true`。
- 增加型別值、display/cache 狀態、圖片／超連結／註解關聯及 graphics metadata，詳見 [API reference](api-reference.md)。
- 同版 parser 對相同內容的 IDs 與順序穩定；0.2 修正解析語意後，不能假設與 0.1 的 IDs／閱讀順序相同。下游應以 parser version 使快取失效。
