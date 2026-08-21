# DEVNOTE — Documa

> 累加式開發筆記，取代 `/compact`。
> **檔頂 SNAPSHOT**：當前最新狀態（覆寫式，想知道「現在」就看這裡）。
> **檔尾 HISTORY**：時間順序的歷史區塊（累加式，想知道「為什麼」就往下讀）。

---

## 📌 SNAPSHOT — 當前狀態
<!-- 這一整段每次 /devnote 會被覆寫，只反映「到目前為止的最新狀態」 -->

**最後更新**：2026-08-22

### 需求狀態
- [x] 2026-08-22：Rust LingXi 0.3.0 抽取式摘要成為 Documa 一級能力；Python／CLI／MCP／function schema／agent profile 共用來源保留契約，逐句映射 block/source/page，長文採階層視窗，明載 `uses_llm=false`／`llm_tokens_used=0`；DocumentIR 0.2 不變
- [x] 2026-08-22：全域 `D:\Python310` 已由 LingXi 0.2.1 替換為目前 Rust source 重建的 0.3.0 ABI3 wheel；原生關鍵詞與摘要、Documa CLI 皆實測 PASS，doctor 9/9
- [x] 2026-08-21：Repository Intelligence Graph v1 完成。Python-first SQLite sidecar 提供 symbols/imports/calls/cycles/metrics/generations、hash 增量同步、proof-carrying query、stale-safe evidence read、impact/diff/test recommendation 與 uncertainty receipt；Python／CLI／MCP／Codex＋Claude plugins 已接入，ContextIR 1.0 行為不變
- [x] 2026-08-19：新增跨來源 ContextIR 1.0 與 document／code／skill adapters、typed graph navigation、hash-bound evidence read、CLI/MCP/function tools；HarnessFold 已以固定 ContextIR 路徑接入 Documa CLI backend。Graph 只導航、stale digest lexical-only、soft edges opt-in、token hard cap 無真 counter 時 fail closed；進階工具不加入 agent profile，避免固定 schema token 成本
- [x] `C:\Users\allan\.agents\skills` 已用逐 root `allow_native_scan_overlap=true` 顯式授權並全面預編譯：43 active、0 quarantined、25,332 blocks、50,858 edges、983 resources、6,816 terms；第二次 sync 為 43 unchanged／index no-op
- [x] Dynamic Skill Loader v1：明確 roots 全體預編譯為獨立 Skill IR/SQLite sidecar，runtime 兩層 lexical + feature-hash HNSW routing、graph dependency closure 與真實 tokenizer budget materialization；來源指令不摘要、不改寫
- [x] Dynamic Skill Loader supporting-resource 語義已釐清：compiler v1.2 只把真正套用於 resource 的「先讀／先以／先依／must read」標為 required；required supporting resources 留在主 bundle 預算外並回傳可執行 read action，另提供 available／partial／full／recommended 統計，已完整載入者不重複推薦
- [x] Dynamic Skill Loader 已移除固定 3,000-token 預設：省略 `max_tokens` 時採 automatic mode 並使用既有 8,000-token 安全上限，顯式預算維持相容；Python／CLI／MCP schema／bootstrap／eval 已同步
- [x] Codex plugin 新增唯一常駐 `documa-skill-loader` bootstrap；agent profile 只增加 load/read-resource，sync/status/graph 限 admin；deterministic Codex zip 已重建並通過 validator
- [x] Skill lifecycle/security：generation supersede、missing/quarantined、incremental no-op、lock、safe YAML、symlink/path escape、binary/script/asset 隔離與 resource hash drift 均有測試
- [x] Windows 安裝／升級加入 MCP lifecycle guard：先偵測與通知退出，逾時依登錄 PID 強制終止，並清理舊版 `documa-mcp.exe`；plugin 改用 `python -m documa.interfaces.mcp_server`
- [x] v0.6.4 runtime、三個 plugin metadata 與四份 install pin 已同步；Windows platform wheel、sdist、Claude/Codex deterministic zip 已重建並驗證
- [x] `pip install documa` 改為完整非 OCR agent runtime；`documa[all]` 額外加入 OCR；細粒度 extras 保留相容
- [x] 修正 v0.4 起 `documa_process(out=...)` 同步 sidecar 建置的 O(N²) 文字 map 回歸；真實 423 頁 IR 由約 168s 降至 3.687s
- [x] 單文件 sidecar v2 新增 local feature-hash HNSW section ANN；只在 lexical coverage 不足時啟動，不呼叫 embedding／LLM／token counter
- [x] 隔離解包的 0.6.4 wheel 回報 module/distribution 皆為 0.6.4；內建 rust_pdf 0.2.0、rust_office 0.1.0，未覆寫全域 Python 安裝
- [x] PDF 公開預設已改為 `auto` 的 Rust-first provider；`rust` 可嚴格指定、`pymupdf` 可回滾，Rust inferred order 在 pipeline 鎖定
- [x] Rust Stage 6C2-E 使用真正 lazy `native_events_v2`；頁面即時釋放、finalization 逐頁 drain，舊 wheel fallback 保留
- [x] Rust Stage 6D 預設 `compact_trace_v1`、verbose 可逆；三次 shadow RSS 1.056367x 通過 1.2x gate，focused 18/18、full 354/354、Ruff pass
- [x] Rust Stage 6C2-E exact wheel 保持不變：SHA-256 `5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61`
- [x] Rust PDF 0.2.0 與 Office 0.1.0 已 vendored 至 Documa `native/`；同一個 platform wheel 內建 `rust_pdf._native`／`rust_office._core`，並透過共用 binding identity/capability/error 契約對接
- [x] Rust Office parser 0.1.0 已在 `D:\PycharmProjects\rust_office_parser` 建立七 crate workspace 與 ABI3 wheel；DOCX、BIFF8 XLS、XLSX、PPTX real fixtures 可完成 process/search/read/cite
- [x] `office_provider=auto|rust|python` 已公開到 Python tools、CLI、MCP、schemas；auto 僅在 binding/contract/capability 缺失時對 DOCX/PPTX fallback，corrupt/encrypted/resource-limit 禁止 fallback
- [ ] Rust Office 本機 release gates 已完成：deterministic corpus 24/24、四 fuzz targets 實跑、DOCX/PPTX parity 與 latency/RSS report PASS；仍待 GitHub Windows/Linux/macOS CI 實際 run 後才能宣稱跨平台 release PASS

- [ ] Rust 尚未通過完整品質 provider gate：目前 quality gold 9/18 failed（reading order、table、footnote/image relation 等）；memory gate 已通過。預設雖為 Rust-first，品質敏感工作仍須 `pdf_provider="pymupdf"`。
- [x] 2026-08-02 LingXi 已升至 0.2.1 exact binding 契約；保留 provider 回滾、leaf-only LingXi，並將 provider 版本納入 sidecar digest/signature 以強制安全重建。
- [ ] v0.6.4 尚未 tag 或發布至 PyPI／plugin registry
- [x] 2026-08-04 Rust PDF／Office 內部子模組與共用 binding 已納入 0.6.4 wheel/sdist；Python 與 plugin artifacts SHA-256 已更新
- [x] v0.5.0 token economy 改造已 release 並推上 origin/main（`ac7c2e3`→`a479a89`）
- [x] MCP 回應單份傳輸、短 block id、omit-empty、nav/auto-budget 預設、collection 排序訊號統一
- [x] 三份 plugin skill 與 zip 已同步重建（package_plugins --check 通過）
- [x] 單文件搜尋新增 lexical precision gate：3+ 個 query literals 的首筆若僅命中 1 個，或 2+ literals 的低覆蓋首筆落在非正文區域，改提示收斂而不自動精讀；`any_of` 同義詞不列入 required literal 計數
- [x] `needs_next` 改為先讀核心 block、讀後仍截斷／語意未完才補鄰接；footnote 與中英文 references 區域可辨識並降權
- [ ] token_economy benchmark 的 gold 仍是合成小文件，量不出真實文件的短 id/瘦身紅利（見未解問題）
- [ ] 單文件搜尋仍在記憶體重算 SimHash/IDF，未消費 sidecar `blocks`/`term_stats`/`block_terms` 預計算（純效能，不影響 token）

### 未解問題
- **benchmark 指標與真實收益脫節**：`tokens_to_supported_answer` 6,427→6,688（+4%），漲幅來自 agent profile 多 4 個工具的 schema 固定成本（+572t）；但真實文件（basel3）回應層省 69-71%。benchmark gold 需換成帶 GUID id、多層結構的真實文件才有鑑別力。
- **grep 對照組缺席**：對外主張「比 grep 省 token」尚無同 query 集的 grep+read 模擬路徑對照數據。
- **替換後能力邊界**：Rust PDF 不提供 renderer/OCR，human-order 與 private table/image gold 尚未完成；LingXi 若直接發布到所有祖先會造成階層重複命中，因此僅替換文字葉節點的關鍵詞選取，並保留 n-gram 邊界熵新詞、`term_freq`/`child_support` bottom-up 統計。仍須保留 `pdf_provider="pymupdf"`、`keyword_provider="ngram"` 回滾，provider/tokenizer 變更時強制重建 sidecar。

### 關鍵技術決策（當前有效）
- **摘要是可丟棄 derived view，不是原文 mutation**：LingXi 只選摘要輸入投影的原句；Documa 明載 `text_form=raw|normalized` 與 Unicode code-point offset space、驗證 offset/text 一致並附 block/page refs。`top_k` 為 soft limit，保留數字／日期／條列事實可超出；遠端 token counter 不參與零 LLM 摘要路徑
- **Native skill root overlap 只允許逐 root 顯式 opt-in**：預設仍拒絕 `.codex/skills`／`.agents/skills`，目前工作區僅對 `agents-local`（`C:\Users\allan\.agents\skills`）設定 `allow_native_scan_overlap=true`
- **Skill 載入採全體 metadata 預編譯＋選中內容動態 materialize**：不把全部 SKILL.md 放進 context；可選離線 enrichment provider 只產生可快取 derived synonyms/positive/negative triggers/topic tags，runtime 仍為 0 LLM，graph truth 只來自來源結構與 lifecycle edges
- **Skill IR 不修改 DocumentIR 0.2**：原始 skill files 是事實來源、各 generation JSON 是可驗證編譯物、SQLite/HNSW 是可刪除重建 sidecar；identity/scope/guardrails 與 explicit dependency closure 不可為了 budget 被裁掉
- **升級入口必須先收斂 MCP lifecycle**：首次安裝可直接 pip；升級／重裝使用 `python -m documa.install`，持有 install lock、關閉／強制終止既有 server 後才呼叫 pip。plugin 不再以長駐 `documa-mcp.exe` 作為啟動 image
> 歷史上做過的、目前仍然成立的決策摘要。被推翻的決策不列。
- **回應層 token 慣例**：短 id + `block_id_prefix` 宣告、omit-empty、`page_ref_kind` 上提、citation 四欄收斂為單一 `page` label（詳見 HISTORY `[2026-07-23]`）
- **MCP wire 單份傳輸**：FastMCP wrapper 回傳 compact JSON 字串 + `structured_output=False`；`call_documa_tool` direct 路徑仍雙保留（詳見 HISTORY `[2026-07-23]`）
- **預設值就是產品**：skill 裡教 LLM 調的參數一律轉成工具預設值（nav、auto budget 2000、include_citations=False）（詳見 HISTORY `[2026-07-23]`）
- **安裝預設也是產品**：base install 包含文件 adapters + MCP + tiktoken，只有 RapidOCR 留在 ll extra；plugin manifest 與安裝 pin 必須跟 project version 同步
- **collection 讀取對 = `(document_id, block_id)`**：`read_ref`/`ir_document_id`/`bbox_refs` 已從搜尋列移除（詳見 HISTORY `[2026-07-23]`）
- **doc-region 規則共用**：`documa.core.doc_regions` 供單文件與 collection 兩堆疊共用，避免 interfaces↔collections 循環匯入（詳見 HISTORY `[2026-07-23]`）
- **HNSW 只作低信心 section router**：向量來自本機 lexical feature hash；exact lexical seed 優先，ANN 不直接成為 evidence、也不引入 embedding API／LLM decomposition／PPR
- **agent profile 涵蓋完整 evidence 工作流**：補入 block_tree/list_blocks/source_window/block_xref；plugin 預設 `DOCUMA_MCP_PROFILE=agent`（詳見 HISTORY `[2026-07-23]`）
- **decorative image 留在 Rust Layout IR、Documa 預設只記 aggregate**：內容／作者 Figure 才提升為 `ImageIR`；`rust_pdf_include_decorative_images=True` 可逆地保留全部 occurrence
- **Rust 只替換 parser adapter，不替換 renderer**：OCR/page preview 仍需 PyMuPDF；Rust 座標只接受 `layout_unrotated_top_left`，跨頁/domain/LLM 語意留在 Documa
- **Rust Documa metadata 預設 compact、verbose opt-in**：`compact_trace_v1` 以共享 schema 保留 ordinal/MCID/text-origin/rule，page object/coordinate space 向上繼承；`rust_pdf_include_verbose_metadata=True` 恢復舊形狀
- **Rust lazy finalization 是 terminal patch stream**：頁面先映射，metadata 在 exhaustion 原地更新，再以 `draining_stable_id_patches_v1` 逐頁套用 role/main-flow；不得在頁面迴圈前把 capabilities/warnings 當最終值
- **Office 引用依來源模型分流**：DOCX/XLS/XLSX 使用 `structural`，PPTX 使用 `slide_number_1_based` 且只有實際 shape bbox 才 visual；不得將 Office logical units 顯示成 PDF 頁碼
- **Office fallback 比 recoverable 更嚴格**：auto 只接受 binding/contract/capability allowlist；corrupt、encrypted、limit 即使底層標 recoverable 也不得切 Python

- **替換採可逆 provider，不刪舊能力**：Rust 負責 PDF extraction，PyMuPDF 保留 renderer/OCR 與明確回退；LingXi 負責預設 `keyword_terms`，n-gram 保留為缺模型回退與新詞能力補償，直到等價的新詞／gold gate 通過。

### 已知地雷（仍需注意）
- **Managed skill roots 不得位於 Codex 原生掃描路徑**：原生 loader 只應看見 plugin bootstrap，否則同一 skill 可能被原生與 Documa 重複注入；root 必須顯式設定 stable id/path/priority/enabled/trusted
- **一般 pip/wheel 沒有可靠的 pre-install hook**：無法由「尚未安裝的新版程式碼」在 pip 覆寫舊 launcher 前自行關閉 MCP；首次安裝直接 pip，後續升級必須走 `python -m documa.install`
- **`documa.install --force-reinstall` 會讓 pip 重新解析／重裝完整依賴樹**：共用全域 Python 可能因此升級其他專案的套件並產生 `pip check` 衝突；artifact smoke 優先使用隔離環境，或後續為 installer 補受控 `--no-deps` 能力
> 踩過且未來仍可能重踩的坑的一句話提醒。已徹底不可能重現的不列。
- **FastMCP `structured_output=False` 的 dict 回傳會被 `pydantic_core.to_json(indent=2)` pretty-print**——必須自己序列化成 str 回傳才是 compact（詳見 HISTORY `[2026-07-23]`）
- **`DocumentBlockType.TOC.value == "table_of_content"` 不是 `"toc"`**——用字串比對 block type 時務必查 `ir.py` enum 值（詳見 HISTORY `[2026-07-23]`）
- **測試用 `_CharCounter`（一字一 token）會誤觸 search 的 auto response budget**——斷言完整回應形狀的測試要傳 `max_response_tokens=0` 關閉（詳見 HISTORY `[2026-07-23]`）
- **INDEX_VERSION=4 / sidecar schema v2**：v0.5.0 前的 collection index，以及未含 `hnsw-route-v1` 的 search sidecar 都是 stale 衍生物；需重建後才使用 indexed routing
- **`document_block_text()` 每次都重建全文件 source-text map**——大量迴圈不可逐 block 呼叫；sidecar 必須一次建 map 並重用（詳見 HISTORY `[2026-07-24]`）
- **`test_registry_locking` 在 Windows 全套跑偶發 `PermissionError` flake**，單獨重跑即過
- **Source install 現在會編譯兩個 Rust extension**：需要 Rust 1.88+ 與平台 linker；預建 wheel 使用者不需要 toolchain。Windows CPython 3.10 wheel 已驗證，Linux/macOS wheel 仍需 CI 實跑
- **Release tests 不得直接信任既有 `build/lib*`**：版本推進後舊 build cache 可能仍載入前版 Python 檔；以新 wheel 解包／隔離安裝後跑全套才是 artifact gate

- **關閉 pytest plugin autoload 會拿掉 snapshot fixtures**：全套需顯式 `-p pytest_datadir.plugin -p pytest_regressions.plugin`
- **Rust memory gate 已過但不可直接切換**：Stage 6D 完整 Documa 峰值 646,643,712 bytes（1.056367x PyMuPDF）；字元／tagged-order／私有 table-image gold 仍是 NO-GO
- **readiness 不是 accuracy**：fixture readiness 18/18 只證明檔案與 capability contract 齊備；2026-07-30 Rust quality mode 實測為 9 passed / 9 failed，不得寫成品質全過。
- **LingXi 完整 stage 目前不是加速項**：保留 n-gram 新詞／support 後，12 份 fixture 暖機中位數 13.9652 ms，較 n-gram 11.0256 ms 慢 26.66%；優勢是 leaf keyword 語意排序，不是整段 pipeline latency。

---

# 📜 HISTORY

---

## [2026-08-22] Rust LingXi 零 LLM 抽取式摘要一級化

- 新增 `documa.summarization` 公開 API 與 LingXi 0.3.0+ capability contract；純文字與 DocumentIR subtree 都可摘要，輸出保留原文 offset、逐句排名診斷與證據 refs，無任何生成／改寫。
- `documa_summarize` 已接 Python tool registry、CLI、MCP、OpenAI schema 與 agent profile；回應固定明載 extractive／uses_llm／llm_tokens_used，只有本機 token counter 才附 context reduction 數據。
- 超過 `max_window_chars` 的文字先分窗抽取，再對原句候選做第二階段 TextRank；最終 offset 重新映回原始輸入。LingXi 0.2.1 關鍵詞仍可用，0.3.0 可同時提供關鍵詞與摘要。
- 真實 native smoke 以目前 Rust source、`--locked`、隔離 target 重建 0.3.0 wheel：Python API 與 CLI 均 PASS，逐句 source span 完全一致，fixture context 226→80 tokens；舊的同版 pre-summary wheel 因缺 method 被 capability gate 正確拒絕。驗證：`tests/` 422 passed／4 skipped、Ruff full PASS、doctor 8 passed／1 optional warning、fixture readiness 18/18、plugin validator／deterministic zip／`git diff --check` PASS。

## [2026-08-22] 全域 LingXi 0.3.0 啟用

- 以目前 `D:\PycharmProjects\rust_Lingxi` source 與 locked dependency graph 重建 `lingxi-0.3.0-cp39-abi3-win_amd64.whl`（SHA-256 `4b05aa81cb9cb2da4390f5332a3df1f4dc482b0e062efdff04fe1f5b4034065f`），先以 workspace target 隔離安裝與 smoke，再使用 `--force-reinstall --no-deps` 將全域 `D:\Python310` 由 0.2.1 替換為 0.3.0，未觸動其他 dependency。
- 真實 binding 驗證：distribution 0.3.0、`Segmenter.extract_summary` available、關鍵詞可用；Documa Python 與 CLI 皆回報 `lingxi/0.3.0`、`uses_llm=false`、`llm_tokens_used=0`，摘要句與 source offsets 完全一致。
- 驗證：`tests/test_summarization.py tests/test_keyword_provider.py` 12 passed；doctor 9 passed／0 warnings；fixture readiness 18/18。pytest 只有 workspace `.pytest_cache` ACL warning；pip 仍回報既存 `~ocuma` invalid-distribution warning，兩者都非 LingXi 安裝失敗。

---

## [2026-08-20] 移除固定 3,000-token skill bundle 預設

- `load_skill_bundle`／Python tool／MCP／CLI 的 `max_tokens` 預設改為 `None`；automatic mode 使用既有 8,000-token 安全上限，回應 budget 明載 `mode`、`requested_max_tokens` 與實際 `max_tokens`。呼叫者顯式傳入 256–8,000 的舊契約不變。
- Tool schema 改為 nullable integer、bootstrap skill 改為預設省略參數、README 與 skill-loader eval 同步；低信心 retry action 在 automatic mode 不再偷偷注入固定預算。
- 真實 `web-access-advanced`／`problem-decomposer`／`web-search-strategy` 省略預算後皆為 `status=ok`，實際 spent 3,115／4,089／4,424 tokens，且各回傳 3 個 supporting-resource reads。
- 驗證：automatic-budget focused regression、skill-loader focused、完整 pytest、Ruff、doctor、plugin deterministic package gate（最終數字見當次驗證結果）；Codex plugin zip 已同步重建。

---

## [2026-08-19] Supporting-resource action 與顯示語義修正

- `references_resource` edge 新增 `read_policy=required|on_demand`；required 判斷改為只看 resource 前方的局部 directive，避免「先讀使用者內容，再依 reference」把後者誤標為必讀，compiler 版本升至 `documa-skill-v1.2`。
- required supporting resources 不再直接吃掉主 bundle token budget，改由 `documa_read_skill_resource` action 表達；一般 resources 依 task block score／已選來源排序，每 skill 最多建議 3 個，explicit required 不受此上限裁掉。
- `SkillBundle.resource_summary` 與每個 `selected_skills[].resource_summary` 明確拆出 available、materialized、partial、full、recommended；完整 materialize 的資源不再重複建議，不可讀 script／asset 不會產生 read action。
- 驗證：skill-loader focused 13/13、完整 pytest 405 passed／4 skipped、Ruff full PASS、doctor 7/7、`git diff --check` PASS；真實三 skill 在 8,000-token 預算下由建議 0 改為各 3，並正確回報 available/materialized 狀態。

---

## [2026-08-19] Documa-owned shared context runtime

- 新增可丟棄 ContextIR 1.0 projection，DocumentIR、SkillIR 與 explicit code files 共用 block/hash/relation contract；Python 以 AST 產生 symbol／contains／calls，其他明確檔案保守退回 whole-file block。
- 新增 `context-build`／`context-search`／`context-read` 及對應 Python、MCP、function schemas。EXTRACTED graph 只作 bounded navigation；來源 digest stale 時 lexical-only，證據讀取重驗正文 hash，soft edges 與 token hard cap 都採 opt-in/fail-closed。
- HarnessFold 新增 Documa CLI backend 與 MCP 啟動設定，document／code／skill 閱讀權責回到 Documa；舊 HarnessFold SQLite backend 暫留 migration fallback。
- 驗證：ContextIR 11 tests、既有 interfaces/schema/watchdog 28 tests、Ruff PASS；HarnessFold 94/94 tests，真實跨 repo build→search→read smoke PASS。

---

## [2026-08-17] Dynamic Skill Loader v1

- 新增 `documa.skills`，涵蓋 safe parser、Skill IR、generation registry、全域 skill/block index、TF/DF/new-word/SimHash metadata、local feature-hash HNSW、兩層 ranking、dependency graph closure、token-bounded renderer 與 resource pagination。
- 採混合策略：所有 configured roots 在 sync 時預編譯 metadata；runtime 只 materialize 最多 3 個入選 skills 的必要原文 blocks。可選 enrichment provider 依 provider/version/source digest 快取，不能建立權威 instructions/edges。
- 新增 Python、CLI 與 MCP contracts；Codex plugin 加入 `documa-skill-loader` bootstrap，Claude Code/OpenClaw 行為不變。驗證：Ruff PASS、`tests/` 392 passed/4 skipped、1,000-skill warm-load p95 ≤250ms gate PASS、plugin/skill validator 與 deterministic zip check PASS。

---

## [2026-08-18] `.agents/skills` 全面預編譯

- 新增 `SkillRoot.allow_native_scan_overlap`、CLI `--allow-native-scan-overlap` 與 MCP schema 欄位；預設拒絕 native roots 的安全邊界不變，只有使用者明確授權的 root 可重疊。
- 設定 `agents-local` → `C:\Users\allan\.agents\skills`（priority 100、trusted/enabled），43/43 skills 編譯成功、0 quarantined；index v3 含 25,332 blocks、50,858 edges、983 resources、6,816 terms 與 43 個 HNSW skill nodes。
- Compiler generation 納入 compiler version；reference resources 的 scope/guardrail 不再自動成為全域 mandatory，只有 `SKILL.md` 本體與 explicit required-resource closure 強制保留。`spec-organizer` minimum bundle 由 9,622 降至 6,446 tokens，7,000-token materialization PASS。

---

## [2026-07-24] 大型文件 `documa_process` 300 秒逾時修正

- 同一份 423 頁、38.7 MB PDF 在 v0.4 前可完成，但 v0.4 將 `documa.search.idx` 納入 `documa_process(out=...)` 同步路徑後出現 MCP host 300 秒 deadline。
- 產物時間分界：PDF parse/previews 約 116s，IR + pipeline 約再 93s，sidecar 約再 168s；MCP timeout 是 client deadline，server 最終仍完成有效產物。
- 根因：sidecar 對每個 document block 呼叫 `document_block_text()`；該函式每次重建全文件 `source block id → text` map，section sketch/subtree cost 又重複讀同一批 block，形成大型文件 O(N²) 熱點。
- 修正：sidecar 一次建立 source-text map 與 document-block text cache；descendant queue 改 `deque.popleft()`。不改 IR、tool schema、sidecar schema 或輸出內容。
- 真實 IR 實測：sidecar 168s → 3.687s；舊新 SQLite 的 metadata/blocks/term_stats/block_terms/routes 雙向 EXCEPT 全為 0。針對性 28 passed；全套 `pytest -p no:cacheprovider` 348 passed。

---

## [2026-07-24] v0.6.1 batteries-included packaging（待發布）

- v0.6.0 未發布；納入大型文件 sidecar 效能修正後直接推進為 v0.6.1。
- runtime、三個 plugin metadata、四份 install pin 與兩個 tracked zip 已同步；本機 editable metadata 亦刷新為 0.6.1。
- 預設 dependencies 納入 PDF/DOCX/PPTX/HTML/MSG/IPYNB adapters、MCP 1.x 與 tiktoken；`all` extra 只新增 RapidOCR。
- 版本推進至 0.6.1；Claude Code、Codex、OpenClaw manifest/package 同步，plugin README pin `documa==0.6.1`。
- validator 與 packaging contract test 會阻擋 runtime/plugin version drift；plugin zip 已重建；全套 `pytest` 348 passed。
- 尚未 commit、tag、push 或發布。

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

---

## [2026-07-29] Rust PDF shadow adapter + draining transfer

- 新增 lazy optional `RustPdfAdapter`、registry explicit provider、穩定錯誤、Layout IR schema/座標驗證，以及 text/role/table/image/navigation/provenance mapping；預設 provider 不變。
- Rust `inferred_order` 成為 adapter block order，`reading_order_locked` 阻止下游再用幾何規則覆寫；四種 Rust order 仍保留 metadata。
- 正式 7 PDFs / 1,113 pages shadow：Rust complete adapter 20.095623 pages/s，PyMuPDF 6.608120 pages/s，Rust 3.041050x；character F1 0.960813，故 NO-GO。
- 新 wheel 提供 `extract_layout_stream()`；Documa 逐頁 consume。draining transfer 加 decorative aggregate policy 後，580/423 頁壓力檔 RSS 分別降 61.54%/18.48%，文字 SHA/block/span 不變；全域最大仍為 PyMuPDF 1.449143x，native page-production 尚待開發。
- Rust adapter/reading-order focused 17 passed；全套在顯式 snapshot plugins 下 353 passed；Ruff 通過。PyMuPDF OCR/page preview renderer 未移除。
---

## [2026-07-29] Rust Stage 6C2-C/D final integration gate

- Exact wheel SHA-256 `8bfde5151edae46e828aaa27d125073b1e4d94915241da5b7fc874586a6036e1`
  passed Rust adapter/reading-order focused 17/17 and full Documa 353/353.
- `documa doctor --project-root .` passed 8/8 with fixture benchmark readiness
  18/18. Full `ruff check --no-cache .` passed after ten behavior-neutral lint
  cleanups; no provider mapping or default selection changed.
- PyMuPDF remains default and remains the OCR/page-preview renderer. Rust native
  event delivery is not yet lazy, so default-provider cutover remains forbidden.

---

## [2026-07-29] Rust Stage 6C2-E native lazy producer

- `RustPdfAdapter` 已從 `draining_json_v1` complete-page queue 切至
  `native_events_v2`。Rust 逐頁解析並釋放 Layout page；Python metadata 在
  `DocumentFinalize` 後原地更新。
- repeated furniture 的 role/main-flow 不要求保留 raw pages；adapter 以
  `draining_stable_id_patches_v1` 逐頁套回既有 `BlockIR` / `PageIR`。
- lazy 期間才發生的 parser error 統一包裝為 `RUST_PDF_PARSE_FAILED`；舊 wheel
  無 stream/finalization API 時仍可 fallback。
- exact wheel focused 17/17、full 353/353、Ruff pass。輸出 text SHA、block/span
  counts 與 Stage 6C2-B 相同。
- 效能仍是 NO-GO：完整 shadow Rust 20.071995 pages/s、比 PyMuPDF Documa 快
  3.682255x，但 RSS 946,515,968 bytes（1.553468x）。逐頁 finalization drain 將
  AI Index probe 降至 900,263,936 bytes，仍未達 1.2x；PyMuPDF 保持 default 與 renderer。
---

## [2026-07-29] Rust Stage 6D compact metadata + memory gate

- 預設映射改為 `compact_trace_v1`：共享 schema 保留 source ordinal、MCID、text origin、rule ID，page object 與 coordinate space 改由 page/document 繼承；verbose 舊形狀可明確 opt-in。
- citation 仍使用真實 block id、page/BBox 與 stable source refs；7/7 文件 text SHA、block/span/semantic counts 與 Stage 6C2-E 相同。
- 正式 1 warm-up + 3 measured shadow：Rust 34.704637 pages/s、PyMuPDF 5.976338，快 5.807007x；Rust RSS 646,643,712 bytes、PyMuPDF 612,139,008，ratio 1.056367x，memory gate PASS。
- AI Index lifecycle profile：parse peak 356,667,392、canonical serialization peak 564,396,032；canonical IR -37.53%，encoded metadata -67.38%。
- focused 18/18、full 354/354、Ruff、doctor 8/8 全過。字元 F1 0.960813、tagged order 0.940546、private table/image gold 未過／缺席，PyMuPDF 繼續是 default 與 renderer。

---

## [2026-07-30] 零模型呼叫的 HNSW section routing

- 新增純標準庫 `documa.search.hnsw`：deterministic 192 維 local feature hash、cosine distance、stable geometric levels、bounded-degree multi-layer graph、`ef_construction=32`／`ef_search=32`。
- sidecar schema 升 v2，新增 `route_ann_nodes`／`route_ann_edges` 與 ANN metadata；atomic rebuild 同時涵蓋 update/delete/state，IR 仍是 citation truth。
- 查詢先跑原有 lexical route；coverage < 1 才走 HNSW，exact/ANN seeds 融合後只縮小 leaf scope，最終排名仍是 BM25-lite + coverage/proximity/intent + dedupe/MMR/token budget。
- 明確排除 NodeRAG 的 query embedding API、LLM entity decomposition、全圖 PPR；debug profile 新增 `route_sources`，一般 nav/evidence 回應不增肥。
- 驗證：focused HNSW/sidecar 9/9、完整 pytest 356/356、Ruff pass、doctor 8/8、fixture readiness 18/18；96-section 測試驗證 HNSW 有執行且未解包全部 vectors。

---

## [2026-07-30] Rust PDF／LingXi 預設替換前能力損失與回滾紀錄

### 已知能力損失

- Rust PDF 只替換 parser extraction；page preview、OCR、掃描件與需要 renderer 的路徑仍依賴 PyMuPDF。
- Rust parser 不支援 encryption、damaged-xref repair、完整 stream/image codec 與渲染；遇到 unsupported/recoverable error 必須能明確切回 PyMuPDF，不能靜默產生殘缺 IR。
- Rust raw text character/bigram F1 已達 0.998954/0.996075，但 human reading-order 雙人 gold、table TEDS-S 與 image gold 尚未完成；這些 gate 在完成前保持 BLOCKED。
- LingXi `extract_keywords()` 只提供 TextRank 詞與權重；直接替換會失去 n-gram 邊界熵新詞、完整 term frequency、child support 與既有 bottom-up metadata 契約。
- LingXi 冷載入實測約 42 ms、常駐 RSS 約增加 21 MB；目前模型 wheel/資產不可公開再散布，base install 不可硬依賴。
- 37-block probe 的 LingXi 與 n-gram mean Jaccard@12 僅 0.2164，代表排序語意會大幅改變；5-query synthetic retrieval 無回歸不足以證明真實正確率。
- 2026-07-30 Rust 預設啟用後，發現 page-block id 可能與 document-block 的短 alias 碰撞；`source_window()` 會錯把 page block 解成 document block，造成中心 id 契約回歸（focused integration 79 passed / 1 failed）。必須先讓 exact page id 優先於衍生 alias，再解除此項。
- 2026-07-30 驗證 sidecar rebuild 時發現：靜態 feature version 雖已升版，但同一 IR 在 `lingxi`／`ngram` 間切換時，舊 `source_digest()` 不含 provider 與 keyword metadata，可能誤用舊 sidecar。必須把實際 provider 與索引輸入納入 digest／tokenizer metadata。
- 2026-07-30 安裝 exact Rust wheel 並啟用新預設後，full suite 為 350 passed / 10 failed：3 個 legacy snapshot、OCR image metadata、三欄閱讀順序、table quality、merged-cell table，以及 3 個 collection/search 行為。這批是替換後的實際契約差異；legacy snapshot 應鎖回舊 provider，OCR 必須路由 renderer，Rust table/order 未過時須可觀測回退，其餘 search 差異須先定位後才能解除。

### 實作要求與回滾

- 公開工具、CLI、MCP 都要接受 `pdf_provider`，預設切至 Rust；`pymupdf` 必須保持可明確選擇。
- Rust 缺 binding 或遇到不支援能力時，回退必須被回應的 parser/warnings/provenance 看見；不得偽裝成 Rust 成功。
- `keyword_provider` 要有 `lingxi`／`ngram`，預設 LingXi；缺模型時允許可觀測回退，並保留 n-gram 新詞能力直到 LingXi 等價方案通過 gold。
- sidecar tokenizer/feature version 必須包含 provider；舊 sidecar 視為 disposable 並重建。
- 最終驗收至少包含 focused/full tests、doctor、fixture readiness、前後 keyword latency/RSS、retrieval Evidence Recall/Citation Precision，以及 Rust PDF 現有 quality/memory gate；任何 human/table/image BLOCKED 仍須明載，不得被局部測試覆蓋。

---

## [2026-07-30] Rust PDF／LingXi 預設替換完成與驗證

- `parse/process/view/ingest` 的 Python tool、CLI、MCP 與 schema 已公開 `pdf_provider`；PDF 預設 `auto` 先用 Rust，recoverable error 可觀測回退 PyMuPDF，嚴格 `rust`／回滾 `pymupdf` 保留。exact wheel `5ac374d0…d61` 已安裝至 Documa 現用 `D:\Python310`。
- `keyword_provider` 預設 `lingxi`；LingXi 只排序文字 leaf，祖先維持 n-gram child-support 聚合，邊界熵 `new_word_terms`／`term_freq` 契約不刪。缺模型時回退原因寫入 block/stage metadata。
- OCR `auto` 明確路由 PyMuPDF renderer，metadata 記錄 `OCR_REQUIRES_PYMUPDF_RENDERER`；Rust parser 不偽裝具備 renderer。
- 修正兩個替換後才暴露的契約問題：exact page-block id 現在優先於 document-block 短 alias；sidecar digest/tokenizer metadata 納入 requested/actual keyword provider 與實際索引詞，provider 切換會重建。
- 12 fixtures / 21 pages / 124 document blocks：n-gram stage 暖機 median 11.0256 ms；LingXi+補償 median 13.9652 ms（1.2666x），cold sample 55.2654 ms。keyword mean Jaccard@12 0.771917，38/124 blocks 改變。
- 5-query synthetic retrieval：兩者 Evidence Recall@300/600/1200 都是 1.0、Citation Precision 0.55、Citation Recall 1.0、paraphrase top-k Jaccard 0.8；只能證明此小集合無回歸，不能取代真人 gold。
- Rust formal shadow 沿用 exact wheel 結果：34.704637 pages/s vs PyMuPDF 5.976338（5.807007x），RSS ratio 1.056367x；raw character/bigram F1 0.998954/0.996075。此次 quality mode 則 Rust 9/18 failed；PyMuPDF legacy baseline 2/18 failed（footnote/image relation），所以 Rust reading-order/table/image gate 仍 BLOCKED。
- 驗證：full pytest 360 passed；`ruff check --no-cache .` passed；doctor 8/8；fixture readiness 18/18；`git diff --check` passed。default smoke 為 `rust_pdf`，OCR smoke 為 `pymupdf` fallback。
---

## [2026-08-01] 0.2.0 binding 契約與 LingXi 階層去重驗證

- 作用中 Python 的 `lingxi` distribution 由 0.1.0 換成 `D:\PycharmProjects\rust_Lingxi\target\wheels\lingxi-0.2.0-cp39-abi3-win_amd64.whl`；`rust_pdf.version_info()` 為 `('0.2.0', 'stage-11')`。
- `_load_lingxi_segmenter()` 與 `_load_rust_pdf()` 新增 exact 0.2.0 contract；版本缺失／不符會進入既有可觀測 fallback，不會把錯版 native binding 當成成功。
- 曾嘗試把 LingXi leaf scores 向所有祖先發布；full suite 出現 5 個階層重複命中回歸（collection incremental、document cache、budget、group hint），因此收斂為 leaf-only LingXi，祖先維持 n-gram/support 聚合。這是搜尋去重契約，不是效能取巧。
- 驗證：focused 12/12、full pytest 362/362、Ruff pass、`git diff --check` pass、doctor 8/8、fixture readiness 18/18；真實 smoke 可抽出中文 TextRank keywords。
---

## [2026-08-01] v0.6.2 Python／plugins packaging

- runtime `pyproject.toml`／`documa.__version__`、Claude Code／Codex／OpenClaw manifests、OpenClaw package 與四份 plugin install pin 全部同步至 `0.6.2`。
- Python artifact：`dist/documa-0.6.2-py3-none-any.whl`（SHA-256 `9dc7981eeeaf00e06b9af7ad2c3b3902cf28d7a1edff270aa4ad72a8f3186324`）與 `dist/documa-0.6.2.tar.gz`（`3699e4d7a9e74837d99d7149821c4bc5e453e0330fb56663425378ab1be24e1a`）。
- Plugin artifact：`plugins/claude-code-documa.zip`（SHA-256 `8f3f14fac3046d704fcdfb2e2ec75c74f35e76c468b6bf6680cbaec07e8b4415`）與 `plugins/codex-documa.zip`（`b910585ce6292c750ba9008dd2361f5c9a76997eaed455a9ae1a52a8e1860eed`）；OpenClaw 依現有慣例同步 package/plugin directory，不另造 zip。
- 驗證：Twine 2/2 passed、plugin deterministic check passed、agent plugin validator passed、wheel/sdist native modules 與 zip manifest content check passed、full pytest 368/368、Ruff pass、doctor 8/8、fixture readiness 18/18、`git diff --check` pass。
- 尚未 commit、tag、push、發布 PyPI 或發布 plugin registry。

---

## [2026-08-01] Windows MCP guarded install

- 新增 `documa.interfaces.mcp_lifecycle`：每個 MCP server 以 file lock 登錄 PID，並監看安裝 shutdown token；登錄與 watcher 啟動受同一 install lock 保護，避免安裝／server 重啟 race。
- 新增 `python -m documa.install`：持有 install lock，先通知登錄中的 server 退出，2 秒未退出則依仍被鎖定的 registration PID 強制終止；Windows 額外用 exact image name 偵測／終止尚未支援登錄的 `documa-mcp.exe`。仍有行程時不啟動 pip。
- Codex／Claude plugin MCP command 改為 `python -m documa.interfaces.mcp_server`；相容 console script 保留，但 plugin 長駐行程不再鎖住 pip 管理的 exe。
- 驗證：guarded install／stdio／packaging focused 12/12，full pytest 368/368，Ruff pass，plugin validator pass，plugin deterministic package check pass，Twine wheel/sdist 2/2 pass；wheel 內含 `documa/install.py` 與 `mcp_lifecycle.py`。
- 最終 artifacts：wheel SHA-256 `9dc7981eeeaf00e06b9af7ad2c3b3902cf28d7a1edff270aa4ad72a8f3186324`；sdist `3699e4d7a9e74837d99d7149821c4bc5e453e0330fb56663425378ab1be24e1a`；Claude plugin `8f3f14fac3046d704fcdfb2e2ec75c74f35e76c468b6bf6680cbaec07e8b4415`；Codex plugin `b910585ce6292c750ba9008dd2361f5c9a76997eaed455a9ae1a52a8e1860eed`。

---

## [2026-08-02] 搜尋 precision gate 與 core-first evidence read

- Harness／grep 對照顯示區塊檢索約省 77% 文件工具 token，但首輪廣查詢會被通用詞、footnote／reference 雜訊拉偏，且 `needs_next` 造成鄰接內容過早讀取。
- 單文件搜尋現在分開計算 `query` literals 與 OR 型 `any_of` 同義詞：3+ query literals 的首筆只命中 1 個時不發 `recommended_next`，改回低精度提示；2+ literals 的低覆蓋非正文首筆同樣先收斂。既有 query/any_of schema 與 ranking 介面不變。
- leaf 的 recommended action 一律先 `documa_read_block`；`needs_next` 只保留為讀後補鄰接信號。新增 footnote region 降權與中／英／簡體 references heading 辨識，footnote 在 evidence profile 標 `is_reference=true`。
- Codex／Claude Code／OpenClaw skills 同步為：直接呼叫已註冊工具、query 使用 2–4 個高鑑別 lexical literals、any_of 不重複、起始 `limit=6`／每 block 1 snippet、多主題分流、正文與多詞命中 precision gate、通常只讀 1–3 個候選。Codex eval 新增 multi-theme precision case。
- 驗證：focused 49/49；full pytest 371/371；Ruff full pass；doctor 8/8、fixture readiness 18/18；agent plugin validator pass；deterministic plugin zip check pass；OpenClaw `node --check` pass；`git diff --check` pass。
- 重建後 plugin zip：Claude `86f50dd1a4da28f9bda7f5db34ba5dea30791953de518318a14d537ce4e00425`；Codex `cbbc5408ac1bf104c11fcc9739a8afb0a31434446e9013f0fe2b26280b8aaeac`。2026-08-01 的 wheel/sdist hashes 對應本次 runtime 變更前內容；正式發布 v0.6.2 前必須重建 Python artifacts 並更新 hashes。

---

## [2026-08-02] LingXi 0.2.1 exact binding 升級

- Documa 的 `REQUIRED_LINGXI_VERSION`、README 安裝路徑與 v0.6.2 changelog 由 0.2.0 更新為 0.2.1；作用中 `D:\Python310` 已透過 guarded installer 由 0.2.0 換成 `lingxi-0.2.1-cp39-abi3-win_amd64.whl`，SHA-256 `3ee066708c826861553869adae0b0504edf92506c89ad4d3af9f3d65f6e41fa0`。
- LingXi 版本寫入實際／requested provider metadata，並納入 `source_digest()` 與 tokenizer signature；sidecar `FEATURE_VERSION` 升為 provider-version-aware v4。只有 LingXi 路徑新增版本欄位，明確 n-gram 路徑維持既有 IR 形狀，避免向前相容 snapshot 回歸。
- 真實 binding smoke：distribution 0.2.1，可載入 bundled assets、執行 TextRank 與 CKIP tag tokenize（`金管會/Nc`、`前/Nes`、`主委/Na`）。
- 驗證：keyword／snapshot focused 10/10；LingXi＋搜尋 focused 50/50；full pytest 372/372；Ruff full pass；`git diff --check` pass；doctor 8/8；fixture readiness 18/18。
- 現有 v0.6.2 wheel/sdist 早於本次 runtime 變更，正式發布前仍須重建。pip 另警告殘留 `D:\Python310\Lib\site-packages\~ocuma-0.6.2.dist-info` 無效 distribution；本次未刪除該非 LingXi 目標。

---


---

## [2026-08-02] Rust Office parser v1 vertical slice 與 Documa provider

- 新建 `rust_office_parser` workspace：office-core/ooxml/word/sheet/slide/py/cli；公開 `office-layout-v1` event stream，ABI3 py39 wheel `rust-office-parser 0.1.0`。
- 真實 deterministic fixtures 覆蓋 DOCX、BIFF8 XLS、XLSX、PPTX；四者皆經 Documa strict Rust provider 完成 process/search/read/cite。DOCX/XLS/XLSX citations 為 structural worksheet/document label；PPTX shape bbox 為 points visual citation，notes 無 bbox 時維持 logical。
- Provider 已接到 registry、Python tools、CLI、MCP 與 JSON schema。`auto` Rust-first，但只有 `RUST_OFFICE_NOT_INSTALLED`、binding contract mismatch 或 capability unavailable 能讓 DOCX/PPTX fallback；legacy DOC/PPT 與 macro-enabled formats 有穩定錯誤。
- search sidecar `source_digest` 納入 parser、adapter contract、Office binding version 與 requested/actual provider，切換時會失效重建。
- 驗證：Rust fmt/Clippy -D warnings/workspace tests PASS；ABI3 wheel build + Python tests 6/6；Documa Office focused 8/8、Office+PDF focused 14/14、interfaces/citation/registry regression 51/51；Ruff changed files PASS。四 fuzz targets可編譯。
- release gate 仍未完成：fixture manifest 明載 4/24 partial，尚無跨平台 CI 實際 run、24 件 corpus、parity F1 與正式 latency/RSS 報告。

## [2026-08-02] v0.6.3 重建與 package gate

- runtime `pyproject.toml`／`documa.__version__`、Claude Code／Codex／OpenClaw manifests 與四份 plugin install pin 同步推進到 `0.6.3`；CHANGELOG 日期更新為 2026-08-02。
- Python artifacts：`dist/documa-0.6.3-py3-none-any.whl`（SHA-256 `bb1309725f6138d7aadd34b95965a91e91c240a7292e2ed67229c8436a594f80`）與 `dist/documa-0.6.3.tar.gz`（`deacd82fa98ed095ce321106572d0e5317966d2bed80e8f7af74125ad82cd8b3`）。isolated build 因安裝 build backend 逾時，確認本機 `setuptools 80.9.0` 滿足 `>=77.0.3` 後以 `python -m build --no-isolation` 成功重建。
- Plugin artifacts：Claude `plugins/claude-code-documa.zip`（SHA-256 `6411ee61779771a8cb581c359a12bea8c6259dd3bd133fdd0d4d219513f571f8`）；Codex `plugins/codex-documa.zip`（`b9e0041573c0e897360b109a897db3d88b745b964dee16cc5beb93d52c4f8fdd`）。兩者 deterministic `--check` 通過；OpenClaw 依既有慣例同步 source directory，不另造 zip。
- 修正 Codex skill readiness／migration governance 中已失效的 `skill-creator-advanced` gate 路徑，改用 `skillops-studio` 現行 `revise`／`package` stages；stage gate PASS、package release gate PASS（11 eval cases，security PASS；live benchmark SKIPPED，未宣稱 live benchmark）。
- 驗證：focused packaging/lifecycle 12/12；full pytest 372/372；Ruff full pass；`git diff --check` pass；Twine wheel/sdist 2/2 PASS；agent plugin validator PASS；doctor 8/8；fixture readiness 18/18；作用中 wheel smoke 為 Documa module/distribution 0.6.3、LingXi 0.2.1。
- 全域 `D:\Python310` 的 `pip check` 為 FAIL：環境原有多個跨專案缺件／版本衝突，且本次 `--force-reinstall` 重新解析依賴後升級了 Pillow、pydantic、mcp、tiktoken 等套件；Documa gates 仍 PASS，但此環境不可宣稱 dependency-clean。另有既存 `~ocuma-0.6.2.dist-info` 警告未刪除。
- `dist/` 受 `.gitignore` 管理，只保存本機 release artifacts；Git commit 納入 runtime、測試、governance、plugin source 與兩個 tracked plugin zip，不納入 `.documa/` store 或歷史上刻意未追蹤的 `token-economy.json`。

---

## [2026-08-03] Rust Office parser v1 release evidence

- deterministic synthetic corpus 由 4 件補齊為 24 件（DOCX/XLS/XLSX/PPTX 各 6）；manifest 記錄 SHA-256、coverage、provenance、license、expected needle/error，連續重建 hash 穩定。
- 修正契約錯誤碼為 `ENCRYPTED_OFFICE_NOT_SUPPORTED`／`ZIP_PATH_TRAVERSAL`；DOCX `w:gridSpan` 水平合併儲存格依共同能力展開，並受 `max_cells` 限制。
- Windows parity/performance report PASS：6 件 DOCX/PPTX 共同能力 fixture character F1 全為 1.0；DOCX/PPTX median time ratio 分別 0.1026/0.0943，peak RSS ratio 0.5839/0.5874。
- 驗證：Rust fmt/Clippy -D warnings/workspace tests PASS；ABI3 wheel build PASS；binding corpus 52/52；Documa full pytest 381/381；Ruff PASS；四 fuzz targets 各實跑 5 秒無 crash；doctor 8/8；fixture readiness 18/18；agent plugin validator PASS；Codex documa-evidence package gate PASS（live benchmark SKIPPED，未宣稱）。
- 尚餘外部 gate：本機只驗證 Windows；GitHub Windows/Linux/macOS CI 尚未有實際 run，因此跨平台正式 release 狀態維持 pending。

---

## [2026-08-03] Rust PDF／Office 內部子模組整合

- 將兩個 parser source vendored 至 `native/pdf` 與 `native/office`；保留獨立 Cargo workspace，Documa 根目錄透過 `setuptools-rust` 同時建置兩個 Python extension。
- 新增共用 native binding 抽象，統一模組載入、`version_info()` identity、required calls、capabilities 與 JSON error envelope 驗證；PDF／Office adapter 不再各自重複處理 binding 契約。
- PDF workspace 僅將 `pdf-core` 與 Python binding 列為 build members；CLI／WASM source 只保留給既有 contract audit，未納入 Documa wheel 編譯。
- 驗證：Rust fmt、workspace check/test、Clippy `-D warnings` 均 PASS；Python full suite 447/447；Windows CPython 3.10 platform wheel 內含兩個 native extension，PDF 與 DOCX/XLS/XLSX/PPTX 真實 smoke PASS。
- 尚餘外部 gate：Linux/macOS platform wheel 與其他 Python ABI 需 CI 實跑；既有 0.6.3 pure-Python release artifacts／hashes 已被新的 platform wheel 模型取代，發布前必須重建。

---

## [2026-08-04] v0.6.4 native distribution artifacts

- runtime `pyproject.toml`／`documa.__version__`、Claude Code／Codex／OpenClaw manifests 與四份 plugin install pin 同步推進至 `0.6.4`；CHANGELOG 新增 0.6.4 節點。
- Python artifacts：`dist/documa-0.6.4-cp310-cp310-win_amd64.whl`（SHA-256 `2474ea6a4131ccb8281c56e62f198d23c0df0220bd54aefe6bfa499be3266a51`）；`dist/documa-0.6.4.tar.gz`（`fd194df5775e1b20c1b96990d9635481c6dd9c3332ba79da7fdbf2c91ffad6ad`）。
- Plugin artifacts：Claude `plugins/claude-code-documa.zip`（SHA-256 `e1eb8305124459c085bd233df2db34cee0fdfe6ba96f3b2ab474656b1119e31a`）；Codex `plugins/codex-documa.zip`（`730a67c824bd84b429db48153adb5d2b42583137ca0c0f0d8d7ed2374f66d82d`）；OpenClaw 依既有慣例同步 source directory。
- 驗證：wheel/sdist build PASS，且 wheel 從 sdist 再編譯 PASS；wheel 內含兩個 CPython 3.10 Windows `.pyd`；隔離 wheel full pytest 447/447；Twine 2/2、Ruff、doctor 8/8、fixture readiness 18/18、agent plugin validator、deterministic zip check、Codex skill package gate 全 PASS。package gate 的 live benchmark 維持 SKIPPED，未宣稱 live benchmark。
- 尚餘外部 gate：Linux/macOS 與其他 Python ABI wheel 未實跑；0.6.4 尚未 tag、發布或安裝到全域 Python。

---

## [2026-08-21] Repository Intelligence Graph v1

- 新增 `documa.codegraph`：versioned SQLite derived index、stable IDs、Python AST/symbol resolver、typed structural/import/call edges、EXACT／RESOLVED／POSSIBLE／UNRESOLVED receipt、Tarjan SCC 與 coupling metrics；source files 維持唯一權威，DB 不保存完整原文。
- sync 使用 file hash 做 add/update/delete/no-op 與 generation 原子切換；失敗檔案標 unavailable 並撤除舊 hard edges。保留 active／previous generation 供 diff 與 evidence-bound impact。
- 新增 Python API、`code-graph-sync/query/read` CLI、admin/advanced MCP 工具，以及 agent profile 單一 `documa_code_context`；query/read 提供 proof path、source span/hash、uncertainty、候選 tests 與 byte/token-bounded evidence。
- 新增 `CodeLanguageAdapter` 與 decoded SCIP import adapter；Python analyzer 為 authoritative，Tree-sitter／protobuf 僅列入 `documa[code]` optional extra。選配 summary enrichment 存獨立 derived table，不得改 graph facts。
- Codex／Claude plugin 新增 `documa-codegraph` skill 並重建 deterministic zip；既有 ContextIR 1.0 與 `context_from_code()` 未更動。
- 驗證：`tests/` 416 passed／4 skipped；Python resolver gold 的 hard-edge precision／recall 均為 100%；Ruff full PASS；plugin validator、兩份 skill validator、deterministic zip check 與 `git diff --check` PASS。合成 1,000,001 行 corpus：cold 11.04s、no-op 0.172s、單檔增量 0.353s、bounded query 18ms、peak RSS 56.5MB，皆通過既定 gate。全套曾遇一次既有 registry Windows concurrent replace 暫態失敗，該案例單獨重跑與其後全套皆 PASS。
- Source-tree 根目錄直接收集 `native/` 測試仍需已編譯的 `rust_pdf._native`／`rust_office._core`；本次完整產品測試限定 `tests/`，未把缺少 native build 視為 graph 回歸。wheel/sdist smoke 亦未宣稱通過：非隔離建置缺 `setuptools-rust`，隔離建置停在 build dependency 準備且無輸出，有限等待後中止。
