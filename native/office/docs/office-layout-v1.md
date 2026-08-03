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
- `ENCRYPTED_OFFICE_NOT_SUPPORTED`：加密/密碼保護。
- `BIFF_REVISION_NOT_SUPPORTED`：非 BIFF8 XLS。
- `INPUT_LIMIT_EXCEEDED`、`ZIP_LIMIT_EXCEEDED`、`ZIP_PATH_TRAVERSAL`：資源/容器限制。
- `OOXML_*_INVALID`、`XLS_OPEN_FAILED`：損壞或結構不合法。

