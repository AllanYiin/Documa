# Claude Code Documa Plugin

<p align="center">
  <img src="assets/documa-logo.png" alt="Documa logo" width="320">
</p>

這個 plugin 透過 plugin-provided MCP server 與文件／repository evidence workflow skills，把 Documa 暴露給 Claude Code。它不打包 Documa 本體；請先在 Claude Code 可見的 Python 環境安裝 Documa。

```powershell
# 首次安裝
python -m pip install "documa==0.7.0"

# 升級／重裝（會先偵測並斷開 MCP）
python -m documa.install --upgrade "documa==0.7.0"
claude --plugin-dir .\plugins\claude-code-documa
```

在 Claude Code 內用以下指令確認 MCP server：

```text
/mcp
```

Plugin-bundled MCP tools 在 Claude Code 內會帶 plugin scope。若需要在 permission rules、skill `allowed-tools` 或 hook matcher 中指定完整名稱，格式會類似：

```text
mcp__plugin_claude-code-documa_documa__documa_process
```

Expected workflow / 預期流程：

1. Use `documa_process` to turn a source document into Documa IR and block outputs.
2. Use `documa_search_blocks` or `documa_list_blocks` to find likely evidence.
3. Use `documa_read_block` for only the selected block bodies.
4. Cite block ids, page/source metadata, and evidence boundaries in the answer.

Repository workflow / 程式碼圖譜流程：先以 `documa code-graph-sync <root>` 建立本機 index，再依 MCP profile 使用 `documa_code_context`，或分開呼叫 `documa_query_code_graph` 與 `documa_read_code_evidence`。只把回傳的 proof path 與通過 hash 驗證的 source blocks 當成證據，並保留 uncertainty receipt。

本地驗證：

```powershell
python scripts\validate_agent_plugins.py
claude plugin validate .\plugins\claude-code-documa --strict
```
