# Codex Documa Plugin

這個 plugin 透過 bundled MCP server config 與 reusable evidence workflow skill，把 Documa 暴露給 Codex。它不打包 Documa 本體；請先在 Codex 可見的 Python 環境安裝 Documa。

```powershell
python -m pip install -e ".[documents,mcp]"
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
  "documa_process",
  "documa_search_blocks",
  "documa_list_blocks",
  "documa_read_block",
  "documa_doctor"
]
```

Expected workflow / 預期流程：

1. Use `documa_process` to turn a source document into Documa IR and block outputs.
2. Use `documa_search_blocks` or `documa_list_blocks` to find likely evidence.
3. Use `documa_read_block` for only the selected block bodies.
4. Cite block ids, page/source metadata, and evidence boundaries in the answer.

本地驗證：

```powershell
python scripts\validate_agent_plugins.py
```
