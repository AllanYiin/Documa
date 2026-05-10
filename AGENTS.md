請以繁體中文溝通。

Documa 第一階段定位為 LLM-ready document understanding package，不納入 UI 開發。

開發原則：

- 優先維持向前相容與穩定介面。
- 不從零實作 PDF parser；底層 parser 透過 adapter 接入。
- Core 不直接依賴特定 parser 物件。
- 內部文字一律使用 Unicode `str`。
- 檔案與 JSON 輸出預設 UTF-8，JSON 使用 `ensure_ascii=False`。
- 保留原始文字與正規化文字，不可靜默覆蓋原文。
- CLI、MCP、tool-calling schema 都應回傳結構化結果。
- 新增物件時同步考慮 update/delete/state 與測試。

