# 規格整理 v 1.2.0

## Codex / Claude Code 分階段開發計畫

Documa codegraph 擴充｜2026-11-25

[技術規格與驗收條件](codegraph-technical.md) · [非技術規格](codegraph-user.md)

依 Stage 0～8 執行。每次只貼當前階段對應宿主的區塊到 Documa repo 內的聊天。AC-01～AC-08 對照技術規格。每階段完成後保存實際命令、環境、版本、結果和阻擋項目。必要驗收受阻時，先排除阻礙再進入下游。階段指令以實作交付為目標，這份計畫的撰寫本身不代表功能已實作。

| 順序 | 交付切片 | 驗收 |
| --- | --- | --- |
| Stage 0 | 基線、固定查詢與可重現故障 | AC-01～AC-08 基準 |
| Stage 1–2 | 索引可靠度與可取證查詢 | AC-01、AC-02 |
| Stage 3–4 | 跨文件及 Rust 邊界 | AC-03、AC-04 |
| Stage 5–6 | Git 影響與可定位導覽 | AC-05、AC-06 |
| Stage 7 | 整合、回歸、宿主與安全邊界 | AC-01～AC-08 |
| Stage 8 | 操作文件與本機交付 | 重跑操作範例與交付檢查 |

### Stage 0｜建立基線與驗收資料

固定既有契約、測試環境和 P0／P1／P2 共同使用的基準。 取得本規格與現有 Documa 開發 checkout。依 AGENTS.md 先讀 DEVNOTE.md，保留其他工作中的修改。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 0「建立基線與驗收資料」。固定既有契約、測試環境和 P0／P1／P2 共同使用的基準。
前置｜取得本規格與現有 Documa 開發 checkout。依 AGENTS.md 先讀 DEVNOTE.md，保留其他工作中的修改。
檔案｜docs/spec/codegraph-technical.md、tests/test_code_graph.py、tests/test_context_graph.py、pyproject.toml、src/documa/interfaces/tool_schemas.py；新增 tests/fixtures/codegraph/ 下的合成材料或基準問題資料。
步驟｜重用 pytest、Ruff、現有 CLI/MCP 與 SQLite。在簡單 Windows 路徑重跑基線，另建立含 #、空白、Unicode 和 source_roots 變更重現。建立 12 題以上固定查詢與 golden locator，保存舊 lookup／rg、延遲、工具次數和 token 基線。確認以既有 documa[code] 作選配安裝路徑，基線無金鑰樣板；用隔離衍生庫驗證現有版本可開啟，記錄新舊索引重建策略。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜python -m pytest -q --basetemp <unique-simple-temp-path> tests/test_code_graph.py tests/test_context_graph.py。另分開執行故障重現與基準查詢。
驗收｜記錄 commit、OS、版本、命令、原始結果與可重跑 fixture；預期失敗有明確原因。鎖定 AC-01～AC-08 數值及 gate 適用範圍，功能行為維持既有契約。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 0「建立基線與驗收資料」。固定既有契約、測試環境和 P0／P1／P2 共同使用的基準。
前置｜取得本規格與現有 Documa 開發 checkout。依 AGENTS.md 先讀 DEVNOTE.md，保留其他工作中的修改。
檔案｜docs/spec/codegraph-technical.md、tests/test_code_graph.py、tests/test_context_graph.py、pyproject.toml、src/documa/interfaces/tool_schemas.py；新增 tests/fixtures/codegraph/ 下的合成材料或基準問題資料。
步驟｜重用 pytest、Ruff、現有 CLI/MCP 與 SQLite。在簡單 Windows 路徑重跑基線，另建立含 #、空白、Unicode 和 source_roots 變更重現。建立 12 題以上固定查詢與 golden locator，保存舊 lookup／rg、延遲、工具次數和 token 基線。確認以既有 documa[code] 作選配安裝路徑，基線無金鑰樣板；用隔離衍生庫驗證現有版本可開啟，記錄新舊索引重建策略。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜python -m pytest -q --basetemp <unique-simple-temp-path> tests/test_code_graph.py tests/test_context_graph.py。另分開執行故障重現與基準查詢。
驗收｜記錄 commit、OS、版本、命令、原始結果與可重跑 fixture；預期失敗有明確原因。鎖定 AC-01～AC-08 數值及 gate 適用範圍，功能行為維持既有契約。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。此階段只新增 fixtures 與基線紀錄，撤回該批新增項即可恢復。

### Stage 1｜修復同步並提供新鮮度

正常查詢含特殊字元的 workspace，並在 repo 變更後可靠刷新。 Stage 0 的重現、baseline 與 fixture 可用。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 1「修復同步並提供新鮮度」。正常查詢含特殊字元的 workspace，並在 repo 變更後可靠刷新。
前置｜Stage 0 的重現、baseline 與 fixture 可用。
檔案｜src/documa/codegraph/store.py、models.py、cli.py、interfaces/tools.py、interfaces/mcp_server.py、interfaces/tool_schemas.py、tests/test_code_graph.py；必要時在 codegraph 新增監看模組。
步驟｜依 CG-01 修 SQLite URI 與設定指紋。用來源變更與反向依賴決定受影響的解析範圍，以完整重建作為比對。維持單 writer 和原子提交。提供唯讀 freshness/status、顯式啟動的 watch 和限定 workspace 的 clear；管理操作需指定 scope。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜執行 codegraph 測試。涵蓋 Windows 字元、相同檔案不同 roots、匯入重解、刪除、重新命名、分支切換、取消、事件溢出與同步結果等價性。
驗收｜AC-01 通過，既有權限與 CLI 入口相容。失敗可恢復，監看可停止，唯讀查詢無索引寫入。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 1「修復同步並提供新鮮度」。正常查詢含特殊字元的 workspace，並在 repo 變更後可靠刷新。
前置｜Stage 0 的重現、baseline 與 fixture 可用。
檔案｜src/documa/codegraph/store.py、models.py、cli.py、interfaces/tools.py、interfaces/mcp_server.py、interfaces/tool_schemas.py、tests/test_code_graph.py；必要時在 codegraph 新增監看模組。
步驟｜依 CG-01 修 SQLite URI 與設定指紋。用來源變更與反向依賴決定受影響的解析範圍，以完整重建作為比對。維持單 writer 和原子提交。提供唯讀 freshness/status、顯式啟動的 watch 和限定 workspace 的 clear；管理操作需指定 scope。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜執行 codegraph 測試。涵蓋 Windows 字元、相同檔案不同 roots、匯入重解、刪除、重新命名、分支切換、取消、事件溢出與同步結果等價性。
驗收｜AC-01 通過，既有權限與 CLI 入口相容。失敗可恢復，監看可停止，唯讀查詢無索引寫入。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。停止監看程序，切回舊版及保留的舊 sidecar 或在舊版隔離路徑重新建索引，工作樹內容由使用者管理。

### Stage 2｜提供可取證的 explore 查詢

以自然文字定位實際程式入口並回傳具證據的路徑。 Stage 1 的 generation 檢查與取證一致性驗收通過。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 2「提供可取證的 explore 查詢」。以自然文字定位實際程式入口並回傳具證據的路徑。
前置｜Stage 1 的 generation 檢查與取證一致性驗收通過。
檔案｜src/documa/codegraph/service.py、models.py、store.py、src/documa/cli.py、src/documa/interfaces/{tools,tool_schemas,mcp_server,tool_profiles}.py、tests/test_code_graph.py。
步驟｜依 CG-02 加入 explore 排序與去重、可定位的 callsite 原文驗證、完整回應 hard cap、可續查 action。沿用既有 lookup 合約及單 agent 工具。以查詢測例比較舊 lookup 與新意圖，輸出哪個 locator 命中及實際 token 計數。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-02 固定集、原文驗證、token cap、同分排序、預算不足、無 tokenizer、generation 競爭及 CLI/MCP 參數對齊測試。
驗收｜PDF provider 正確入口進前三。精確符號排序、固定集與預算全部達標。既有 intent 和既有證據預算語義相容。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 2「提供可取證的 explore 查詢」。以自然文字定位實際程式入口並回傳具證據的路徑。
前置｜Stage 1 的 generation 檢查與取證一致性驗收通過。
檔案｜src/documa/codegraph/service.py、models.py、store.py、src/documa/cli.py、src/documa/interfaces/{tools,tool_schemas,mcp_server,tool_profiles}.py、tests/test_code_graph.py。
步驟｜依 CG-02 加入 explore 排序與去重、可定位的 callsite 原文驗證、完整回應 hard cap、可續查 action。沿用既有 lookup 合約及單 agent 工具。以查詢測例比較舊 lookup 與新意圖，輸出哪個 locator 命中及實際 token 計數。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-02 固定集、原文驗證、token cap、同分排序、預算不足、無 tokenizer、generation 競爭及 CLI/MCP 參數對齊測試。
驗收｜PDF provider 正確入口進前三。精確符號排序、固定集與預算全部達標。既有 intent 和既有證據預算語義相容。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。移除新增 explore 意圖入口，保留現有 lookup/trace。若加了衍生搜尋欄位，使用既有 schema 路徑重建。

### Stage 3｜連結規格與測試證據

從符號找到可驗證的規格引用與候選測試，也能由規格追到實作。 Stage 2 可驗證不同證據位置並分配證據預算。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 3「連結規格與測試證據」。從符號找到可驗證的規格引用與候選測試，也能由規格追到實作。
前置｜Stage 2 可驗證不同證據位置並分配證據預算。
檔案｜src/documa/codegraph/{models,store,service}.py、src/documa/context/{adapters,service}.py；必要時新增本機文件連結 adapter，擴充 CLI/MCP 同步參數與 tests/test_code_graph.py。
步驟｜依 CG-03 限制 repository scope，重用 DocumentIR／ContextIR，建立對應來源 digest、block ID 與 span 的關係。提取明確路徑、符號、設定鍵及直接測試呼叫，把同名等文字推斷列為候選。實作更新、移除與原文雜湊核對。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-03、文件同名碰撞、Unicode 連結、來源改刪、無效 anchors、symlink escape、忽略規則、原文內指令文字與既有 ContextIR 回歸。
驗收｜正反向連結可查。確定關係具原始出處，候選需 opt-in，文件變更後舊證據不可通過驗證。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 3「連結規格與測試證據」。從符號找到可驗證的規格引用與候選測試，也能由規格追到實作。
前置｜Stage 2 可驗證不同證據位置並分配證據預算。
檔案｜src/documa/codegraph/{models,store,service}.py、src/documa/context/{adapters,service}.py；必要時新增本機文件連結 adapter，擴充 CLI/MCP 同步參數與 tests/test_code_graph.py。
步驟｜依 CG-03 限制 repository scope，重用 DocumentIR／ContextIR，建立對應來源 digest、block ID 與 span 的關係。提取明確路徑、符號、設定鍵及直接測試呼叫，把同名等文字推斷列為候選。實作更新、移除與原文雜湊核對。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-03、文件同名碰撞、Unicode 連結、來源改刪、無效 anchors、symlink escape、忽略規則、原文內指令文字與既有 ContextIR 回歸。
驗收｜正反向連結可查。確定關係具原始出處，候選需 opt-in，文件變更後舊證據不可通過驗證。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。調回 Python-only scope 並重建 workspace 索引；既有文件與 ContextIR 來源保持可讀。

### Stage 4｜打通 Rust 與 PyO3 路徑

讓使用者查到 Python 包裝如何進入 Documa 原生套件。 Stage 2 的路徑證據完成、Stage 3 的跨來源覆蓋與狀態欄位已固定。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 4「打通 Rust 與 PyO3 路徑」。讓使用者查到 Python 包裝如何進入 Documa 原生套件。
前置｜Stage 2 的路徑證據完成、Stage 3 的跨來源覆蓋與狀態欄位已固定。
檔案｜src/documa/codegraph/{models,store,service,scip}.py、src/documa/codegraph/ 新增 Rust adapter；src/documa/cli.py、src/documa/interfaces/、pyproject.toml（僅需相容選配依賴時）、tests/test_code_graph.py、tests/fixtures/codegraph/。
步驟｜依 CG-04 實作 adapter 選擇、Cargo workspace/crate 解析、Rust 定義與可解析呼叫、PyO3 註冊對應。以 native/pdf、native/office、native/lingxi 和 src 對應的 Python 包裝取固定驗收點。將語言與 adapter 版本加入 generation，回報未解析巨集和缺少能力。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-04、唯一與歧義 PyO3 匯出、alias、宏、缺 grammar、Python-only 安裝及多語言增量等價測試。
驗收｜PDF、Office、LingXi 各驗證一條可靜態核對的跨語言路徑，揭露覆蓋缺口。Python 預設和既有 SCIP 合約相容。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 4「打通 Rust 與 PyO3 路徑」。讓使用者查到 Python 包裝如何進入 Documa 原生套件。
前置｜Stage 2 的路徑證據完成、Stage 3 的跨來源覆蓋與狀態欄位已固定。
檔案｜src/documa/codegraph/{models,store,service,scip}.py、src/documa/codegraph/ 新增 Rust adapter；src/documa/cli.py、src/documa/interfaces/、pyproject.toml（僅需相容選配依賴時）、tests/test_code_graph.py、tests/fixtures/codegraph/。
步驟｜依 CG-04 實作 adapter 選擇、Cargo workspace/crate 解析、Rust 定義與可解析呼叫、PyO3 註冊對應。以 native/pdf、native/office、native/lingxi 和 src 對應的 Python 包裝取固定驗收點。將語言與 adapter 版本加入 generation，回報未解析巨集和缺少能力。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-04、唯一與歧義 PyO3 匯出、alias、宏、缺 grammar、Python-only 安裝及多語言增量等價測試。
驗收｜PDF、Office、LingXi 各驗證一條可靜態核對的跨語言路徑，揭露覆蓋缺口。Python 預設和既有 SCIP 合約相容。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。透過語言 scope 啟用 Python-only 後重建衍生庫；選配 Rust 解析依賴可獨立撤回。

### Stage 5｜交付 Git 變更影響報告

從版本差異找到受影響功能、相關文件與建議重跑測試。 Stage 1～4 維持舊 diff 行為，並具備程式、文件及 Rust 的來源定位。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 5「交付 Git 變更影響報告」。從版本差異找到受影響功能、相關文件與建議重跑測試。
前置｜Stage 1～4 維持舊 diff 行為，並具備程式、文件及 Rust 的來源定位。
檔案｜src/documa/codegraph/service.py、store.py、models.py、必要時新增 Git 比較模組；src/documa/cli.py、src/documa/interfaces/ 及 tests/test_code_graph.py。
步驟｜依 CG-05 新增 git_diff。以只讀 Git object 和工作樹資料取得前後 span，對應新增、刪除、改名、修改的符號，再沿圖譜找影響路徑。以原始 Git revision 和原文 hash 驗證歷史證據。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-05、兩個 commit、工作樹 staged/unstaged/untracked、特殊檔名、ref 不存在、刪除證據、分支變動與既有 diff 回歸。
驗收｜可從使用者指定比較範圍得到結構化報告，刪除符號可核對舊版來源，測試候選附可驗證的理由。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 5「交付 Git 變更影響報告」。從版本差異找到受影響功能、相關文件與建議重跑測試。
前置｜Stage 1～4 維持舊 diff 行為，並具備程式、文件及 Rust 的來源定位。
檔案｜src/documa/codegraph/service.py、store.py、models.py、必要時新增 Git 比較模組；src/documa/cli.py、src/documa/interfaces/ 及 tests/test_code_graph.py。
步驟｜依 CG-05 新增 git_diff。以只讀 Git object 和工作樹資料取得前後 span，對應新增、刪除、改名、修改的符號，再沿圖譜找影響路徑。以原始 Git revision 和原文 hash 驗證歷史證據。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-05、兩個 commit、工作樹 staged/unstaged/untracked、特殊檔名、ref 不存在、刪除證據、分支變動與既有 diff 回歸。
驗收｜可從使用者指定比較範圍得到結構化報告，刪除符號可核對舊版來源，測試候選附可驗證的理由。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。停用 git_diff 意圖；原有最新同步 diff 仍可用。清理僅限該次隔離比較的衍生資料。

### Stage 6｜交付可定位的專案導覽

讓使用者從入口按順序理解元件，並能請宿主生成導覽或 PR 摘要。 Stage 5 影響報告和 CG-02 查詢回應預算已可驗證。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 6「交付可定位的專案導覽」。讓使用者從入口按順序理解元件，並能請宿主生成導覽或 PR 摘要。
前置｜Stage 5 影響報告和 CG-02 查詢回應預算已可驗證。
檔案｜src/documa/codegraph/service.py、src/documa/cli.py、src/documa/interfaces/、plugins/codex-documa/skills/documa-codegraph/SKILL.md、plugins/claude-code-documa/skills/documa-codegraph/SKILL.md、其餘存在的 codegraph skill、tests/test_code_graph.py。
步驟｜依 CG-06 新增 tour 順序、入口、來源、缺口與固定模板輸出。更新宿主技能，在現有模型中根據證據寫回答、導覽文件和 PR 摘要；來源不足時明說，模型產文沿用宿主原生 Streaming。工具本身回完整結構化結果。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-06、tour 固定模板快照、排序穩定與引用驗證，並在 AGENTS/skill 正常載入的宿主執行最小 smoke。
驗收｜固定模板在無金鑰的 CLI 可用。支援的宿主可用現有對話生成有引用的文件，過程無額外模型服務。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 6「交付可定位的專案導覽」。讓使用者從入口按順序理解元件，並能請宿主生成導覽或 PR 摘要。
前置｜Stage 5 影響報告和 CG-02 查詢回應預算已可驗證。
檔案｜src/documa/codegraph/service.py、src/documa/cli.py、src/documa/interfaces/、plugins/codex-documa/skills/documa-codegraph/SKILL.md、plugins/claude-code-documa/skills/documa-codegraph/SKILL.md、其餘存在的 codegraph skill、tests/test_code_graph.py。
步驟｜依 CG-06 新增 tour 順序、入口、來源、缺口與固定模板輸出。更新宿主技能，在現有模型中根據證據寫回答、導覽文件和 PR 摘要；來源不足時明說，模型產文沿用宿主原生 Streaming。工具本身回完整結構化結果。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 AC-06、tour 固定模板快照、排序穩定與引用驗證，並在 AGENTS/skill 正常載入的宿主執行最小 smoke。
驗收｜固定模板在無金鑰的 CLI 可用。支援的宿主可用現有對話生成有引用的文件，過程無額外模型服務。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。撤回新增 tour 意圖或宿主提示版本，已完成的 structured evidence 入口仍可查詢。

### Stage 7｜完成整合、回歸與邊界驗收

用正常入口驗收各階段與宿主契約，保留可重跑的證據。 Stage 0～6 功能與單階段驗收已提交在可測 checkout，開發測試未依賴私有文件或外部模型金鑰。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 7「完成整合、回歸與邊界驗收」。用正常入口驗收各階段與宿主契約，保留可重跑的證據。
前置｜Stage 0～6 功能與單階段驗收已提交在可測 checkout，開發測試未依賴私有文件或外部模型金鑰。
檔案｜tests/test_code_graph.py、tests/test_context_graph.py、tests/fixtures/codegraph/、現有 MCP/CLI/schema 測試、scripts/validate_agent_plugins.py、scripts/package_plugins.py、docs/spec/codegraph-development-plan.md 的驗證紀錄。
步驟｜執行 AC-01～AC-08，在 Windows/Linux 比對舊版、增量與乾淨重建。對 Documa 程序禁外網並移除模型金鑰驗證純程式路徑；用 Codex 與 Claude Code 既有模型各 smoke 一次，核對引用、缺口提示及支援宿主的 Streaming。紀錄實際命令、結果、缺口及效能。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 codegraph/ContextIR、CLI/MCP/schema profiles、Ruff、plugin validator 及 deterministic packaging checks。重跑隔離的逾時、讀寫衝突、取消、scope 逃逸、提示文字注入和 Git ref 邊界案例。
驗收｜各 AC 記錄 PASS、FAIL 或 BLOCKED，具環境與證據，必要 gate 全 PASS 才能放行。未接通的宿主驗證據實登錄 BLOCKED。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 7「完成整合、回歸與邊界驗收」。用正常入口驗收各階段與宿主契約，保留可重跑的證據。
前置｜Stage 0～6 功能與單階段驗收已提交在可測 checkout，開發測試未依賴私有文件或外部模型金鑰。
檔案｜tests/test_code_graph.py、tests/test_context_graph.py、tests/fixtures/codegraph/、現有 MCP/CLI/schema 測試、scripts/validate_agent_plugins.py、scripts/package_plugins.py、docs/spec/codegraph-development-plan.md 的驗證紀錄。
步驟｜執行 AC-01～AC-08，在 Windows/Linux 比對舊版、增量與乾淨重建。對 Documa 程序禁外網並移除模型金鑰驗證純程式路徑；用 Codex 與 Claude Code 既有模型各 smoke 一次，核對引用、缺口提示及支援宿主的 Streaming。紀錄實際命令、結果、缺口及效能。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜跑 codegraph/ContextIR、CLI/MCP/schema profiles、Ruff、plugin validator 及 deterministic packaging checks。重跑隔離的逾時、讀寫衝突、取消、scope 逃逸、提示文字注入和 Git ref 邊界案例。
驗收｜各 AC 記錄 PASS、FAIL 或 BLOCKED，具環境與證據，必要 gate 全 PASS 才能放行。未接通的宿主驗證據實登錄 BLOCKED。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。依失敗 AC 回到負責的 Stage；隔離測試與暫存索引使用獨立路徑，保留原始故障證據。

### Stage 8｜完成操作文件與交付

交付可操作的使用手冊、相容性與回滾說明。 Stage 7 必要 gates 全部 PASS，且有可再現的命令和結果。

#### Codex Instructions

```text
在 Documa workspace 的 Codex 聊天貼上此段，先讀 AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 8「完成操作文件與交付」。交付可操作的使用手冊、相容性與回滾說明。
前置｜Stage 7 必要 gates 全部 PASS，且有可再現的命令和結果。
檔案｜docs/spec/codegraph-technical.md、docs/spec/codegraph-user.md、docs/spec/codegraph-development-plan.md、docs/documa/、README.md（核對現有編輯後才改相關段落）、plugins/ 的 codegraph skill 與既有打包工具。
步驟｜依實際 CLI/MCP 介面撰寫安裝、同步、查詢、版本比較、導覽、錯誤復原與無額外金鑰示範。說明回答與文件由宿主 LLM 生成，支援的宿主表面沿用 Streaming。標記選配能力與保守限制，核對範例能直接操作。
宿主｜從 workspace 執行既有 CLI、測試和 Codex plugin 入口；長期操作說明優先放現有 Codex codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜重跑文件中的指令、白話用詞檢查、plugin validator 與 deterministic package 檢查。對照 Stage 7 結果、來源路徑和可回滾操作。
驗收｜三份規格、操作文件與 gate 結果一致。交付包含基線、實作版本、回滾方式及未涵蓋項目，交付動作限本機檔案。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

#### Claude Code Instructions

```text
在 Documa 根目錄開啟 Claude Code 並貼上此段。讀取適用的 CLAUDE.md（若存在）、AGENTS.md、DEVNOTE.md 和 docs/spec/codegraph-technical.md。
任務｜只交付 Stage 8「完成操作文件與交付」。交付可操作的使用手冊、相容性與回滾說明。
前置｜Stage 7 必要 gates 全部 PASS，且有可再現的命令和結果。
檔案｜docs/spec/codegraph-technical.md、docs/spec/codegraph-user.md、docs/spec/codegraph-development-plan.md、docs/documa/、README.md（核對現有編輯後才改相關段落）、plugins/ 的 codegraph skill 與既有打包工具。
步驟｜依實際 CLI/MCP 介面撰寫安裝、同步、查詢、版本比較、導覽、錯誤復原與無額外金鑰示範。說明回答與文件由宿主 LLM 生成，支援的宿主表面沿用 Streaming。標記選配能力與保守限制，核對範例能直接操作。
宿主｜從 repo 執行既有 CLI 與測試；宿主操作說明優先放現有 Claude Code codegraph skill。 回答和文件一律由宿主內建模型生成，支援時沿用 Streaming，Documa 不接模型服務或額外金鑰。
測試｜重跑文件中的指令、白話用詞檢查、plugin validator 與 deterministic package 檢查。對照 Stage 7 結果、來源路徑和可回滾操作。
驗收｜三份規格、操作文件與 gate 結果一致。交付包含基線、實作版本、回滾方式及未涵蓋項目，交付動作限本機檔案。
輸出｜回報實際修改檔、指令、結果、剩餘限制及回滾點。保留現有使用者修改。
```

**風險與回滾**。回到對應版本的文件與 plugin bundle，透過各階段隔離路徑重建相容 sidecar。

### 規格包驗證紀錄

2026-11-25 於 `fea2724` 核對規格包和現有基線。新功能與宿主端 gate 由 Stage 7 留存實測結果。

| 檢查 | 實際結果 |
| --- | --- |
| `python <spec-organizer>/scripts/check_plain_language.py docs/spec/codegraph-user.md` | PASS，零禁用詞。 |
| Python 靜態檢查三份 Markdown | PASS，UTF-8、標題、相對連結、SVG XML、JSON 範例皆有效。查核 9 個階段、18 個可貼用區塊、6 項能力及 8 組驗收條件。 |
| `python -m pytest -q --basetemp <unique-simple-temp-path> tests/test_code_graph.py tests/test_context_graph.py` | 現有 Windows 基線 19 passed，exit code 0。Python 結束時另有本機 `pyreadline3` 清理訊息。 |

#### Stage 0 開發基線

Windows 10 build 26300、CPython 3.10.10、SQLite 3.39.4、tiktoken 0.13.0。以 `PYTHONPATH=src` 指向 `fea2724` 工作區原始碼，在隔離的簡單路徑建立 SQLite。現有測試 19 項通過。歷史實測在含 `#` 原生暫存路徑重現 SQLite URI 開啟失敗，也重現只改 `source_roots` 未更新限定符號名稱。

- 固定查詢集 `tests/fixtures/codegraph/queries.json` 共 12 題。執行 `python scripts/benchmark_code_graph.py --scratch-dir <simple-temp-parent> --query-suite tests/fixtures/codegraph/queries.json`。舊圖譜 top-3 與證據命中均為 6/12，自由文字分組為 2/6。`rg` 路徑搜尋與讀檔對照組 12/12，使用每題的手寫模式、至多讀三個整檔，單題 2–4 次操作。PDF provider 舊圖譜結果為 10,011 tokens 且未命中，整檔對照為 18,944 tokens 且命中，使用本機 `o200k_base` 計數。
- 同一 script 用 `--lines 10000 --files 50` 的合成基線所有既有 gates 通過。冷建 0.227 s、no-op 0.047 s、單檔更新 0.059 s、查詢 0.002 s，單檔更新只解析 1 檔。以上耗時是本機基線，較大規模仍由既有 scale gate 驗證。
- AC-02 使用 2,000 tokens 預設與 80% top-3 門檻。Stage 2 先驗證已可解析的 10 題精確／自由文字案例，至少命中 8 題，四個精確符號均須第一名。Stage 7 用全部 12 題，至少命中 10 題，兩題跨依賴／來源同時各有獨立的 AC 驗收。其餘 AC 依技術規格判定，宿主端與 Linux 結果另記實測。

#### Stage 1 索引驗收

- Windows 預設含 `#` 的 sandbox 暫存路徑直接執行 `python -m pytest -q tests/test_code_graph.py tests/test_context_graph.py tests/test_interfaces_and_cli.py tests/test_schema_compatibility.py tests/test_mcp_stdio_watchdog.py`，43 項通過。涵蓋 Unicode SQLite URI、source roots 更新、改名、切換分支、中斷回滾、唯讀新鮮度、限定 workspace 清理，以及衍生圖譜與乾淨重建的紀錄等價。
- 使用 `python scripts/benchmark_code_graph.py --scratch-dir <simple-temp-parent>` 驗證 1,000,001 行、1,001 檔。所有既有 gates 通過，冷建 17.533 s、no-op 0.530 s、單檔更新 1.179 s、lookup 0.019 s。no-op 解析 0 檔，單檔更新解析 1 檔。使用固定間隔掃描做前景監看，局部解析由逆向依賴界定，綁定或成員變更時會記錄全圖重算原因。

#### Stage 2 查詢驗收

- `python scripts/benchmark_code_graph.py --scratch-dir <simple-temp-parent> --query-suite tests/fixtures/codegraph/queries.json --query-intent explore --check-query-quality` 通過四項 gates。10 題 Stage 2 查詢全數命中前三且附所需檔案證據，四題精確符號全數第一名。PDF provider 第一筆為 `src/documa/adapters/registry.py`，完整回應 1,899 tokens。整組最高 1,953 tokens。尚待跨依賴與文件關係的兩題獨立列入 Stage 7。
- Stage 1 使用的同一組五個 pytest 檔案現有 45 項通過。新增整份回應計數、局部原文與續讀、CLI 與工具描述對齊、呼叫點的來源雜湊、過期提示及缺本機 tokenizer 的驗收。

#### Stage 3 文件證據驗收

- 同一組五個 pytest 檔案現有 46 項通過。測試包含 README 明確路徑與符號連結、只以名稱比對的候選、錯誤 anchor、圍欄中的樣例連結、外部與越界路徑、直接測試呼叫和候選測試依據。
- 以本機 DocumentIR fixture 從程式符號追到 `source_b1` 並讀回原文。Markdown 修改後拒絕舊證據，刪除明確文件並重同步後移除衍生連結。Stage 2 的查詢品質 gates 在加入文件索引後仍通過。

#### Stage 4 原生模組驗收

- 同一組五個 pytest 檔案以隔離的 Tree-sitter 0.25.2／Rust grammar 0.24.2 執行，47 項通過。在未裝 grammar 的原本 Python 另驗證 Cargo 可查、Rust 檔回報 `CODE_RUST_GRAMMAR_UNAVAILABLE`。Ruff、`git diff --check`、`uv lock --check` 通過。
- 實際同步 Documa 的 Python 與 Rust scope，127/127 Rust 檔、19/19 Cargo 檔解析成功。PDF 25 條、Office 3 條、LingXi 12 條靜態綁定邊。三組各實讀 Python 使用位置、Rust 定義和 PyO3 註冊來源。可核對的例子為 `src/rust_pdf/__init__.py:9` → PDF `lib.rs:127,488`、`src/rust_office/__init__.py:42` → Office `lib.rs:114,168`、`src/documa/_vendor/lingxi/__init__.py:19` → LingXi `lib.rs:135,674`。Rust 收據另列 3,977 個未解析參照或語法缺口，保守保留動態分派等限制。

#### Stage 5 Git 歷史證據驗收

- 同一組五個 pytest 檔案在隔離 Rust 環境執行，48 項通過。以兩個本機 commits 加一份工作樹測新增、刪除、改名、修改、忽略檔案、錯誤 ref 和唯讀工作樹。刪掉的 `old.removed` 仍可由 base commit 的原始 blob 重新取得並比對相同雜湊。既有同步差異 `diff` 仍受同一組測試覆蓋。

#### Stage 6 導覽驗收

- 同一組五個 pytest 檔案在隔離 Rust 環境執行，49 項通過。導覽 fixtures 驗證無 tokenizer 的 Markdown 輸出、原始位置、分支、相同 generation 重現。實跑 Documa 的 `adapter_for_source` 導覽取得附來源連結的 Markdown。
- `python scripts/validate_agent_plugins.py`、`python scripts/package_plugins.py --check` 通過。本機重建 Codex、Claude Code、Hermes 的 tracked ZIP，宿主模型的實際 tool call 與 Streaming 驗收移至 Stage 7。

#### Stage 7 整合驗收

| Gate | 實測結果 |
| --- | --- |
| AC-01、AC-08 Windows | PASS。codegraph、ContextIR、CLI/MCP、schema、watchdog、packaging 共 59 項通過。 |
| AC-01、AC-08 Linux | PASS。WSL 隔離 Python 3.12.3 環境，57 項通過、2 項依平台略過、6 個 subtests 通過。 |
| AC-02 查詢與預算 | PASS。12 題固定查詢集 11 題在前三筆命中且有對應證據，四題精確符號均第一名。PDF provider 題 1,733 tokens，整組最高 1,977 tokens。`dependency-store` 機械對照題仍未在短結果中命中，真實依賴與跨來源關係另依 AC-03、AC-04 驗證。 |
| AC-03、AC-04、AC-05、AC-06 功能 | PASS。對應文件、Rust、Git、導覽案例包含在跨平台套件回歸。原生三個套件的確證鏈見 Stage 4。 |
| AC-07 獨立 Documa | PASS。Python 測試移除模型金鑰並攔截網路連線。已具本機詞彙時 `explore`、`tour` 可用，冷詞彙快取且無本機已載入 tokenizer 時回 `TOKEN_COUNTER_UNAVAILABLE`。 |
| AC-07 Codex 與 Claude Code | PASS。兩端都透過同一合成 fixture 的 MCP 工具讀到來源並在回覆引用 `provider.py`。Claude Code 另核對到 partial message events，Codex 核對 JSONL 的工具完成及模型回覆事件。Codex 僅在該次測試程序核准指定唯讀工具。 |
| 靜態與封裝 | PASS。Ruff、`git diff --check`、`uv lock --check`、插件 validator 與 deterministic ZIP gate 通過。 |

額外的 Windows Documa 與原生 PDF 全套合計 643 passed、2 skipped、1 failed，原生 Office 97 passed、5 skipped。唯一失敗是既有文件檢索的 `test_search_uses_hierarchical_route_sidecar_for_large_outline`，在隔離 `fea2724` 原始碼上重現相同的 `KeyError: 'branch_id'`。Windows 使用隔離副本測試，Office 以目前 Cargo 原始碼離線重編。這份廣泛回歸結果完整保留。

Codex 冒煙的單工具臨時核准方式依官方設定參考。 [OpenAI Docs](https://learn.chatgpt.com/docs/config-file/config-reference)

#### Stage 8 文件與交付驗收

- 新增 [程式庫圖譜操作指南](../documa/code-graph.md)，在 [README 文件索引](../../README.md) 加入入口。包含本機 tokenizer 準備、語言選擇、同步、Git 歷史取證、固定模板、宿主生成及復原方式。
- 從實際工作區執行 status、explore、tour 與 Git 歷史比較，均回傳成功。PDF provider 查詢原文位於 `src/documa/adapters/registry.py`，完整回應 1,739 tokens。導覽輸出含 Rust-first 分支，實際 `HEAD~1` 對 `HEAD` 的比較回報 6 個符合索引範圍的檔案變更。
- 白話禁語檢查、文件相對連結、SVG XML、JSON 範例、插件 validator 與 deterministic ZIP 檢查通過。交付包含工作區原始碼、三份規格、操作指南及三份本機插件 ZIP。
