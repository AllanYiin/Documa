# Hermes Agent Documa Plugin

<p align="center">
  <img src="assets/documa-logo.png" alt="Documa logo" width="320">
</p>

這是供 Hermes Agent 使用的 Portable Agent Plugins v1 套件。它透過 `mcp.json` 啟動 Documa stdio MCP server，並提供文件證據、repository graph、維護與動態 skill loader 四個 Agent Skills。套件不包含 Documa runtime；請先在 Hermes 可見的 Python 環境安裝相同版本。

```powershell
# 首次安裝 runtime
python -m pip install "documa==0.7.0"

# 升級／重裝 runtime（會先偵測並斷開既有 MCP）
python -m documa.install --upgrade "documa==0.7.0"

# 本地開發安裝；Hermes 對本地目錄要求 file:// URL
hermes plugins install file:///absolute/path/to/Documa/plugins/hermes-documa --enable
```

重新啟動 Hermes 後，用 `hermes plugins list` 確認 `documa` 已啟用，再從 `/tools` 或一般對話確認 Documa MCP tools 已載入。Portable plugin 的 MCP tools 會被 Hermes 加上 plugin namespace；skills 以底層名稱（例如 `documa_process`）描述流程，實際呼叫時使用 Hermes 已註冊、同名結尾的 MCP tool。

預期工作流：

1. 文件：`documa_process` → `documa_search_blocks`／`documa_list_blocks` → `documa_read_block` → citation verification。
2. 文件集合：逐檔 `documa_ingest` → `documa_search_collection` → 依 `(document_id, block_id)` 讀取證據。
3. 程式碼圖譜：先 `documa code-graph-sync <root>`，再以 `documa_code_context` 查詢並只採用 hash-verified source evidence。
4. Managed skills：`documa_load_skill` → 依 `rendered_skill_md` 執行 → 僅按 `next_actions` 讀取必要 supporting resource。

本地驗證：

```powershell
python scripts\validate_agent_plugins.py
python scripts\package_plugins.py --check
```

Hermes 對 Portable Agent Plugins v1 的 MCP 管理介面仍在快速演進；以 `hermes plugins list`、啟動紀錄與實際 tool discovery 作為目前 smoke evidence，不以 `hermes mcp list` 是否顯示 portable server 作為唯一判定。
