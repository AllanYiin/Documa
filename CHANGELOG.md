# Changelog

## v0.6.4 — Internal Rust parser distribution（2026-08-04）

- **Rust LingXi 抽取式摘要成為一級能力**：新增公開 `summarize_text`／`summarize_document`、`documa summarize` CLI、`documa_summarize` MCP/function tool 與 agent profile schema。結果只選取原文子句，保留 offset、block/source/page refs、TextRank／可解釋性／新穎性／事實訊號，固定回報零 LLM 呼叫；長文採來源可逆的階層視窗。LingXi 0.2.1 關鍵詞相容保留，0.3.0 同時提供關鍵詞與摘要。
- **Repository Intelligence Graph v1**：新增 Python-first、SQLite 持久化的 Code／Dependency／Call Graph，支援 generation 原子切換、file-hash 增量同步、typed/resolution-aware edges、Tarjan cycles、coupling metrics、proof-carrying query、hash-bound evidence read、generation diff、impact/test recommendation 與 uncertainty receipt。Python、CLI、MCP 與 Codex／Claude plugin skills 已接入；既有 ContextIR 1.0 與 `context_from_code()` wire contract 保持不變。
- **Dynamic Skill Loader v1**：新增獨立 `documa.skills` Skill IR、trusted-root incremental sync、兩層 lexical/HNSW routing、權威 dependency graph closure 與真實 tokenizer budget materialization；所有 instruction blocks 保留來源原文與 provenance，scripts/assets 不執行或注入。Codex plugin 新增精簡 loader bootstrap，agent MCP 僅增加 load/resource-read，管理工具限 admin。
- **Native library 顯式接管**：Skill root 新增逐 root `allow_native_scan_overlap` opt-in；預設仍拒絕 Codex/shared native scan paths，但可由使用者明確授權既有 `.agents/skills` 進行全量預編譯。Compiler version 納入 generation，避免 parser policy 更新後錯誤沿用舊 IR。
- **可選離線 routing enrichment**：sync 可接入具 provider/version 的 bounded enrichment，僅快取 derived synonyms、positive/negative triggers 與 topic tags；runtime 維持零 LLM，metadata 不成為 instruction 或 dependency truth。
- **內建 PDF／Office Rust parsers**：rust-pdf-parser 0.2.0 與 rust-office-parser 0.1.0 已成為 Documa 的 `native/` 內部 source trees；單一 platform wheel 同時提供 `rust_pdf._native` 與 `rust_office._core`，不再要求使用者另裝兩個 parser wheel。
- **共用 native binding 契約**：PDF／Office adapters 統一使用 identity、required calls、capabilities 與 JSON error envelope 驗證；core 仍只依賴 parser-neutral IR。
- **來源與發布封裝**：sdist 保留兩個可建置 Cargo workspaces、授權與 deterministic fixtures，排除 Cargo target、WASM 預編譯產物及快取；Windows CPython 3.10 platform wheel 已完成五格式 smoke。

## v0.6.3 — Rust-first native providers（2026-08-02）

- **LingXi 0.2.1 中文關鍵詞 provider**：文字葉節點的 `keyword_terms` 預設改由 LingXi TextRank 選取；既有 n-gram 僅保留祖先 support/new-word 補償與明確回滾，避免同一 evidence 在階層中重複命中。binding 嚴格驗證 0.2.1，缺失或版本不符時可觀測回退；provider 版本納入 IR metadata、source digest 與 tokenizer signature，升級後會重建舊 sidecar。
- **內建 Rust PDF／Office parsers**：rust-pdf-parser 0.2.0 與 rust-office-parser 0.1.0 已 vendored 至 `native/`，由同一個 Documa platform wheel 編譯 `rust_pdf._native` 與 `rust_office._core`。兩者共用 binding identity、capability 與 native error envelope 契約；PDF 保留 PyMuPDF recoverable fallback／renderer，Office 則依格式與錯誤 allowlist 決定是否回退。
- **MCP 安裝生命週期**：新增 `python -m documa.install` 受控升級入口；安裝鎖會阻止 MCP 重啟，已登錄 server 先收到退出通知、逾時則依 PID 強制終止，Windows 舊版 `documa-mcp.exe` 亦會被偵測並關閉。Codex／Claude plugin 改以 Python module 啟動，避免長駐行程鎖住 pip 管理的 console launcher。
- **檢索精度閘門**：單文件廣查詢若首筆只命中 1 個詞，或低覆蓋首筆落在 footnote／references／TOC／頁首頁尾，不再自動建議讀取，而是提示改用 2–4 個高鑑別 lexical literals；`needs_next` 改為先讀核心 block 後才條件式補鄰接內容。三份 agent skill 同步採 `limit=6`、每 block 1 段 snippet、query/any_of 去重與多主題分流。

## v0.6.1 — batteries-included agent runtime（2026-07-24）

- **大型文件 sidecar 效能修正**：`documa_process(out=...)` 建索引時只建立一次 source-text map，並快取所有 document-block 文字；423 頁／7,583 blocks 真實 IR 的 sidecar 重建由約 168 秒降至 3.687 秒，資料表逐列等價。

- **零模型呼叫的 HNSW section routing**：search sidecar schema 升至 v2，使用 section title/path/sketch/high-IDF terms 建立 deterministic local feature-hash vectors 與 multi-layer HNSW graph；lexical coverage 不足時才啟動 ANN，與 exact seed 融合後仍交由既有 BM25/intent/MMR/token budget 排名。查詢不呼叫 embedding API、LLM decomposition 或 token counter；IR/page/bbox citation truth 不變。

- **安裝預設改為完整非 OCR agent runtime**：`pip install documa` 現在包含 PDF、DOCX、PPTX、HTML、EML/MSG、IPYNB adapters、MCP server 與 tiktoken；`pip install "documa[all]"` 額外加入 RapidOCR。既有細粒度 extras 保留向前相容，MCP 1.x 設上限避免自動跨入尚未穩定的 v2。
- **Plugin 版本同步**：Claude Code、Codex 與 OpenClaw plugin metadata 全部對齊 Documa 0.6.1；plugin 安裝文件鎖定 `documa==0.6.1`，避免 plugin 與 runtime 漂移。

## v0.5.0 — token economy overhaul（回應層瘦身與雙堆疊排序統一）（2026-07-23）

- **MCP 單份傳輸**：FastMCP wrapper 改為單一 compact JSON text（`structured_output=False`），不再同時送 `structuredContent` 與 pretty-printed text——每個回應的 wire 成本約砍半。`call_documa_tool` 的 direct 路徑仍同時保留兩者供程式端取用。
- **短 block id**：單文件回應在 envelope 宣告一次 `block_id_prefix`，各項目改發 prefix-stripped 短 id（`p12_para3`）；所有工具輸入端同時接受長短兩型。合成 id（無 prefix）的 IR 不宣告 prefix、不受影響。
- **Omit-empty 與常數上提**：null/空字串/空集合欄位不再序列化；`page_ref_kind` 常數上提到 envelope；逐項 citation 四欄位（`citation_label`/`page_ref_kind`/`printed_page_labels`/`pdf_page_labels`）收斂為單一 `page` label。basel3 實測：`block_tree max_depth=2` 9,756→2,813 tokens（-71%）、`list_blocks depth=1` 3,476→1,092（-69%）。
- **Heading path 去根**：`_block_path`／sidecar／collection index 的 heading path 不再重複文件根節點 title（常為完整檔案路徑）；`INDEX_VERSION` 升 4、sidecar `FEATURE_VERSION` 加 `route-path-v2`，舊索引由 health 標 stale 引導重建。
- **`documa_block_tree` 精簡預設 + sketches**：`include_citations` 預設改 False；新增 `include_sketches`——直接掛上 ingest 時算好的 section sketch 與 `read_cost_chars`，一次呼叫取得「章節＋梗概」全貌，常可零 read 回答 overview 類問題。
- **`documa_inspect_block` 有界輸出**：不再全量傾倒 block（metadata 僅保留 role/source_range/source_block_type 與截斷後的 keyword/new-word terms），citation 資訊不再重複兩份。
- **搜尋回應自動預算**：有 token counter 時，search 回應預設套用 2,000 token 上限（未觸發時不輸出 budget 塊）；`max_response_tokens=0` 可關閉。nav 列補上 `needs_next`（僅 True 時輸出）。
- **診斷欄位分級**：`retrieval`/`snippet_policy`/`query`/`terms` 等診斷 baggage 移到 `debug` profile（evidence 僅保留 `effective_granularity` 與 `selected_evidence_tokens`）；citation 家族移除 `timing_ms`；`read_blocks` 不再回聲輸入參數，巢狀項目剝除 envelope 重複欄位。
- **Collection 搜尋瘦身與排序統一**：`documa_search_collection` 預設改 `nav`（flat 列真正瘦身為 `document_id/source/block_id/path/page_refs/score/snippet`）；移除 `ir_document_id`、`bbox_refs`、`dedupe_key` 與 `read_ref` 重複（讀取對 = `(document_id, block_id)`）；grouped rollup 的巢狀 top_blocks 剝除文件層重複欄位。flat 路徑補上與單文件一致的 content-hash 去重與 doc-region 降權（共用新模組 `documa.core.doc_regions`）。
- **Tool 面瘦身**：`agent` profile 補入 `block_tree`/`list_blocks`/`source_window`/`block_xref`（evidence 工作流完整可用），三個 plugin 的 MCP server 預設 `DOCUMA_MCP_PROFILE=agent`（schema 3,227 tokens，較 admin 省約 1,500/session）。
- **Skills 對齊預設值**：documa-evidence 三份同步改寫——凡工具已是預設的參數建議一律移除，補上短 id、sketch overview、`scope_block_id`/`granularity` 收斂與 collection 讀取對的說明；documa-maintenance 註明 admin profile 切換方式。

## v0.4.0 — adaptive retrieval, batch evidence, retrieval sidecar（2026-07-22）

- 新增 `documa_read_blocks`、boundary-aware continuation cursor 與共享 evidence token budget。
- `documa_search_blocks` 新增 scope/granularity、coverage/proximity/intent-fit、stable SimHash、branch-aware suppression、MMR 與 adaptive evidence selection。
- 新增可重建 `documa.search.idx` SQLite sidecar，包含 version/source generation、document DF、block features、hierarchical routes 與 deterministic section sketches。
- collection 新增 compact `nav` rollup；MCP/tool schema 新增 `agent`/`advanced`/`admin` profiles。
- plugin workflow 拆為日常 `documa-evidence` 與維護/發布 `documa-maintenance`。
- 新增 `benchmarks/token_economy` 真實 tokenizer benchmark 與 CI hard gate。

- **Agent response profiles**：`documa_search_blocks` 預設改為 navigation-only `response_profile=nav`；`evidence` 延後到分頁後才展開 citation/selection metadata 與精確 token count，`debug` 才回診斷欄位。明確傳入舊 `verbosity` 仍保留相容輸出。
- **Executable next action**：`recommended_next.actions[]` 改為實際 schema 可接受的 `{tool, arguments}` calls，補齊 `ir_path`、移除非法 `block_ids`/`include_children`，並依 section/continuation/leaf 分流到 browse、source window 或 read。
- **Budget correctness**：`max_response_tokens` 現在計入完整 compact-serialized structured payload（results、hints、next action、budget metadata），按順序縮減 optional metadata、低排名結果與 top ref；`spent_tokens` 為最終實測值。
- **Shared phrase parser**：單文件與 collection search 共用 quote-aware query AST；引號片語不再把 quote 字元當成查詢詞，且保留 `first-version` 類單一 lexical unit。
- **MCP transport**：相容模式仍依 MCP 建議在 `content` 保留完整 serialized JSON，但移除 pretty-print whitespace；確認只消費 `structuredContent` 的 direct host 可用 `text_mode="summary"` opt-in 縮小文字副本。


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
