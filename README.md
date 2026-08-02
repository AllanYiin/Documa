# Documa

<p align="center">
  <img src="documa_logo.png" alt="Documa logo" width="320">
</p>

Documa 是給 agent 用的 **document evidence runtime**：讓 agent 用一小部分的 token 讀懂大文件、回答時指得出出處，而且引用可以被機器驗證。

如果你正在評估「有沒有比把整份文件塞進 context 更省、比 grep 原始文字更準」的文件讀取機制，這份 README 就是為你寫的。

## 問題：agent 讀文件的三種常見方式，各有一筆 token 帳

以一份 69 頁的 PDF（Basel III 流動性架構，全文 **49,570 tokens**）為例：

| 方式 | Token 成本（同一文件、10 題查詢實測） | 痛點 |
| --- | --- | --- |
| 整份塞進 context | 49,570（每輪對話都攤提） | 貴；長文件超過 context；模型在中段迷路（lost in the middle） |
| grep / 逐行搜尋原始文字 | 每題路徑（grep 全部命中行＋前 3 命中點各擴窗 60 行）**中位數 8,593，範圍 0–15,323**——散文的「一行」是整段，一次 grep 動輒 8k+ | 沒有結構、沒有頁碼出處；跨頁表格、雙欄版面在文字流裡直接斷裂；CJK 無詞界，命中全憑子字串運氣 |
| 向量 RAG | 檢索本身便宜 | 需要 embedding 前置成本與基礎設施；chunk 邊界破壞表格與章節；引用難以回溯到頁面座標 |
| **Documa 漸進式區塊讀取** | 每題完整路徑（搜尋 → 照發 `recommended_next` → 有界讀取 top hit → 產生引用）**中位數 2,035，範圍 97–2,703（約全文的 4%）**，且答案帶頁碼級引用 | 前置一次 `documa process`（確定性、離線、無 ML 依賴）；總覽型問題另需大綱 2,813 tokens |

> 量測方式：同一份 69 頁 PDF，10 題涵蓋事實、數值、定義、中文詞、英文縮寫的查詢，o200k tokenizer。grep 對象是匯出的 Markdown（1,988 行），模擬 coding agent 的典型路徑：`grep -n` 全部命中行（含 `檔名:行號:` 前綴）＋對前 3 個命中點各讀一個 60 行視窗，複合詞逐詞退避。Documa 是 v0.5.0 工具實際呼叫序列的 compact JSON 回應加總，第二步完全照搜尋回應的 `recommended_next.actions[]` 執行、未人工挑選。有一題兩條路徑都零命中：grep 零輸出，Documa 花 97 tokens 回報零結果並附改寫提示。中位數比 4.2×；逐題數據可用 `python benchmarks/token_economy/compare_grep_vs_documa.py --ir <documa.ir.json> --markdown <documa.md>` 對任何文件重現。

Documa 的核心主張：**讓 LLM 的每一次工具呼叫都只換回「做下一步決策所需」的最小資訊**，並在收尾時給出可驗證的頁碼級引用。

## 機制：四步漸進式揭露

```
process/ingest ──► block tree（大綱+梗概）──► search（排序過的候選）──► read（有界讀取）──► cite/verify
     一次性              ~2.8k tokens              ~1.2k tokens            每塊數百 tokens        出處可機器驗證
```

1. **Ingest** — `documa ingest report.pdf` 把文件處理成 parser-neutral 的 IR 與 block tree，拿到穩定的 `document_id`（同內容自動去重）。支援 PDF、Word、PowerPoint、HTML、email、notebook、Markdown。
2. **Overview** — `documa_block_tree` 回傳章節大綱；`include_sketches=true` 直接附上 ingest 時算好的每節梗概（sketch）與讀取成本，總覽類問題常常**零讀取**就能回答。
3. **Search + Read** — `documa_search_blocks` 先以 exact lexical section route 收斂範圍，coverage 不足時才用不呼叫模型的 local feature-hash HNSW 作 ANN section routing，再以 BM25-lite + coverage/proximity/intent 排序（TOC 與頁眉頁腳自動降權、近似重複去除）。每個 hit 附帶結構路徑、頁碼、建議讀取量與**可直接照發的下一步工具呼叫**（`recommended_next.actions[]`）；`documa_read_block` 以 `max_chars`/`max_tokens` 有界讀取，讀不完給續讀 cursor。
4. **Cite + Verify** — `documa_cite_block` 回溯到「第幾頁、頁面上哪個 bbox」，`documa_verify_citations` 機器檢查引用的區塊真實存在——答案不只便宜，還可稽核。

跨文件同理：`documa_search_collection`（SQLite FTS5，含 CJK 逐字索引）一次回答「哪幾份文件提到 X」，每份文件一列精確命中數 rollup。

## 選擇查詢模式：單文件或多文件

兩種模式使用同一套 IR 與區塊讀取能力，差別在搜尋範圍與前置處理：

| 需求 | 使用入口 | 適合情境 | 回傳重點 |
| --- | --- | --- | --- |
| **單文件查詢** | `documa search-blocks <ir_path>` / `documa_search_blocks` | 已知答案位於哪一份文件，要快速定位章節、表格或段落 | 依相關度排序的 block、結構路徑、頁碼、snippet 與下一步讀取建議 |
| **多文件查詢** | `documa search-collection` / `documa_search_collection` | 不確定答案在哪份文件，或要比較一批合約、報告、郵件 | 跨文件 block 命中；可按文件彙總、限制每份文件命中數，或限定文件範圍 |

> 這裡的「查詢」是 evidence retrieval：回傳可供後續讀取與引用的候選 block／文件，不會自行呼叫 LLM 合成最終答案。

### 查詢單一文件

先處理文件，再搜尋產生的 IR；搜尋只回候選片段，確認候選後再用 `block` 有界讀取原文：

```powershell
documa process .\report.pdf --out .\out\report --export-format block-json
documa search-blocks .\out\report\documa.ir.json --query "流動性覆蓋比率" --limit 5
documa block .\out\report\documa.ir.json --id "<搜尋結果的 block id>" --read --max-chars 1500
```

適合「這份報告如何定義 X？」或「這份合約的違約金是多少？」。如需頁碼與 bbox 引用，再對選定的 block 執行 `documa cite-block`。

### 查詢多份文件

多文件模式使用本機 store 與 SQLite FTS5 collection index。文件格式可以混用；下例把 Word、PDF 與 Markdown 放入同一個 collection：

```powershell
documa ingest .\contracts\master.docx --store-dir .\.documa
documa ingest .\contracts\amendment.pdf --store-dir .\.documa
documa ingest .\contracts\meeting-notes.md --store-dir .\.documa

# 第一次查詢前建立索引；之後 ingest/delete 會增量維持既有索引
documa index-collection --store-dir .\.documa
```

回答「哪些文件提到 X？」時，使用 `--group-by-document` 取得每份文件的精確命中數、最佳 snippet 與最多三個可讀取的 `top_blocks`：

```powershell
documa search-collection --store-dir .\.documa --query "違約金" --group-by-document
```

需要直接取得跨文件 block 命中時，省略 `--group-by-document`；可用 `--per-document-limit` 避免單一文件佔滿結果：

```powershell
documa search-collection --store-dir .\.documa --query "資本要求" --per-document-limit 2
```

後續只想搜尋已選定的文件時，可重複傳入 `--document-id`。Collection 結果中的穩定讀取鍵是 `(document_id, block_id)`；不要只保留 block id。

```powershell
documa search-collection --store-dir .\.documa --query "終止條款" `
  --document-id "doc-..." --document-id "doc-..."
```

> Collection search 是 lexical/statistical search，不會自動做同義詞、跨語言或語義相似度展開。查詢無結果時，先改用文件中的原詞、縮寫或較短詞組；需要語義檢索時，請透過預留的 hybrid/vector adapter 邊界接入外部 retriever。

## 支援的文件格式

所有 adapter 都輸出同一種 parser-neutral IR；下游的 block tree、search、read、cite 與 collection search 不直接依賴原始 parser 物件。因此，同一個 collection 可以同時包含不同格式：

| 類型 | 副檔名 | 轉換重點 |
| --- | --- | --- |
| PDF | `.pdf` | 保留頁面、文字區塊與 bbox 來源鏈；掃描件可用 `documa[all]` 與 `--ocr` |
| Word | `.docx` | 擷取標題、段落與表格 |
| PowerPoint | `.pptx` | 每張投影片視為一頁，擷取文字 shape、標題、表格與 bbox |
| HTML | `.html`, `.htm`, `.xhtml` | 依 DOM 順序保留標題、段落、表格與連結 |
| Email | `.eml`, `.msg` | 擷取郵件標頭、本文與附件清單／metadata；`.msg` 需要 `extract-msg` |
| Jupyter Notebook | `.ipynb` | 依 cell 順序保留 Markdown／程式碼內容、文字輸出預覽與附件 metadata |
| Markdown / text | `.md`, `.markdown`, `.mdp`, `.mdp.md`, `.txt` | 保留標題層級、段落、表格與 fenced code 內容 |

預設安裝即包含全部文件 adapter、MCP server 與本地 token counter：

```powershell
python -m pip install documa
```

首次安裝時尚無 MCP 行程，直接使用 pip 即可。升級或重裝時，請改用受控安裝入口：

```powershell
python -m documa.install --upgrade documa
```

它會先阻止新的 Documa MCP server 啟動，偵測並通知現有 server 退出；逾時未退出時強制終止，確認全部斷線後才執行 pip。Windows 上也會清理尚未支援生命週期登錄的舊版 `documa-mcp.exe`，避免 `WinError 32`。一般 pip/wheel 沒有可靠的 pre-install hook，因此不要用直接的 `pip install --upgrade documa` 取代這個升級入口。

若是從尚未提供 `documa.install` 的舊版升級，Windows 只需做一次遷移：

```powershell
Get-Process -Name documa-mcp -ErrorAction SilentlyContinue | Stop-Process -Force
python -m pip install --upgrade documa
```

Documa 的中文關鍵詞預設使用 LingXi 0.2.1，PDF extraction 預設使用
rust-pdf-parser 0.2.0。此開發環境可由本機來源專案安裝其已建置 wheel：

```powershell
python -m documa.install --force-reinstall "D:\PycharmProjects\rust_Lingxi\target\wheels\lingxi-0.2.1-cp39-abi3-win_amd64.whl"
python -m pip install --force-reinstall "D:\PycharmProjects\rust-pdf_parser\target\stage6c2e-final-wheels\rust_pdf_parser-0.2.0-cp310-cp310-win_amd64.whl"
```

若 native binding 缺失或版本不符，`pdf_provider=auto` 會可觀測地回退至
PyMuPDF，`keyword_provider=lingxi` 會回退至 n-gram；也可明確指定
`pdf_provider=pymupdf` 或 `keyword_provider=ngram`。PyMuPDF 仍負責頁面預覽與
OCR renderer，Rust parser 不宣稱具備渲染能力。

需要本機 CPU OCR 時改裝完整版本：

```powershell
python -m pip install "documa[all]"
```

限制：目前不支援舊式 Office `.doc`／`.ppt`；email 與 notebook 附件會保存為資產與 metadata，但不會遞迴當成獨立文件解析。各格式的版面語意會盡量映射到共同 IR，但不保證不同 parser 能還原完全相同的視覺結構。

### 回應層的 token 工程（v0.5.0）

省 token 不只靠「少讀」，也靠**每個回應本身就瘦**：

- 回應只傳一份 compact JSON（不重複傳 structuredContent + pretty text）。
- Block id 去掉重複的文件 GUID 前綴，envelope 宣告一次 `block_id_prefix`；空欄位一律不序列化；常數（如 `page_ref_kind`）上提到 envelope。
- 搜尋預設 `nav` profile：每個 hit 只回路由決策需要的欄位；citation/selection 細節留給 `evidence`，診斷留給 `debug`。
- 有 token counter 時，搜尋回應自動套 2,000 token 上限，超出時按「可有可無 → 低排名結果」順序優雅裁減並回報 `dropped_results`。
- Token 一律由真實 tokenizer 計算（tiktoken 自動偵測；Claude 可走 Anthropic count-tokens API），**禁止 chars/4 估算**。

同一份 69 頁 PDF 的實測（compact JSON tokens，v0.4.0 → v0.5.0）：

| 回應 | v0.4.0 | v0.5.0 |
| --- | --- | --- |
| `block_tree max_depth=2` | 9,756 | **2,813（-71%）** |
| `list_blocks depth=1` | 3,476 | **1,092（-69%）** |
| `search_blocks` 5 hits | 1,344 | 1,181 |
| MCP wire（傳輸層） | 每回應兩份 | **單份（再省一半）** |

固定成本也可控：MCP 工具面分 `agent`（16 工具，schema 約 3.3k tokens，涵蓋完整 evidence 工作流）/`advanced`/`admin` 三個 profile，plugin 預設最小的 `agent`。

可重現的量測入口：`python benchmarks/token_economy/run_agent_benchmark.py`（真實 tokenizer 計分：Tokens-to-Supported-Answer、Evidence Recall、Search Path Length、Budget Correctness）。

## 15 分鐘評估路徑

需要 Python 3.10+。以下以 PowerShell 為例（macOS/Linux 改路徑分隔符即可）：

```powershell
python -m pip install documa   # 完整非 OCR agent runtime
```

**1. 處理一份你自己的 PDF**（挑一份你熟悉內容的長文件，才能評估答案品質）：

```powershell
documa process .\your.pdf --out .\out\eval --export-format block-json
```

**2. 看大綱與梗概**（評估點：不讀內文能否掌握文件結構）：

```powershell
documa block-tree .\out\eval\documa.ir.json --max-depth 2
```

**3. 問一個具體問題**（評估點：hit 的排序品質與每 hit 的 token 成本）：

```powershell
documa search-blocks .\out\eval\documa.ir.json --query "你關心的主題"
```

**4. 只讀命中的區塊**（評估點：有界讀取 + 續讀 cursor）：

```powershell
documa block .\out\eval\documa.ir.json --id "<搜尋結果的 block id>" --read --max-chars 1500
```

**5. 產生可驗證引用**：

```powershell
documa cite-block .\out\eval\documa.ir.json --id "<block id>"
```

回傳類似：

```json
{
  "page_label": "PDF p.2",
  "grounding": "visual",
  "bboxes": [{ "page": 2, "x0": 56.0, "y0": 240.0, "x1": 486.0, "y1": 328.0 }],
  "citation_string": "[PDF p.2, bbox(56,240,486,328)]"
}
```

**6.（選配）看完整離線 demo**——不呼叫任何 LLM，輸出一份 trace 展示「搜尋→選塊→讀取→合成答案」全程與逐步 token 用量：

```powershell
documa block-demo .\your.pdf --question "這份文件的主要風險是什麼？" --out .\out\eval-demo
```

**多文件評估**：依照上方[查詢多份文件](#查詢多份文件)將文件 ingest 到同一個 store、首次建立 collection index，再執行分組或平面搜尋。

## 接進你的 agent

同一套能力有四個入口，行為一致：

| 入口 | 使用方式 |
| --- | --- |
| **MCP** | Host 建議以 `python -m documa.interfaces.mcp_server` 啟動，避免 Windows 長駐行程鎖住 `documa-mcp.exe`；相容用的 `documa-mcp` 命令仍保留。`DOCUMA_MCP_PROFILE=agent` 只暴露 evidence 工作流的 16 個工具。repo 內附三個現成 plugin：Claude Code（`plugins/claude-code-documa`）、Codex（`plugins/codex-documa`）、OpenClaw（`plugins/openclaw-documa`），各含引導 LLM 走短搜尋路徑的 `documa-evidence` skill。 |
| **OpenAI function calling** | `from documa.interfaces import openai_tool_schemas, call_documa_tool` |
| **CLI** | 上面評估路徑用的指令；適合 shell-based agent 或人工檢查。 |
| **Python API** | `documa.adapters` + `documa.pipeline.run_default_pipeline`，或直接 `call_documa_tool(name, args)`。 |

Python 最小範例：

```python
from documa.interfaces import call_documa_tool

result = call_documa_tool(
    "documa_search_blocks",
    {"ir_path": "out/eval/documa.ir.json", "query": "資本適足率", "limit": 5},
)
# result["structuredContent"]["results"] 每筆含 block_id/path/page/score/snippet/read_chars，
# recommended_next.actions[] 是可直接照發的下一步呼叫
```

給 LLM 的回應約定（plugin skill 已內建教學）：回應在 envelope 宣告一次 `block_id_prefix` 後發短 block id，回傳工具時原樣帶回即可；空欄位不出現＝空值；`page` 是引用標籤、`page_refs` 是實體頁碼。

## 設計性質（評估 checklist）

- **確定性、可離線**：core 無 ML/LLM 依賴、無網路呼叫，同輸入同輸出；適合放進 CI 與資安敏感環境。
- **出處可驗證**：每個 block 帶頁碼與 bbox 來源鏈；`documa_verify_citations` 做引用存在性檢查（明確不是語義驗證——語義驗證的 `AnswerSupportChecker` 介面在 core、實作在 examples）。
- **CJK 完整支援**：關鍵詞抽取用 CJK n-gram + 邊界熵新詞發現；collection FTS 逐字索引讓中文子詞查詢可用；snippet 視 CJK/ASCII 分別以字元/詞窗置中。
- **格式統一**：PDF（`.pdf`）、Word（`.docx`）、PowerPoint（`.pptx`）、HTML、email（`.eml`/`.msg`，含 mailbox 批次 `documa ingest-mailbox`）、Jupyter（`.ipynb`）、Markdown/text 全部進同一種 IR，下游只依賴 IR。
- **IR 是 semver 契約**：`ir_version` minor 只允許 additive 欄位；schema 由 dataclass 生成並有 CI 同步閘門（`documa validate-ir` 可驗檔）。
- **品質有 gold 基準**：`documa benchmark --mode quality` 對 13 個 gold case 計分（表格 TEDS、閱讀順序 NED、關係連結 F1）；雙欄/三欄/sidebar 閱讀順序 1.0。兩個已知缺口（footnote 與 caption 連結）**蓄意保留為 failed**，不調門檻掩蓋。
- **OCR 選配不混料**：`documa[all]`（額外加入 RapidOCR，CPU）處理掃描件；所有 OCR 產物標記 `origin: "ocr"` 與信心值，永不冒充原生文字。
- **索引皆可拋棄重建**：collection index（SQLite FTS5）與 retrieval sidecar 都是版本化衍生物，來源真相只在 IR + registry。

## 什麼時候不該用 Documa

- 文件很短（幾頁以內）而且只問一次——直接塞 context 更簡單，前置處理不划算。
- 你需要**語義**相似檢索（同義改寫、跨語言概念對齊）——Documa 檢索是 lexical/statistical 的；它預留了 hybrid/vector adapter 邊界，但目前不內建 embeddings。
- 你要的是視覺／版面渲染判斷（「第 3 頁有沒有蓋章」）、完整 UI 文件管理系統，或生產級 OCR 產品。
- 你只要最底層的 PDF 文字抽取，且已滿意 PyMuPDF / pdfplumber。

## 升級注意（v0.5.0）

- v0.5.0 前建立的 collection index 與 search sidecar 會被 `documa doctor --store-dir` 標為 stale，首次搜尋前請 `documa index-collection` 重建（或重跑 `process`）。
- 工具回應形狀有 breaking 變更：搜尋列移除 `read_ref`/`ir_document_id`/`bbox_refs`（讀取對＝`document_id` + `block_id`）、`block_tree` 預設不含 per-node citation、空欄位不再序列化。細節見 [CHANGELOG](CHANGELOG.md)。

## 深入閱讀

| 主題 | 位置 |
| --- | --- |
| 架構分層與各 stage 演進 | [docs/documa/architecture.md](docs/documa/architecture.md) |
| IR 相容性契約 | [docs/spec/ir-compatibility.md](docs/spec/ir-compatibility.md) |
| Gold 標註格式與品質門檻 | [fixtures/pdf/gold/README.md](fixtures/pdf/gold/README.md) |
| Token 經濟 benchmark | [benchmarks/token_economy/](benchmarks/token_economy/run_agent_benchmark.py) |
| Plugin 安裝與 skill | [plugins/README.md](plugins/README.md) |

## 開發與測試

```powershell
python -m pip install -e ".[dev]"
python -m pytest                      # 全套（含 snapshot 回歸）
documa doctor                         # 環境診斷
documa benchmark --mode quality       # gold 品質計分
```

Snapshot 回歸測試把 3 份真實 PDF 的完整 pipeline 輸出與 golden files 比對；只有「預期內的輸出變更」才能 `pytest --force-regen` 重建，且 commit 訊息必須說明原因。

新增 parser adapter：在 `src/documa/adapters/` 實作並於 `registry.py` 註冊副檔名，adapter 只回傳 IR、不外洩 parser 原生物件。新增 public tool：同步更新 `interfaces/tools.py`、`tool_schemas.py`、`mcp_server.py`、`cli.py` 與測試。

## License

MIT.
