# Documa 0.8.0 本機交付

此交付包含 Windows x64、CPython 3.10 專用 wheel，以及 Codex、Claude Code、Hermes Agent、OpenClaw 四種既有插件。未發布至 PyPI／npm／宿主 registry，也未修改本機全域安裝或宿主設定。

## 安裝 Python runtime

解開 bundle 後，在宿主實際使用的 Python 3.10 x64 環境執行：

```powershell
python -m pip install .\documa-0.8.0-cp310-cp310-win_amd64.whl
documa doctor --no-benchmark
```

已裝舊版 Documa 時，優先使用既有升級入口以關閉使用該 runtime 的 MCP：

```powershell
python -m documa.install --upgrade .\documa-0.8.0-cp310-cp310-win_amd64.whl
```

若舊版沒有 `documa.install`，先手動停止使用該 Python 環境的 Documa MCP，再執行 pip 安裝。

wheel 已內附 rust-Lingxi **0.4.5** 原生模組與三個必要模型，不需要另裝或下載 `lingxi`。它使用 `documa._vendor.lingxi` 私有 namespace，不覆蓋獨立安裝的 Lingxi；亦不使用全域 `LINGXI_ASSETS` 指定的其他模型。wheel 同時內建 Rust PDF 0.2.0 與 Office 0.1.0。

此 bundle 不是完整離線 Python wheelhouse：其他一般 Python 依賴仍由 pip 解析，離線機器需事先準備這些依賴。Python 3.11+、Linux、macOS 不可安裝此 `cp310-win_amd64` wheel；請使用相符平台的 wheel，或以 Python 3.10+、Rust 1.88+ 和 linker 從另附的 sdist 編譯。

## 插件

| 產物 | 用途 |
| --- | --- |
| `codex-documa-0.8.0.zip` | 解開後，依 Codex local plugin flow 載入具有 `.codex-plugin/plugin.json` 的根目錄。 |
| `claude-code-documa-0.8.0.zip` | 解開後，依 Claude Code 的 local plugin 流程載入具有 `.claude-plugin/plugin.json` 的根目錄。 |
| `hermes-documa-0.8.0.zip` | Portable Agent Plugins v1，根目錄為 `plugin.json`、`mcp.json` 與 skills。 |
| `documa-openclaw-documa-0.8.0.tgz` | OpenClaw native npm package；依宿主套件安裝流程載入。 |

每個插件保留自己的 README、manifest、skills 與既有能力邊界。前三者啟動 `python -m documa.interfaces.mcp_server`；OpenClaw 呼叫 `documa` CLI，並非完整 MCP tool 集的同義版本。請確認宿主 `PATH` 指到剛安裝 wheel 的環境；若 OpenClaw 在 WSL/Linux 內執行，必須在該環境建置／安裝相符的 Documa，而非使用 Windows wheel。

本次只封裝，未自動執行任何宿主安裝、enable、restart 或 consent；這些步驟由使用者依宿主流程操作。ZIP 是 wrapper，不各自重複放入大型 Python runtime；同 bundle 的 wheel 已自帶 Lingxi。

## 驗證與復原

`SHA256SUMS.txt` 可用 `Get-FileHash -Algorithm SHA256` 比對；`release-report.json` 記錄本機驗證與未執行項目。在 repository 中可重跑：

```powershell
python native/lingxi/verify.py
python scripts/validate_agent_plugins.py
python scripts/package_plugins.py --check
python scripts/verify_release_artifacts.py dist/documa-0.8.0-cp310-cp310-win_amd64.whl dist/documa-0.8.0.tar.gz
# 以下須以安裝新 wheel、未另裝 lingxi 的隔離環境執行：
python -I -X utf8 scripts/smoke_installed_release.py
```

保留原有 Python 環境與插件檔作為復原點；若要回退，先停止使用中的 MCP，再在原環境安裝先前保留的 wheel、恢復先前插件。這次升版沒有修改 DocumentIR schema，也沒有自動遷移、刪除或重建使用者 store。現存 native PDF 品質 gate 限制並未因這次 Lingxi 升版而解決。
