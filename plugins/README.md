# Documa Agent Plugins

這個目錄放 host-specific plugin wrappers。它們把 Documa 當成第三方 package 使用，並且刻意留在 `src/` 外面，避免 Documa core 變成某個 agent host 專用的實作。

所有 wrapper 都假設 host 執行環境已經能找到 Documa：

```powershell
python -m pip install -e ".[documents,mcp]"
```

共用整合契約：

1. 支援 MCP 的 host 一律優先使用 `documa-mcp`。
2. 只有 host-native runtime 需要直接註冊 tool 時，才包 `documa` CLI。
3. 回答流程維持 evidence-driven：先 process 文件，再 search/list blocks，最後只 read 選中的 blocks。
4. 不依賴 parser-native objects，也不繞過 Documa IR。

## Plugin Layouts

| Directory | Host | Integration style |
| --- | --- | --- |
| `claude-code-documa/` | Claude Code | `.claude-plugin` package with plugin-provided MCP server |
| `codex-documa/` | Codex | `.codex-plugin` package with plugin-provided MCP server |
| `openclaw-documa/` | OpenClaw | Native OpenClaw tool plugin wrapping the `documa` CLI |

## Host-specific MCP config

Codex 與 Claude Code 的 plugin manifest 都用 `mcpServers` 指向 MCP config；`.mcp.json` 也都使用 top-level `mcpServers` map。兩邊仍維持各自 wrapper 目錄，避免 host-specific metadata 互相耦合：

| Host | Manifest field | `.mcp.json` shape |
| --- | --- | --- |
| Codex | `"mcpServers": "./.mcp.json"` | wrapped `mcpServers` map，例如 `{ "mcpServers": { "documa": { "command": "documa-mcp" } } }` |
| Claude Code | `"mcpServers": "./.mcp.json"` | wrapped `mcpServers` map，例如 `{ "mcpServers": { "documa": { "command": "documa-mcp" } } }` |

不要把兩邊 `.mcp.json` 合併成同一份；wrapper 應維持 host-specific。

## Minimum Smoke Checks

```powershell
documa doctor
python scripts\validate_agent_plugins.py
```

For OpenClaw plugin source sanity:

```powershell
node --check .\plugins\openclaw-documa\index.js
```
