# 規格整理 v 1.2.0

## 技術規格文件

Documa codegraph 擴充｜2026-11-25｜基線 `main` `fea2724`

[非技術規格](codegraph-user.md) · [分階段開發計畫](codegraph-development-plan.md)

### 背景、目標與範圍

現有 Python 圖譜已支援雜湊增量解析、符號與依賴查詢、影響分析、候選測試、圖譜差異與來源驗證。實際查詢 `PDF parser provider fallback` 時，前五筆未命中 provider 選擇入口。基線也重現了 Windows 路徑含 `#` 時 SQLite 唯讀開啟失敗，以及只更改 `source_roots` 時沿用舊符號。相關測試在一般路徑通過 19 項。這些是基線觀察，功能交付由下方驗收項目判定。 [同步實作](../../src/documa/codegraph/store.py) · [查詢實作](../../src/documa/codegraph/service.py)

主要使用者是透過 Codex、Claude Code 或 CLI 探索程式庫的開發者。PR 審查人員需要知道受影響的 API 與測試，新進成員需要順著入口讀懂元件，維護者需要能重建索引並檢查證據覆蓋率。

三個參考專案於 2026-11-25 查閱公開原始說明。CodeGraph 的自動更新、聚焦讀取與多語言索引對應本規格的索引、查詢和 Rust 階段。Graphify 的 rationale 與文件引用對應跨來源證據。Understand Anything 的差異分析與導覽對應審查及上手流程。驗收採 Documa 自己的測試與基準。 [CodeGraph](https://github.com/colbymchenry/codegraph) · [Graphify](https://github.com/Graphify-Labs/graphify#what-it-does) · [Understand Anything](https://github.com/Egonex-AI/Understand-Anything#4-keep-learning)

| 優先級 | 使用者可驗收的能力 |
| --- | --- |
| P0 | 可靠增量更新、查詢前新鮮度、可選監看、`explore` 查詢和原文證據預算 |
| P1 | 程式碼對應規格及測試、Rust／Cargo／PyO3 追蹤，以 Documa 的 PDF、Office、LingXi 為驗證案例 |
| P2 | Git 變更影響報告、可定位的架構概覽與上手導覽 |

交付介面為既有 Python、CLI、MCP 和宿主聊天。圖形 UI、上傳預覽、圖面縮放與工作台版面屬其他需求。本階段的來源由明確本機路徑選定。先支援 Python 和 Rust，Markdown 與明確指定的本機文件用於證據連結。

### 假設與前提

- **已確認**。索引、排序、路徑解析、驗證、影響分析與固定模板輸出用純程式完成。回答、導覽解說、文件與 PR 摘要一律由宿主現有 LLM 生成。Documa 無須模型端點、額外金鑰、外部向量服務或模型套件。
- **已確認**。維持 Documa 第一階段無獨立 UI、現有 CLI／MCP 入口穩定、原文與正規化文字分存，以及 UTF-8 結構化輸出。JSON 序列化使用 `ensure_ascii=False`。ContextIR 1.0 和既有查詢意圖維持相容。
- **設計預設**。寫入由顯式同步或使用者啟動的 CLI 監看負責。Agent 查詢保持唯讀。以 `HEAD` 對工作樹作為變更報告的預設比較。`explore` 每次預設最多三個證據區塊，完整回應預設 2,000 tokens。數值於 Stage 0 用固定驗收集核對，調整須記錄原因。
- **待量測**。跨語言巨集與動態分派的可解析範圍、Windows/Linux 監看成本、代表性大型程式庫的延遲和記憶體。Stage 0 鎖定 fixtures 和環境後測量，分別報告覆蓋率與不確定性。

### 系統與宿主分工

來源經語言 adapter 或文件 adapter 產出索引。SQLite 保存可重建的索引與溯源欄位。Query service 先選入口與路徑，再讀取少量來源並驗證雜湊。宿主取得結構化結果後自行撰寫回答或文件。開放式提問可由宿主改寫後再查詢，語意聯想留在宿主答覆中，作為待查線索。已驗證關係仍由來源與解析規則決定。

既有 Python 呼叫者可沿用自行傳入的 `CodeSummaryEnricher`。本規格的 CLI、MCP 與宿主工作流均無須啟用它。舊索引中的增補摘要以 derived metadata 呈現，生成回答前仍須取證。

在支援 **Streaming** 的宿主對話中，宿主邊生成邊呈現回答與文件草稿。若宿主表面只提供完整文字，使用該宿主的最終回應。Documa MCP 回傳單份結構化工具結果，寫入文件時由宿主提交完整成品。

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 880 208" role="img" aria-labelledby="title">
  <title id="title">從本機來源到可驗證證據與宿主回答</title>
  <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8Z" fill="#365171"/></marker></defs>
  <rect width="880" height="208" fill="#f5f8fc"/>
  <g fill="#ffffff" stroke="#7389a1"><rect x="20" y="55" width="150" height="90" rx="10"/><rect x="235" y="55" width="170" height="90" rx="10"/><rect x="468" y="55" width="165" height="90" rx="10"/><rect x="695" y="55" width="165" height="90" rx="10"/></g>
  <g fill="none" stroke="#365171" stroke-width="2" marker-end="url(#arrow)"><path d="M175 100H225"/><path d="M410 100H458"/><path d="M638 100H685"/></g>
  <g font-family="sans-serif" text-anchor="middle" fill="#1c3353" font-size="17"><text x="95" y="89">本機來源</text><text x="320" y="89">索引與解析</text><text x="551" y="89">查詢與取證</text><text x="777" y="89">宿主生成</text></g>
  <g font-family="sans-serif" text-anchor="middle" fill="#365171" font-size="13"><text x="95" y="118">程式、文件、Git</text><text x="320" y="118">adapter、SQLite</text><text x="551" y="118">排序、路徑、雜湊</text><text x="777" y="118">回答、文件、摘要</text></g>
  <text x="440" y="179" font-family="sans-serif" text-anchor="middle" fill="#365171" font-size="15">原始檔與指定 Git 內容是可核對的來源</text>
</svg>
```

### 核心流程與資訊優先級

1. 維護者以明確 repository root 同步。解析來源、設定和 adapter 版本，提交新 generation。CLI 顯示更新、刪除、略過與未解析的數量。
2. 開發者在宿主詢問「Documa 如何選擇 PDF provider？」宿主使用 `explore`。Documa 檢查新鮮度，優先匹配符號、路徑與高區別度查詢詞，再擴張到有關聯的入口、呼叫點與目的地。
3. 服務驗證所選來源並在預算內回傳。若結果截斷，提供可定位的下一批候選。若來源過期，回傳可操作的更新提示。若查無資料，回報已搜尋範圍與解析限制。
4. 審查時可改用 Git 比較。需要說明或文件時，由宿主引述已查核的來源。無模型的 CLI 可直接輸出 JSON 或固定模板 Markdown。

| 任務優先級 | 資訊揭露 |
| --- | --- |
| 主要任務，找到可核對的答案 | 先呈現關鍵符號、來源與重要限制 |
| 次要任務，查變更風險與測試 | 先呈現變更摘要、受影響對象、候選測試的依據 |
| 低頻任務，讀架構導覽 | 先呈現入口及步驟，依需求再讀各段原文 |
| 維護任務，排查更新或解析問題 | 失敗時呈現錯誤碼、範圍與重試方式。細部計時、覆蓋率與候選關係由 CLI 筆錄或進階輸出查閱 |

### 專案目錄規劃

```text
src/documa/
  codegraph/        models.py、store.py、service.py、python_adapter.py、scip.py
                    新增獨立模組承接查詢排序、檔案監看、文件連結、Rust 與 Git 比較
  context/          既有跨來源證據 adapter 與來源契約
  cli.py            既有命令與管理操作
  interfaces/       tools.py、tool_schemas.py、mcp_server.py、tool_profiles.py
tests/              test_code_graph.py、test_context_graph.py、同目錄的擴充 fixtures
plugins/            現有各宿主 codegraph skill，補查詢與取證範例
docs/spec/          本規格與開發計畫
docs/documa/        使用者操作、維護與疑難排解
```

重用既有模型、讀取器和工具名稱。新增模組以 `snake_case` 命名，依實際能力與測試切分；Core 可以解析 adapter 回傳的通用紀錄。維護和查詢權限沿用現有 admin、advanced 和 agent profiles。

### 功能規則

| ID | 功能 | 實作與驗收規則 |
| --- | --- | --- |
| CG-01 | 同步及新鮮度 | 使用 `Path.resolve().as_uri()` 建立 SQLite 唯讀 URI。generation 納入排序後的來源摘要、解析設定及 adapter 版本，包含會影響符號解析的 roots、語言和 scope。解析只重跑新增、變更或受設定影響的檔案。用反向依賴找需重解析的邊，遇到無法局部確定的解析則安全重建並記錄原因。唯讀查詢核對授權 scope 內的檔案清單與摘要，偵測新增、刪除和修改。可重用已核對的檔案指紋作快速路徑，低可信或變動時讀檔驗證，取證一律重新驗雜湊。支援 `status`，以及綁定前景程序生命週期的可選監看。 |
| CG-02 | `explore` 查詢 | 精確節點 ID 或全名在前，其次為檔案路徑、識別字與查詢詞覆蓋，再以距離和關係補候選。同分依路徑、位置與 ID 固定排序。針對證明路徑選取定義與呼叫點，去除重複範圍，控制節點、跳數、區塊與整份回應大小。沿用 `lookup` 等既有行為。 |
| CG-03 | 文件與測試連結 | 預設只掃描授權 root 中可發現的 README／ADR／RFC／規格 Markdown，其他文件須明列本機來源並使用既有文件 adapter。相對檔案、符號連結與直接測試呼叫可建立有來源的關係。同名或文字比對只能形成 `POSSIBLE` 候選，預設不走訪。測試只回報候選及命中理由。文件回到 ContextIR 或 DocumentIR 的 block 與原始 locator。 |
| CG-04 | Rust／PyO3 | 先解析 Cargo workspace、crate 邊、Rust 定義、可唯一解析的呼叫與 PyO3 module/export 註冊。跨語言邊須具備 Python import 或 wrapper、Rust 註冊點與可唯一對應的名稱。每個 adapter 報告 discovered、parsed、skipped、unresolved。Tree-sitter 可透過既有 `documa[code]` 擴充，Rust 缺依賴時回報 capability。SCIP 支援沿用作後續擴充點。 |
| CG-05 | Git 比較 | 新增 `git_diff` 意圖，支援 `HEAD` 對 `worktree` 及任意兩個可解析的本機 commits，比較時保持唯讀。`worktree` 納入選定 scope 中 staged、unstaged 與非忽略的新增檔。以變更行對應前後版本符號，用 base 圖譜追蹤刪除，用 head 圖譜追蹤新增，兩側對照改名和修改。只重用版本與 scope 相符的索引。缺相符索引時，在隔離暫存區從本機 Git object 建立所需版本，以預算限制建置。若某側無法建圖，明列較低的分析覆蓋率。回傳受影響公開 API、關係路徑、文件與候選測試。既有 `diff` 保留最新同步差異語義。 |
| CG-06 | 概覽與導覽 | `tour` 按 workspace、package、crate 與公開入口產生有序步驟。每步附來源定位、查證狀態及下一步。JSON 和固定模板 Markdown 可由 CLI 直接產出。宿主生成的說明可引用步驟，變更後按 generation 更新。 |

### 資料模型、狀態與持久化

| 紀錄 | 識別與欄位 | 新增、更新與刪除 |
| --- | --- | --- |
| Workspace | `workspaceId`、canonical root、scope、`activeGeneration` | 首次同步建立。修改 scope 產生新 generation。管理命令可清除該 workspace 的衍生索引，來源檔由使用者管理。 |
| Generation | workspace + `generation`、來源摘要、設定指紋、版本與覆蓋計數 | 同步以 transaction 或 staging 原子提交。保留 active 與既有對上一版的差異資訊。暫存或中斷版本不得可查，過期暫存由清理程序釋放。 |
| File、Node、Occurrence | repo 相對路徑，既有 ID、語言、全名、span、來源摘要 | 新來源解析建立。修改時替換受影響紀錄。刪除來源時清理舊節點與其邊，改名以移除舊 locator 並新增新 locator 表示。 |
| Edge、EvidenceRef | 既有 edge ID、兩端節點、關係、resolution；來源 locator、span 或 block ID、hash、revision | 依來源和解析規則重建。任一端點移除或來源失效時撤下。`EXACT`／`RESOLVED` 可導航，`POSSIBLE` 需 opt-in，`UNRESOLVED` 回報缺口。 |
| Change report、Tour | 固定比較端點或 generation、範圍、證據引用及候選 | 每次請求產生，匯出由使用者指定路徑。再次查詢重新計算。讀取歷史引用時驗證 Git object 或目前來源摘要。 |

| 主要狀態 | 進入與恢復規則 |
| --- | --- |
| `missing` | 無索引。以明確根目錄同步後進入 `fresh`。 |
| `fresh` | active generation 與來源、設定吻合。可查證據。 |
| `stale` | 偵測到新、改、刪、設定不一致或證據雜湊變化。查詢回傳更新提示，顯式同步成功後恢復。 |
| `syncing` | 單 writer 建置候選 generation。完成則原子切換，失敗或取消則回滾；讀者依來源新鮮度判斷舊 generation 是否仍可用。 |
| `failed` | 回傳錯誤碼與重試建議。狀態查詢可顯示失敗紀錄，準備好後重試同步。 |

CLI 重啟後可用同一 workspace ID 或 root 恢復。查詢、候選集合及尚未輸出的文件草稿屬本次請求或宿主對話。SQLite 遷移失敗時，保留舊代衍生庫供舊版二進位讀取，以新路徑重建新版索引；舊索引清理限明確指定的管理操作。

### 介面與回應契約

| 入口 | 請求與權限 | 結果 |
| --- | --- | --- |
| `code-graph-sync`／`documa_sync_code_graph` | 沿用 root、include/exclude、source roots。新增選用 `languages`、`document_paths`。只允許管理入口寫入。 | `workspace_id`、generation、diff 計數、warnings、語言覆蓋率 |
| `code-graph-status`、`code-graph-watch`、`code-graph-clear` | 擬新增 CLI。狀態唯讀。監看顯式啟動並可終止，清理由管理者提供 workspace ID 和確認參數。 | freshness、當前 generation 或操作結果。清理只影響衍生索引。 |
| `code-graph-query`／`documa_query_code_graph`／`documa_code_context` | 沿用 `workspace_id`、`query`、`intent`、`symbols`、`targets`、`expected_generation`。三種新意圖允許同一入口，`git_diff` 可附 `base_ref`、`head_ref`。`explore` 新增獨立參數 `max_response_tokens` 控制整份回應，既有 `total_max_tokens` 維持證據原文預算語義。 | `generation`、`nodes`、`edges`、`proofPaths`、`uncertaintyReceipt`。新增意圖可回 `freshness`、`coverage`、`evidence`、`budgetReceipt`、`nextAction`。 |
| `code-graph-read`／`documa_read_code_evidence` | 既有節點 ID 保持相容，新增可辨識 callsite 和文件 block 的 evidence ID。附 `expected_generation` 及既有預算。 | 已驗證原文、locator、span 或 block ID、hash、revision、未讀清單 |

`explore` 的 hard cap 以實際 tokenizer 計算 documa 序列化後的完整回應物件，計入節點、證據、摘要及截斷回執。預算不足時先收斂證據與候選，仍不足則回傳精簡結構化錯誤。計數器缺失且要求 token hard cap 時回 `TOKEN_COUNTER_UNAVAILABLE`。同一 generation 的追讀以不可執行的結構化參數呈現，由宿主選擇呼叫；generation 變更時重新查詢。

新意圖在使用工作樹證據時預設檢查完整世代的新鮮度。現有意圖保留原本的 generation 與證據驗證方式，呼叫者可先用 `code-graph-status` 檢查整個 scope。Git commit 資料由不可變 OID 認定，工作樹側仍要查驗最新內容。

```json
{"workspace_id":"cw_example","query":"PDF parser provider fallback","intent":"explore","max_response_tokens":2000}
```

範例查詢應讓 `src/documa/adapters/registry.py` 的 provider 選擇證據進入前三筆。`locator` 使用 repository 相對路徑。每筆證據可附 `sourceSpan` 或文件 `blockId`，以及 `sourceHash`。`revision` 對工作樹證據使用 generation，對歷史證據使用已解析的 commit OID。

### 背景執行、錯誤與觀測

監看程序僅涵蓋授權 root。預設 500 ms debounce，檔案事件按路徑合併，只有一個同步 writer；同步期間收到的事件合併到下一批。事件溢出或來源狀態不明時重新核對整個 manifest。`Ctrl+C` 停止接收事件並取消未提交更新。暫時鎖競爭最多重試 3 次並退避；路徑、權限和解析設定錯誤回傳可操作訊息。正常資料輸出留在 stdout，背景進度與錯誤送 stderr 或現有 structured logger。

| 情境 | 回應與復原 |
| --- | --- |
| 缺索引、generation 不符、證據已改 | 沿用 `CODE_GRAPH_NOT_FOUND`、`CODE_GRAPH_STALE`、`CODE_GRAPH_SOURCE_STALE` 與既有證據雜湊錯誤。回傳 action，重新同步後重試查詢。 |
| 來源超出 scope、Git ref 無法解析或 adapter 不可用 | 回傳明確 scope/ref/capability 原因。可用來源的覆蓋率與缺口隨結果呈現。 |
| 檔案刪改、SQLite busy 或同步中止 | 重取乾淨快照，保持 transaction 一致，重試須以整次同步為單位。 |
| 查不到直接證據、回應截斷 | 明列查詢範圍、已讀證據、候選或 `nextAction`。宿主回答須揭露資訊缺口。 |

觀測欄位包括 stage duration、掃描／解析／刪除數、語言與解析覆蓋、失效原因、證據讀取數與完整回應 token 數。預設記錄 workspace ID 與計數，避免將來源片段、環境金鑰和使用者原始提問寫入日誌。Git subprocess 使用參數列與已驗證的 ref；工作樹路徑限制在授權 root。來源內容與工具輸出都視為待引用資料。文件內文字無法觸發命令、擴張 scope 或指定額外服務。

### G3M 與非功能驗收

以「查到正確入口、取到可核對證據、維持低工具成本」為衡量方向。Stage 0 建立至少 12 題固定集，涵蓋精確符號、自由文字、依賴與跨來源查詢；每題記錄預期 locator、span 或節點。Stage 7 對照舊 lookup 與 `rg`＋原文讀取，報告 top-3 命中、支持證據覆蓋、工具次數、回應 tokens、冷建與單檔更新耗時。第一輪數值是建議門檻，Stage 0 以實測和環境記錄核對後列為正式 gate。

| 驗收 | 操作與預期 |
| --- | --- |
| AC-01｜更新正確 | Windows 測 `#`、空白和 Unicode 路徑；修改 `source_roots` 必換 generation。新增、刪除、改名、切分支及匯入目標改動後，增量與乾淨重建的有效節點、邊及解析狀態一致。未變動同步解析 0 檔。 |
| AC-02｜入口與證據 | PDF provider 問題的正確入口在前三名，精確符號 fixtures 全數優先。固定集 top-3 命中至少 80%，並提供 baseline；budget、重試及過期回應有回歸測試。整份成功回應符合設定的 hard cap。 |
| AC-03｜跨來源準確 | fixture 規格中的明確連結與直接測試呼叫有對應源證據。名稱碰撞只列候選。文件內容變更後，舊引用驗證失敗。 |
| AC-04｜Rust 覆蓋 | PDF、Office、LingXi 各驗證一條實際可靜態核對的 Python → Rust binding 路徑，提供兩側原文與覆蓋率。缺 grammar 或宏無法解析時回報覆蓋缺口。 |
| AC-05｜Git 歷史引用 | 以兩個本機 commits 與工作樹比較驗證新增、刪除、改名、修改。已刪除符號可從基準 Git object 引用，測試候選附路徑。既有 `diff` fixtures 通過。 |
| AC-06｜導覽 | PDF provider 固定模板輸出具入口、順序、至少一處分支或邊界及可點讀證據。相同輸入和 generation 的結構化輸出可重現。 |
| AC-07｜無額外模型服務 | 無模型金鑰且封鎖索引程序外部連線時，索引、查詢與固定模板輸出通過。Codex／Claude Code 以宿主既有模型完成帶引用的回答或文件，支援的宿主表面保留 Streaming。 |
| AC-08｜相容與復原 | Windows/Linux 跑既有 codegraph、ContextIR、CLI/MCP/schema 回歸。測單 writer、取消、程序重啟、缺 tokenizer、scope 逃逸、Git ref／檔名歧義與文件文字注入。舊 workspace 可重開，舊 generation 不符時不得交付為目前原文。 |

最小 BDD 案例。Given 已同步且 `src/documa/adapters/registry.py` 含已知 provider 選擇邏輯，When 查詢前該檔遭變更，Then 回傳過期提示與同步動作。完成同步後，查詢只引用可核對的新 span 與 hash。

主要風險包括大型程式庫的發現成本、監看掉事件、間接 PyO3 註冊、分支切換期間的讀寫競爭和文件同名連結。對策為固定基線、manifest 複核、解析覆蓋率、原子切換與候選標示。唯讀查詢及歷史 Git 比較避免改寫工作樹。未完成的宿主實測或外部平台驗證須按實際狀態登錄。
