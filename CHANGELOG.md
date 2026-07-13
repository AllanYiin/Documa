# Changelog

## v0.3.0 — reading order v2, credible benchmark, evidence runtime, query efficiency（2026-07-13）

定位收斂為 **document evidence runtime for agents**：ingest → block reading → citation → verifiable answer。

### 新增（查詢效率與多文件檢索）

- **單文件查詢效率**：文件 LRU 快取（mtime/size 自失效，`DOCUMA_DOCUMENT_CACHE_SIZE` 可調）；`documa_search_blocks` 改 BM25-lite 排序（IDF、TF 飽和、body 長度正規化、TOC/頁眉降權）；`offset`/`total_matches` 分頁；有界 `documa_block_tree`（`max_depth`/`max_nodes`/`include_citations`）；搜尋回應內建確定性 `recommended_next`（可直接照發的 read 呼叫）與 `hints`（零結果補救、分頁、拆題）。
- **Token 計數政策**：全面禁止 chars/4 類啟發式。可插拔 counter（`documa.interfaces.token_counting`）：tiktoken 自動偵測（OpenAI 系）、`DOCUMA_TOKEN_COUNTER=anthropic:<model>` 走 Anthropic count-tokens API（content-hash 快取、二分截斷）。`max_tokens`／`max_response_tokens` 預算參數；無 counter 時 token 欄位為 null、預算參數回 `TOKEN_COUNTER_UNAVAILABLE`。新 extras：`documa[tokens]`、`documa[anthropic-tokens]`。
- **Collection 檢索修正與擴充**：FTS 欄位改 CJK 逐字切分（**中文子詞查詢從無法命中變為可用**；`INDEX_VERSION` 升 3，舊索引由 health 標 stale 引導重建）；查詢詞 AND 優先、引號片語、零命中才 OR 降級並以 `match_mode` 標示；`bm25()` 欄位權重；SQL 窗函數精確 `per_document_limit`（廢除過抓啟發式）；`group_by_document` 文件層 rollup（精確命中數、查詢置中 snippet、≤3 個 read-ready top_blocks）；`document_ids` 子集過濾；snippet 一律以命中詞置中（共用 `documa.core.snippet_windows`）；`recommended_next`/`hints`/`max_response_tokens` 與單文件同構。
- **增量索引維護**：`documa ingest`/`delete-document` 預設自動 upsert/remove 集合索引（content-hash 短路、單交易、`--no-update-index` 可關），ingest 完立即可搜；全量 `index-collection` 降級為修復手段。registry map 以 mtime 快取免除每查重 parse。
- **MCP 面補全**：`documa_ingest`、`documa_list_documents`、citation 家族（`cite_block`/`render_citation`/`source_window`/`verify_citations`）上 MCP，agent 可走完「ingest → 搜尋 → 讀取 → 引用 → 驗證」閉環。
- **Plugins 與 skills**：三個 host wrapper 都補齊多文件迴圈（openclaw 新增 4 個 collection 工具）；documa-evidence skill 深化為查詢策略指南（問題形狀路由表、廣度→收斂流程、token 旋鈕、反模式）；`scripts/package_plugins.py` 確定性打包 + CI freshness gate。
- **documa-mcp 孤兒防護**：stdio host 消失時自動退場（Windows PeekNamedPipe／POSIX POLLHUP，非消耗性偵測，互動執行不受影響）。

### 新增

- **ReadingOrderStage v2（zone/column，含 trace）**：XY-Cut++ 系確定性演算法——spanner 偵測（含「遮罩最寬塊以顯露被蓋住的 gutter」）、深度限制的垂直分帶、y 共存驗證的欄切割、網格偵測（列對齊的儲存格改列優先閱讀）。每個 block 記錄 `metadata.reading_order`（zone_id / column_index / rule / 套用的 Gestalt 原則），每頁記錄 zone+gutter trace——排序錯誤可直接定位，此能力在現有開源系統中未見。雙欄、三欄、sidebar gold case 全數 1.0。
- **Benchmark 擴充至 18 案例 / 13 gold**：8 份新生成 fixture（三欄、跨頁表、合併儲存格、財報表格、頁首頁尾、中英混排、sidebar、長 TOC）；新指標——span-aware TEDS（gold HTML colspan/rowspan 展開，與 PyMuPDF 網格慣例實測對齊）、關係連結錨定 P/R/F1、頁首頁尾角色分類、OCR 文字召回；逐 case 門檻覆寫（含驗證）；OCR gold 在無 extra 環境為 skipped。
- **Registry 併發防護**：filelock（core 第一個 runtime 依賴，理由註記於 pyproject）包住索引 read-modify-write，昂貴的 parse 在鎖外以 reload-recheck-commit 收尾；鎖逾時回明確 `LOCK_TIMEOUT`。新增 `documa inspect-store` 與 `documa doctor --store-dir`（索引完整性、缺檔、孤兒目錄、stale 鎖偵測——只回報不自動刪）。雙程序 100 次競爭 ingest 實測索引無損。
- **驗證分層定案**：L1 `documa_verify_citations`（id 存在性，既有）→ L2 `build_evidence_bundle()`（確定性證據組裝，新增於 core）→ L3 `AnswerSupportChecker` protocol（claim 級驗證契約；Anthropic API 串流參考實作在 examples，ML/LLM 不進 core 與 CI）。
- **CI 可見性**：non-blocking quality job，逐 case 分數表寫入 run summary，bench.json 存 30 天 artifact。

### 蓄意保留的 failed cases（下一輪標的）

- footnote-linking-001：FootnoteLinkingStage 未連結上標註腳標記與註腳本文。
- image-chart-asset-extraction-001：CaptionLinkingStage 未連結 "Image 1:" caption 與內嵌圖片。

### quality 門檻轉硬閘的條件

連續 10 次 CI run 中，各 gold case 分數波動 < 0.02 且無新增 error，即可把 quality job 的 continue-on-error 移除。

### 已知限制

- 巢狀欄不遞迴切割（trace 標 `fallback_row_major`）。
- RapidOCR 在 render zoom 2 下會漏掃部分行（ocr gold 門檻暫定 0.6，附日期註記）。
- filelock 於網路磁碟語意較弱；store 目錄應在本機磁碟。

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
