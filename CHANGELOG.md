# Changelog

## Unreleased — IR 0.2：identity, citation, OCR, quality（2026-07）

從「能處理文件」到「能支援可稽核的 agent 回答」的一輪功能擴充。IR 升 0.2（純 additive，0.1 檔案完全相容，契約見 [docs/spec/ir-compatibility.md](docs/spec/ir-compatibility.md)）。

### 新增

- **Document registry 與 `documa ingest`**：content-addressed `document_id`（`doc-` + sha256 前 16 碼）、同內容去重、同路徑新內容自動 supersede。所有工具與指令的 `ir_path` 參數同時接受 document_id（路徑存在優先）。管理指令 `list-documents`、`delete-document --yes`、`ingest --rebuild-index`；索引損毀時自動備份為 `registry.json.corrupted`。
- **Citation / provenance 工具**（6 個新 MCP/LLM 工具，共 22 個）：`documa_cite_block`、`documa_cite_chunk`、`documa_render_citation`（page-bbox / markdown / inline）、`documa_source_window`、`documa_verify_citations`（id 存在性檢查，明確不做語義驗證）、`documa_validate_ir`。對應 CLI：`cite-block`、`cite-chunk`、`source-window`、`validate-ir`。無 bbox 的來源降級為 `grounding: "logical"`。
- **JSON Schema 與驗證**：`schema/documa.schema.json` 由 dataclass 生成（`scripts/generate_schema.py`，CI `--check` 閘門）；`documa validate-ir` 回報 JSON-pointer violations，並含語意檢查（頁碼、bbox 方向、未知 major 版本、巢狀深度上限 100）。
- **OCR（選配 `documa[ocr]`，RapidOCR/ONNX/CPU）**：`documa process --ocr` / `documa ingest --ocr`。低文字密度頁整頁辨識（native 雜訊塊移入 `suppressed_native_blocks`），一般頁只辨識內嵌圖片。產物標記 `origin: "ocr"`、`ocr_engine`、`ocr_confidence`；頁均信心 < 0.3 標 `ocr_low_confidence`。未安裝 extra 時優雅降級並在 `warnings` 回報。
- **品質評測**：`documa benchmark --mode quality` 對 `fixtures/pdf/gold/` 的標註計分——表格 TEDS / TEDS-S、閱讀順序 NED（皆與 OmniDocBench / docling-eval 同族指標）；孤兒 gold 目錄報 error。`documa diff` 輸出兩份 IR 的結構化差異。
- **Snapshot 回歸測試**：3 份自製真實版面 PDF（表格/雙欄/圖文混排）+ 1 份掃描件 fixture；pytest-regressions golden files 保護全 pipeline 輸出。
- **IR 0.2 欄位**：`producer_version`、`adapter_version`、`pipeline_profile`。

### 移除

- `JobState` enum（定義後從未使用的死碼；不影響任何 IR 檔案）。

### 已知限制

- 雙欄版面閱讀順序目前為列優先，quality benchmark 的 reading-order-multicolumn-001 case 為 failed（此為 benchmark 上線後量測到的真實 pipeline 問題，待 ReadingOrderStage 修正）。
- quality 門檻 0.85 為暫定值，待更多 gold 標註後校準；目前 gold 僅 2 個 case（表格、閱讀順序）。
- OCR 無 GPU 路徑；首次執行需下載模型（離線環境請預先於可連網機器執行一次並複製模型快取）。
- Registry 為單機單寫者 JSON index，不處理多程序併發。
