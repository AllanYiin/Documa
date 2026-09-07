# Documa 0.9.0 安裝與交付

先選 runtime 的作業系統／CPU／Python ABI，再選宿主插件。插件不內嵌 Python runtime；四份 wrapper 都使用同一個 Documa 0.9.0。

## Runtime

| 目錄／bundle | 目標 |
| --- | --- |
| `runtime/windows-x86_64` | Windows x64，CPython 3.10 |
| `runtime/linux-x86_64` | Linux x86_64，CPython 3.10／3.11／3.12，glibc 2.28+ |
| `runtime/linux-arm64` | Linux ARM64 / aarch64，CPython 3.10／3.11／3.12，glibc 2.28+ |
| `source/documa-0.9.0.tar.gz` | 其他環境自行編譯；需要 Rust 1.88+、Python headers 與平台 linker，未等同預建驗證 |

同架構目錄中的三個 Linux wheels 對應不同 Python ABI，由 pip 選取；不要安裝其他架構或重新命名 wheel。ARM64 安裝測試透過 QEMU 執行，並未宣稱實體 ARM 裝置驗證。Alpine/musl、macOS 與 Windows ARM64 沒有本次預建 wheel。

以 Linux ARM64 為例，在 bundle 解壓縮根目錄執行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --find-links runtime/linux-arm64 'documa==0.9.0'
python -m documa.cli doctor --no-benchmark
```

Windows 請選 `runtime/windows-x86_64`，以 CPython 3.10 執行 pip。需要套件索引下載一般 Python 相依套件；三個 Rust 模組與 LingXi 模型已內建，不需另裝 LingXi。OCR 是選用功能，未納入本次非 OCR 驗證。

既有 Documa 的升級必須使用 guarded installer，先處理 MCP lifecycle。以下以 ARM64 CPython 3.10 為例；其他版本請指定實際交付的相符 wheel 檔名：

```bash
python -m documa.install --upgrade ./runtime/linux-arm64/documa-0.9.0-cp310-cp310-manylinux_2_28_aarch64.whl
```

宿主的 `python` 命令必須指向安裝 0.9.0 的環境；可在宿主設定選定對應 Python。

## 宿主插件

| 宿主 | 交付位置 | 格式 |
| --- | --- | --- |
| Codex | `plugins/codex/codex-documa-0.9.0.zip` | 根目錄含 `.codex-plugin/plugin.json` 與 `.mcp.json` |
| Claude Code | `plugins/claude-code/claude-code-documa-0.9.0.zip` | 根目錄含 `.claude-plugin/plugin.json` 與 `.mcp.json` |
| Hermes | `plugins/hermes/hermes-documa-0.9.0.zip` | Portable Agent Plugins v1，`plugin.json`、`mcp.json`、skills |
| OpenClaw | `plugins/openclaw/documa-openclaw-documa-0.9.0.tgz` | 原生 npm 插件，可執行的 `index.js` CLI wrapper |

依各包 README 的宿主安裝流程載入；只選用需要的宿主，不必全裝。此次已做結構、內容、安全靜態檢查和共用 runtime MCP 驗證；四宿主實際安裝／reload 另列為未執行。沒有替使用者發布或更新既有宿主設定。

## 校驗與回滾

`SHA256SUMS.txt` 記錄實際相對路徑與 SHA-256；Linux 可執行 `sha256sum -c SHA256SUMS.txt`。每個 platform bundle 都有自己的校驗清單，`release-report.json` 記錄驗證範圍與原生 binary hash。

保留舊環境／0.8.0 套件；回滾使用 `python -m documa.install` 指向已知良好的舊 wheel。原始 PDF 不受影響，Markdown／block tree／搜尋索引是可重建衍生資料，切換版本後應重新產生。既有 PDF 編碼、素材與部分版型品質限制仍存在。

Linux 建置採 PyPA 官方 manylinux 映像；CI 分開使用原生 x86_64 與 ARM64 runner。[PyPA 跨架構說明](https://cibuildwheel.pypa.io/en/stable/faq/)、[GitHub runner 規格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。本機實測範圍以 release report 為準。
