# Documa Agent Plugins

<p align="center">
  <img src="../assets/documa-logo.png" alt="Documa logo" width="320">
</p>

這個目錄放 host-specific plugin wrappers。它們把 Documa 當成第三方 package 使用，並且刻意留在 `src/` 外面，避免 Documa core 變成某個 agent host 專用的實作。

所有 wrapper 都假設 host 執行環境已經能找到 Documa：

```powershell
# 首次安裝
python -m pip install "documa==0.6.4"

# 已安裝 Documa 時的升級／重裝：先斷開 MCP，再執行 pip
python -m documa.install --upgrade "documa==0.6.4"
```

共用整合契約：

1. 支援 MCP 的 host 一律使用 `python -m documa.interfaces.mcp_server`，避免 Windows 長駐行程鎖住 pip 管理的 `documa-mcp.exe`。
2. 只有 host-native runtime 需要直接註冊 tool 時，才包 `documa` CLI。
3. 回答流程維持 evidence-driven：單文件先 process，再 search/list blocks，最後只 read 選中的 blocks；多文件先逐檔 ingest（集合索引增量維護），廣度問題用 `search_collection --group-by-document`，再以 `document_ids` 收斂並沿 `read_ref` 讀取。
4. 每個 wrapper 都必須同時暴露單文件與多文件（collection）兩條查詢路徑，並遵循搜尋回應內建的 `recommended_next`／`hints` 引導。
5. 不依賴 parser-native objects，也不繞過 Documa IR。

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
| Codex | `"mcpServers": "./.mcp.json"` | wrapped `mcpServers` map，command 為 `python`，args 為 `["-m", "documa.interfaces.mcp_server"]` |
| Claude Code | `"mcpServers": "./.mcp.json"` | wrapped `mcpServers` map，command 為 `python`，args 為 `["-m", "documa.interfaces.mcp_server"]` |

不要把兩邊 `.mcp.json` 合併成同一份；wrapper 應維持 host-specific。

## Packaging

`plugins/claude-code-documa.zip` 與 `plugins/codex-documa.zip` 是發佈產物，一律用打包腳本重生（確定性輸出：固定時間戳、排序條目），不要手動壓縮：

```powershell
python scripts\package_plugins.py          # 重生 zip
python scripts\package_plugins.py --check  # CI 強制：zip 與 plugin 目錄不同步即失敗
```

改動任何受管理的 plugin 目錄後必須重跑打包腳本並一併 commit 對應 zip，否則 CI 會擋下。

## Minimum Smoke Checks

```powershell
documa doctor
python scripts\validate_agent_plugins.py
python scripts\package_plugins.py --check
```

For OpenClaw plugin source sanity:

```powershell
node --check .\plugins\openclaw-documa\index.js
```
