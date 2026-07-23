# DEVNOTE — Documa

> 累加式開發筆記，取代 `/compact`。
> **檔頂 SNAPSHOT**：當前最新狀態（覆寫式，想知道「現在」就看這裡）。
> **檔尾 HISTORY**：時間順序的歷史區塊（累加式，想知道「為什麼」就往下讀）。

---

## 📌 SNAPSHOT — 當前狀態
<!-- 這一整段每次 /devnote 會被覆寫，只反映「到目前為止的最新狀態」 -->

**最後更新**：2026-07-23 23:23

### 需求狀態
- [x] v0.5.0 token economy 改造已 release 並推上 origin/main（`ac7c2e3`→`a479a89`）
- [x] MCP 回應單份傳輸、短 block id、omit-empty、nav/auto-budget 預設、collection 排序訊號統一
- [x] 三份 plugin skill 與 zip 已同步重建（package_plugins --check 通過）
- [ ] token_economy benchmark 的 gold 仍是合成小文件，量不出真實文件的短 id/瘦身紅利（見未解問題）
- [ ] 單文件搜尋仍在記憶體重算 SimHash/IDF，未消費 sidecar `blocks`/`term_stats`/`block_terms` 預計算（純效能，不影響 token）

### 未解問題
- **benchmark 指標與真實收益脫節**：`tokens_to_supported_answer` 6,427→6,688（+4%），漲幅來自 agent profile 多 4 個工具的 schema 固定成本（+572t）；但真實文件（basel3）回應層省 69-71%。benchmark gold 需換成帶 GUID id、多層結構的真實文件才有鑑別力。
- **grep 對照組缺席**：對外主張「比 grep 省 token」尚無同 query 集的 grep+read 模擬路徑對照數據。

### 關鍵技術決策（當前有效）
> 歷史上做過的、目前仍然成立的決策摘要。被推翻的決策不列。
- **回應層 token 慣例**：短 id + `block_id_prefix` 宣告、omit-empty、`page_ref_kind` 上提、citation 四欄收斂為單一 `page` label（詳見 HISTORY `[2026-07-23]`）
- **MCP wire 單份傳輸**：FastMCP wrapper 回傳 compact JSON 字串 + `structured_output=False`；`call_documa_tool` direct 路徑仍雙保留（詳見 HISTORY `[2026-07-23]`）
- **預設值就是產品**：skill 裡教 LLM 調的參數一律轉成工具預設值（nav、auto budget 2000、include_citations=False）（詳見 HISTORY `[2026-07-23]`）
- **collection 讀取對 = `(document_id, block_id)`**：`read_ref`/`ir_document_id`/`bbox_refs` 已從搜尋列移除（詳見 HISTORY `[2026-07-23]`）
- **doc-region 規則共用**：`documa.core.doc_regions` 供單文件與 collection 兩堆疊共用，避免 interfaces↔collections 循環匯入（詳見 HISTORY `[2026-07-23]`）
- **agent profile 涵蓋完整 evidence 工作流**：補入 block_tree/list_blocks/source_window/block_xref；plugin 預設 `DOCUMA_MCP_PROFILE=agent`（詳見 HISTORY `[2026-07-23]`）

### 已知地雷（仍需注意）
> 踩過且未來仍可能重踩的坑的一句話提醒。已徹底不可能重現的不列。
- **FastMCP `structured_output=False` 的 dict 回傳會被 `pydantic_core.to_json(indent=2)` pretty-print**——必須自己序列化成 str 回傳才是 compact（詳見 HISTORY `[2026-07-23]`）
- **`DocumentBlockType.TOC.value == "table_of_content"` 不是 `"toc"`**——用字串比對 block type 時務必查 `ir.py` enum 值（詳見 HISTORY `[2026-07-23]`）
- **測試用 `_CharCounter`（一字一 token）會誤觸 search 的 auto response budget**——斷言完整回應形狀的測試要傳 `max_response_tokens=0` 關閉（詳見 HISTORY `[2026-07-23]`）
- **INDEX_VERSION=4 / sidecar route-path-v2**：v0.5.0 前建的 collection index 與 sidecar 會被 doctor 標 stale，首搜前需重建
- **`test_registry_locking` 在 Windows 全套跑偶發 `PermissionError` flake**，單獨重跑即過

---

# 📜 HISTORY

---

## [2026-07-23] Token economy 研究 + 全面改造 + v0.5.0 release

### 本次做了什麼（增量）
從「documa 還有哪些省 token 空間」的研究出發（兩個 Explore agent 審計 MCP 回應面與搜尋/索引層 + basel3 實測），把結論全部實作並發佈 v0.5.0：

- 實測基準揭露：`block_tree max_depth=2` 9,756t，其中 id 欄位 2,427t（每個 id 帶 32 字元 doc GUID）、citation 四胞胎欄位約 5,100t，合佔 77%；`_tool_result` 把整份 payload 以 text+structuredContent 送兩次；plugin 預設 admin profile（26 工具 4,743t）。
- 改造後 basel3：tree 2,813t（-71%）、list_blocks 1,092t（-69%）、search 5 hits 1,181t（-12%），wire 再砍半。
- collection 搜尋補上與單文件一致的 content-hash 去重與 doc-region 降權；heading path 三處 builder（tools/sidecar/sqlite）統一去除文件根節點 title（原本每條 path 開頭都是完整檔案路徑）。
- sidecar 的 section sketch（ingest 時已算好但從未給 LLM 看過）首次接上：`documa_block_tree include_sketches=true`。
- 三份 plugin skill 改寫對齊新預設；`pytest` 342 全過；三個 commit 推上 main；版本推進 0.5.0、zip 重建。

### 本次重大技術決策
- **短 id 用「envelope 宣告前綴 + 條件式發布」而非改 IR 的 id 格式**
  - 內容：回應層 strip `db_{document.id}_` 前綴，`_canonical_block_id` 讓輸入端接受長短兩型；IR 不動。
  - 理由：IR id 是 semver 契約與 citation 穩定 key，動不得；回應層轉換零遷移成本。合成 id（無前綴）的 IR 連 `block_id_prefix` 都不宣告，避免對小文件反而變胖（benchmark 抓到過 +8t/回應的淨損）。
  - 影響：`recommended_next` actions、neighbors、xref、citation 家族全部同步短 id；`_prune_next_actions` 的比對集合需同時收長短型。
- **診斷欄位三級制**：nav＝路由必需欄位；evidence＝+citation/selection；debug＝`retrieval`/`snippet_policy`/`query`/`terms`/`timing` 類 baggage。原本 evidence 帶著整包診斷（含 `route_index_path` 檔案系統路徑）。
- **auto response budget 的「未觸發即隱形」**：預設 2,000t 上限只在真的裁掉東西時才輸出 `budget` 塊，否則 pop 掉——保護傘常開但不收保護費。
- **MCP 單份傳輸用自訂裝飾器**（`_documa_tool`：`functools.wraps` + 覆寫 `__signature__`/`__annotations__` 為 `-> str`），23 個 wrapper 一處收斂，不逐一改 return。
- **`doc_regions` 放 `documa.core` 而非 interfaces**：sqlite_index（collections）需要它，而 interfaces 已 import collections——放 interfaces 會循環匯入。`search_ranking` re-export 保持舊 import 路徑可用。

### 本次失敗經驗與填坑
- **FastMCP 非結構化輸出反而 pretty-print**
  - 現象：以為 `structured_output=False` 就省一半，實際 dict 回傳走 `_convert_to_content` → `pydantic_core.to_json(result, indent=2)`，比 compact 更肥。
  - 最終解法：wrapper 自己 `json.dumps(..., separators=(",",":"))` 回傳 str；str 會被原樣放進 TextContent。
  - 根因：mcp SDK 1.28.1 的 unstructured 相容路徑沿用舊版 FastMCP 行為，序列化格式不受呼叫端控制。
- **TOC 降權整組失效**
  - 現象：抽共用 `infer_doc_region` 後 `test_search_blocks_demotes_toc_hits_below_body_evidence` 紅掉，TOC 排回第一。
  - 根因：原碼比對 `block.type == DocumentBlockType.TOC`（enum），我改成字串比對時寫 `"toc"`，但 enum value 是 `"table_of_content"`。
  - 教訓：enum→字串重構時先查 value，別憑 key 名猜。
- **auto budget 被測試的 char counter 誤觸**
  - 現象：`test_single_document_quoted_phrase_search` 紅掉，snippets 消失。
  - 根因：測試類 `setUp` 掛 `_CharCounter`（1 char = 1 token），debug payload 「token 數」瞬間超過 2,000，auto budget 把結果列裁到剩 1 列（恰好是無 snippet 的 keywords-hit 列）。舊測試其實是對空 list 做 `all()` 的空洞斷言，一直假綠。
  - 最終解法：新增 `max_response_tokens=0` 作為明確關閉語意（順手成為公開 API），測試傳 0；同時把空洞斷言補上 `assertTrue(snippets)`。
- **benchmark 指標微升的解讀陷阱**
  - 現象：改造後 `tokens_to_supported_answer` 反升 4%。
  - 根因：benchmark 把 skill+schema 固定成本算進每 query，agent profile 補 4 個工具 +572t；而合成 gold 的 id 本來就短、文件只有 3 塊，吃不到任何瘦身紅利。指標對「回應層邊際成本」完全不敏感。
  - 教訓：改回應層之前先確認 benchmark 的 gold 能反映目標變因，否則會被固定成本噪音誤導。

### 備註
`token-economy.json`（benchmark 產物）留在 repo 根目錄未追蹤。本次未動 pipeline/IR，snapshot 測試無需 regen。
