# 從 0.1 升級至 0.2

## 升級摘要與相容性（Compatibility）

適用使用 Python facade、CLI 或 `office-layout-v1` IR 的整合者。升級前保留原 wheel 與既有索引，可在獨立環境測試新 wheel。

## 不相容變更與行為修正

- `version_info()` 變成 `("0.2.0", "office-layout-v1")`；原有事件種類維持不變。
- 錯誤仍可用 `except ValueError` 捕捉，新增 `OfficeError.code/message/recoverable/context`。CLI 錯誤改寫 stderr，仍以 2 結束。
- 無效選項現在會拒絕，不能再把未實作模式當作任意 metadata 字串傳入。
- PPTX 依實際簡報順序而非 part 檔名；預設排除隱藏投影片；備註依 relationships 配對。
- DOCX/PPTX 合併表格只有 anchor 含文字，覆蓋格為 null；`metadata.cells` 保存跨度。巢狀表格為獨立 table，附 parent／nested 關聯。
- 修正 XML entity、段落分隔與圖片關聯，因此 block／asset IDs 或文字可能與 0.1 不同。請依 parser 版本使下游索引失效並重建。
- spreadsheet cell records 增加欄位；請檢查所需 key，不要對整個 record 使用固定 key 集合相等判斷。
- `sparse=True` 是 opt-in，此時 `Table.rows` 為空，讀取 `metadata.cell_records`。
- 原生 `_core` 是私有介面；請使用 `rust_office.open()`，不要依賴 native function signature。

## 升級步驟與驗證（Verify）

1. 依 README 建置並在測試環境安裝 0.2 wheel。
2. 執行 `python -m pytest -q tests`、代表文件的內容／結構比對。
3. 檢查 warnings、合併表格語意、公式 cache 狀態及 PPTX 重新排序／隱藏案例。
4. 在配對的 Documa 專案驗證 provider 契約，再重建索引；本 repo 的測試不替代該整合測試。

```python
import rust_office
assert rust_office.version_info() == ("0.2.0", "office-layout-v1")
assert rust_office.capabilities()["formats"]["xlsx"]["supported"]
```

## 回滾與失敗排查

發現下游不相容時，在原環境重新安裝保留的 0.1 wheel 並使用相符的索引／快取。
不要混用新 parser 的 IDs 與舊索引。配對 Documa 的 DOCX/PPTX 可依其 provider 設定回到 Python；XLS/XLSX 沒有該 fallback。
已知限制（Known issues）：跨平台 CI、真實授權語料尚未通過時，應保留發布阻擋狀態，而非以本機成功代替。

維護者更新此頁時，應對照版本、Schema、Python facade 與回歸測試；本頁最後核對版本為 0.2.0。
