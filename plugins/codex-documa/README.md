# Codex Documa Plugin

<p align="center">
  <img src="assets/documa-logo.png" alt="Documa logo" width="320">
</p>

這個 plugin 透過 bundled MCP server config、evidence workflows 與精簡 skill-loader bootstrap，把 Documa 暴露給 Codex。它不打包 Documa 本體；請先在 Codex 可見的 Python 環境安裝 Documa。

Plugin 內含四個邊界清楚的 skills：`documa-skill-loader` 是唯一常駐的 managed-skill 路由層，`documa-evidence` 負責日常文件問答，`documa-codegraph` 負責程式碼、相依、呼叫與影響取證，`documa-maintenance` 負責 doctor、index repair、benchmark、migration 與 release gates。MCP server 可用 `DOCUMA_MCP_PROFILE=agent|advanced|admin` 控制工具發現面；這是 Documa server policy，不是 MCP 標準 capability。


```powershell
# 首次安裝
# 本次交付先使用隨附的 Windows CPython 3.10 x64 wheel：
python -m pip install .\documa-0.8.0-cp310-cp310-win_amd64.whl
# 僅當目標 package index 已發布此版本時使用：
python -m pip install "documa==0.8.0"

# 升級／重裝（會先偵測並斷開 MCP）
python -m documa.install --upgrade "documa==0.8.0"
```

使用 Codex local plugin flow 載入 `plugins/codex-documa`。啟用後，在 Codex 內用以下指令確認 MCP server：

```text
/mcp
```

建議先把 plugin-scoped MCP server 設成 prompt approval，再視團隊需求縮小可用 tools。例如：

```toml
[plugins."codex-documa".mcp_servers.documa]
enabled = true
default_tools_approval_mode = "prompt"
enabled_tools = [
  # single-document reading loop
  "documa_process",
  "documa_search_blocks",
  "documa_list_blocks",
  "documa_block_tree",
  "documa_block_xref",
  "documa_inspect_block",
  "documa_read_block",
  # multi-document collection loop
  "documa_ingest",
  "documa_list_documents",
  "documa_index_collection",
  "documa_search_collection",
  # evidence close-out
  "documa_cite_block",
  "documa_render_citation",
  "documa_source_window",
  "documa_verify_citations",
  "documa_doctor",
  # dynamic skill loading (agent profile)
  "documa_load_skill",
  "documa_read_skill_resource",
  "documa_code_context"
]
```

### 設定 managed skill roots

Managed roots 必須放在 Codex 原生 skill 掃描路徑之外，避免同一份 skill 同時被原生 loader 與 Documa 載入。設定與首次編譯使用 admin profile：

```powershell
documa skills root-add managed D:\agent-skills --priority 10
documa skills sync
documa skills status
```

若要明確接管既有 native skill library，可在 `root-add` 加上 `--allow-native-scan-overlap`；這是逐 root 的顯式授權，預設仍拒絕重疊。

啟動 MCP 時若已有 `.documa/skills/config.json`，會先增量同步一次；之後 load 最多每 60 秒做一次 stale check，或以 `refresh=true` 強制同步。自然語句由本機 lexical metadata 與 feature-hash HNSW 路由；明確名稱可直接命中。可選的離線 enrichment 只增加 derived synonyms/triggers/tags，runtime 仍然不呼叫 LLM。

Expected workflow / 預期流程：

1. Single document: `documa_process` → `documa_search_blocks`/`documa_list_blocks`/`documa_block_tree` → `documa_read_block` for only the selected block bodies.
2. Multiple documents: `documa_ingest` per file (the collection index updates incrementally) → breadth via `documa_search_collection --group-by-document` → narrow with `document_ids` + `per_document_limit` → chain `read_ref` into `documa_read_block`.
3. Follow each search response's `recommended_next` and `hints`; bound output with `max_chars`/`max_tokens`/`max_response_tokens`.
4. Close out with `documa_cite_block`/`documa_verify_citations`, citing block ids, page/source metadata, and evidence boundaries.
5. Managed skills: `documa_load_skill` → follow `rendered_skill_md` → call `documa_read_skill_resource` only for a returned `next_actions` reference.
6. Repository graph: `documa code-graph-sync <root>` once, then `documa_code_context` with an exact symbol and one intent; report proof paths only with the returned hash-verified source blocks and uncertainty receipt.

本地驗證：

```powershell
python scripts\validate_agent_plugins.py
```
