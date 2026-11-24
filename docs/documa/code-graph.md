# Documa 程式庫圖譜

Documa 在本機建立可重建的 SQLite 索引。它會找出符號、呼叫路徑、規格段落和候選測試，並在回傳前核對來源雜湊。Codex 或 Claude Code 使用宿主現有的模型回答、解說或撰寫文件。有支援的宿主介面會逐步顯示生成文字。Documa 的索引、查詢和固定模板輸出無須模型金鑰。

[技術規格](../spec/codegraph-technical.md) · [開發驗收紀錄](../spec/codegraph-development-plan.md)

## 從目前原始碼執行

在 Documa repo 根目錄使用已具備 Documa runtime 相依套件的 Python 環境。PowerShell 可指定原始碼路徑，避免已安裝舊版覆蓋本次功能。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
$index = python -m documa.cli code-graph-sync . --source-root src | ConvertFrom-Json
$workspace = $index.workspace_id
python -m documa.cli code-graph-status $workspace
```

預設索引 Python 及選定的 README、ADR、RFC 和規格 Markdown。可多次指定 `--document-path` 納入授權 repo 中的其他本機文件，例如存檔的 DocumentIR。

```powershell
python -m documa.cli code-graph-sync . --source-root src --document-path plugins/codex-documa/skills/documa-codegraph/SKILL.md
```

需要 Rust 時，先在專案環境安裝 `documa[code]`，並明確選擇兩種語言。`--language rust` 單獨使用時，索引 Rust、Cargo 與文件。跨語言追蹤需要同時索引 Python。

```powershell
$index = python -m documa.cli code-graph-sync . --source-root src --language python --language rust | ConvertFrom-Json
$workspace = $index.workspace_id
```

`coverage` 依語言呈現找出的檔案、已解析、略過及未解析的數量。Tree-sitter 查到的語法錯誤與 Rust 動態參照保留在覆蓋資訊中。Documa 用 Cargo 宣告與靜態 PyO3 註冊找到可驗證的綁定。條件編譯、巨集展開與執行期分派可能留下證據缺口。

## 找入口、查版本與取證

初次使用 `explore` 時，先準備本機 `o200k_base` 詞彙。安裝或準備環境時執行下列指令會載入公開 tokenizer 檔，這個步驟可在有網路的環境預先完成。離線部署可透過 `TIKTOKEN_CACHE_DIR` 指定已準備好的位置。查詢時，Documa 使用已載入或通過雜湊檢查的本機詞彙。

```powershell
python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
python -m documa.cli code-graph-query $workspace "PDF parser provider fallback" --intent explore --max-response-tokens 2000
```

`explore` 優先匹配精確符號、路徑和查詢詞，再沿圖查找關係。完整 JSON 工具回應預設上限為 2,000 tokens。`evidence` 預設最多三筆，原文片段包含來源位置與雜湊。`nextAction` 提供讀取完整證據的參數。`POSSIBLE` 邊須使用 `--include-possible` 選用，引用時應標示為候選。

Git 比較可用本機 refs 或同步後的工作樹。`beforeImpact` 和 `afterImpact` 各自對應所在版本。若歷史索引超過版本快照預算，`coverage` 會標明可分析範圍。`diff` 意圖仍代表最近一次同步的索引差異。

```powershell
python -m documa.cli code-graph-query $workspace --intent git_diff --base-ref "HEAD~1" --head-ref HEAD
python -m documa.cli code-graph-query $workspace --intent git_diff --base-ref HEAD --head-ref worktree
```

用報告中的歷史 `blockId` 續讀已刪除符號，讀取時會核對指定 commit、blob 和來源雜湊。

```powershell
$report = python -m documa.cli code-graph-query $workspace --intent git_diff --base-ref "HEAD~1" --head-ref HEAD | ConvertFrom-Json
$history = $report.changes | ForEach-Object { $_.beforeEvidence } | Where-Object { $_.blockId -like 'git-proof:*' } | Select-Object -First 1
if ($history) { python -m documa.cli code-graph-read $workspace --block-id $history.blockId }
```

需要可重跑的 Markdown 導覽時，在 repo 根目錄輸出固定模板。也可以將同一份證據交由宿主撰寫適合新成員的版本。

```powershell
python -m documa.cli code-graph-query $workspace --intent tour --symbol documa.adapters.registry.adapter_for_source --format markdown
```

## 從宿主回答或寫文件

同步所選 root 後，把 workspace ID 交給宿主。預設 `agent` profile 透過 `documa_code_context` 同次查詢方向、證據和缺口。管理 profile 啟用 `documa_sync_code_graph`，進階 profile 可直接使用 `documa_query_code_graph` 和 `documa_read_code_evidence`。

> 使用 `documa_code_context` 的 `tour` 意圖，從 `documa.adapters.registry.adapter_for_source` 開始。請用目前對話的模型寫一份新進開發者可讀的導覽，每一段附原文位置。遇到推測或無法核對的關係時，列在說明裡。

宿主應先取證，才能引用原文。可接在 prompt 後指定回應所需檔案、字數或受眾。宿主生成的文字僅供理解，圖譜關係與來源驗證由 Documa 確定。

## 更新、復原與回滾

1. `CODE_GRAPH_STALE` 或來源過期時，確認查詢仍指向原 workspace，按同一組 roots、languages 和文件路徑重新同步。
2. `TOKEN_COUNTER_UNAVAILABLE` 時，準備受支援的本機詞彙。只需固定模板時，可執行 `tour` 的 CLI Markdown 輸出。
3. 可用 `code-graph-watch` 在前景維持索引，使用 `Ctrl+C` 結束。操作用狀態與索引更新於前景程式顯示。
4. 保留舊版可開的衍生庫。切回舊版時用獨立 `--store-dir` 重新同步。可用 `code-graph-clear <workspace-id> --confirm` 清理指定 workspace 的衍生索引紀錄，之後再次同步可重建。

```powershell
python -m documa.cli code-graph-watch . --source-root src --language python --language rust
```

本交付使用工作區原始碼與本機插件 ZIP。需要別的執行環境時，讓插件連到包含此實作的 Documa runtime。
