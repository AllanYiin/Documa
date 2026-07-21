# Documa

<p align="center">
  <img src="https://raw.githubusercontent.com/AllanYiin/Documa/main/assets/documa-logo.png" alt="Documa logo" width="320">
</p>

Documa 是給 agent 用的 **document evidence runtime**：讓 agent 讀懂文件、只讀需要的部分、回答時指得出出處，而且引用可以被驗證。

核心流程只有四步：

1. **Ingest** — `documa ingest report.pdf` 把文件處理進本地儲存庫，拿到一個穩定的 `document_id`（同內容自動去重）。
2. **Block reading** — `documa search-blocks` 找相關區塊、`documa block --read` 只讀需要的內容，不用整份塞進 prompt。
3. **Citation** — `documa cite-block` 把任何區塊回溯到「第幾頁、頁面上哪個位置」，附原文摘錄與引用字串。
4. **Verifiable answer** — `documa_verify_citations` 檢查答案引用的區塊真實存在，`build_evidence_bundle` 把證據整包交給你選擇的驗證器逐條核對。

每一步都是確定性的（無 ML 依賴、可離線、同輸入同輸出），品質由 `documa benchmark --mode quality` 的 gold 標準答案持續量測。支援 PDF、Markdown、Office 文件、HTML、email 與 notebook——但格式支援是地基，不是賣點；賣點是**可稽核的答案**。

Documa 不是 UI 產品，也不是新的 PDF parser。

## Overview / 概覽

Documa 的最小成功路徑是：安裝套件，`documa ingest` 一份文件拿到 document_id，用 block search / read 讓 agent 只讀需要的內容，回答時用 citation 工具附出處。

這份 README 先帶你完成一次成功流程，再說明支援格式、email mailbox、Python API、tool-calling、MCP 與開發方式。

## 你什麼時候需要 Documa

適合使用 Documa 的情境：

- 你要讓 agent 或 RAG 系統讀文件，但不想一次把整份文件塞進 prompt。
- 你需要知道回答來自哪一頁、哪個 block、哪個來源檔案。
- 你有不同格式的文件，例如 `.pdf`、`.docx`、`.pptx`、`.html`、`.eml`、`.msg`、`.ipynb`，但想統一成同一種資料結構。
- 你要透過 CLI、Python function、OpenAI function tools 或 MCP host 使用同一套文件理解能力。
- 你要批次處理一個 email 資料夾，而不是只讀單封 email。

不適合使用 Documa 的情境：

- 你只需要最底層 PDF 文字抽取，而且已經滿意 PyMuPDF / pdfplumber 等 parser 的結果。
- 你要完整 UI、雲端文件管理系統或 email client。
- 你要生產級的完整 OCR 產品。Documa 提供選配的 `documa[ocr]` extra（RapidOCR）處理掃描與 image-only PDF，但 core 不內建 OCR，也不是 OCR 專用工具。

## Documa 會產生什麼

對一份文件執行 `documa process` 後，常見輸出是：

| 輸出 | 用途 |
| --- | --- |
| `documa.ir.json` | 完整的 Documa intermediate representation，是 parser-neutral 的主要資料。 |
| `documa.rag.json` | 給 RAG / retrieval 用的 chunks。 |
| `documa.blocks.json` | 給 agent 漸進式讀取的 block tree。 |
| `assets/` | 文件圖片、page preview、email attachments 或 notebook attachments。 |

最常用的 agent 讀取流程是：

1. `documa process` 產生 IR 與 blocks。
2. `documa search-blocks` 先找可能相關的區塊。
3. `documa block --read` 只讀需要的 block body。
4. 下游模型根據 block 內容與 metadata 回答。

## 快速開始：處理第一份文件

以下指令以 PowerShell 為例。macOS / Linux 使用者可把路徑分隔符號改成 `/`，並視環境把 `python` 改成 `python3`。

### 1. Prerequisites / Requirements / 準備 Python 環境

Documa 需要 Python 3.10 以上。

```powershell
python --version
python -m pip --version
```

建議使用 virtual environment：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Python Packaging User Guide 也建議先確認 Python 與 pip 可從命令列執行，必要時建立 virtual environment。

### 2. 安裝 Documa

如果你正在這個 repo checkout 內開發或試用：

```powershell
python -m pip install -e ".[dev,documents,mcp]"
```

如果你只想安裝最小核心能力，可先用：

```powershell
python -m pip install -e .
```

不同文件格式需要不同 optional extras。最省事的文件格式組合是 `documents`，它包含 PDF、DOCX、PPTX、HTML、MSG 與 notebook 相關依賴。

| Extra | 需要時機 |
| --- | --- |
| `pdf` | 讀 `.pdf`，安裝 PyMuPDF。 |
| `docx` | 讀 `.docx`。 |
| `pptx` | 讀 `.pptx`。 |
| `html` | 讀 `.html` / `.htm` / `.xhtml`。 |
| `msg` | 讀 Outlook `.msg`。 |
| `ipynb` | 讀 Jupyter `.ipynb`。 |
| `documents` | 一次安裝常見文件格式依賴。 |
| `mcp` | 啟動 `documa-mcp`。 |
| `demo` | 執行 demo 整合，例如 OpenAI / tiktoken 相關範例。 |

### 3. 準備一份 PDF

快速開始建議直接用 PDF，因為它最能展示 Documa 的核心價值：從 parser adapter 取得頁面、文字 block、座標與 metadata，再輸出 Markdown、RAG chunks 與 agent 可分段讀取的 blocks。

請準備一份「可抽取文字」的 PDF，例如技術報告、合約、研究摘要或產品規格。OCR-only 掃描 PDF 可能需要先經過 OCR，否則底層 parser 可能讀不到文字。

以下命令假設你的範例 PDF 放在 `.\tmp\sample.pdf`：

```powershell
New-Item -ItemType Directory -Force -Path .\tmp | Out-Null
$env:DOCUMA_SAMPLE_PDF=".\tmp\sample.pdf"
Test-Path -LiteralPath $env:DOCUMA_SAMPLE_PDF
```

最後一行應回傳 `True`。如果是 `False`，請把 `$env:DOCUMA_SAMPLE_PDF` 改成你的 PDF 路徑。

### 4. 執行處理

```powershell
documa process $env:DOCUMA_SAMPLE_PDF `
  --out .\out\sample `
  --lang zh-Hant,en `
  --export-format markdown `
  --export-format rag-json `
  --export-format block-json
```

預期會看到 JSON 結果，其中 `status` 應該是 `ok`。

### 5. 檢查輸出

```powershell
Get-ChildItem -LiteralPath .\out\sample
```

你應該至少看到：

- `documa.ir.json`
- `documa.md`
- `documa.rag.json`
- `documa.blocks.json`

這三種輸出各有用途：

| 輸出 | 你可以拿來做什麼 |
| --- | --- |
| `documa.md` | 給人類快速閱讀、diff，或放進只吃 Markdown 的工具。 |
| `documa.rag.json` | 丟給 retrieval / vector store ingestion。 |
| `documa.blocks.json` | 讓 LLM / agent 先搜尋 metadata，再只讀相關 block。 |

### 6. 像 agent 一樣搜尋與讀取

先搜尋 block：

```powershell
documa search-blocks .\out\sample\documa.ir.json --query "風險" --limit 5
```

再讀取某個 block：

```powershell
documa block .\out\sample\documa.ir.json --id "<block-id>" --read
```

`<block-id>` 請替換成上一個搜尋結果中的 block id。

如果你想看完整的 LLM 分塊查詢流程，可以跑 PDF 專用 demo。這個 demo 不呼叫外部 LLM；它會寫出一份 trace，展示「載入 PDF、建立 blocks、搜尋候選 blocks、只讀選中 blocks、根據 evidence 合成回答」的流程。

```powershell
documa block-demo $env:DOCUMA_SAMPLE_PDF `
  --question "這份文件的主要風險是什麼？" `
  --out .\out\sample-demo `
  --lang zh-Hant,en
```

完成後檢查：

```powershell
Get-ChildItem -LiteralPath .\out\sample-demo
```

你應該看到 `documa.ir.json`、`documa.blocks.json` 與 `documa.block_demo.trace.json`。

### 7. 查詢策略階梯（低 token 查詢）

agent（或你）查一份文件時，依問題形狀選路徑，再逐級收斂：

1. **結構／總覽問題**（「有哪些章節」）：`documa block-tree --max-depth 2 --no-citations` 取得便宜的大綱，需要更深再用 `documa blocks --parent-id <id>` 下鑽。
2. **特定事實**：`documa search-blocks --query "..." --limit 5`，預設 compact 輸出。搜尋回應內建兩個確定性引導：`recommended_next`（直接可用的下一步 `read_block` 呼叫，含建議的 `max_chars`）與 `hints`（零結果補救、`offset=N` 分頁、拆題建議）。
3. **讀取證據**：`documa block --id <id> --read --max-chars 1500`（或 `--max-tokens`，兩者取較嚴者）。token 一律由真實 counter 計算，不用 chars/4 之類的估算：裝了 `documa[tokens]`（tiktoken）會自動啟用；針對 Claude 可設 `DOCUMA_TOKEN_COUNTER=anthropic:<model>` 走 Anthropic count-tokens API（需 `anthropic` 套件與 `ANTHROPIC_API_KEY`）。沒有 counter 時 token 欄位為 null、`--max-tokens` 會回報 `TOKEN_COUNTER_UNAVAILABLE`。證據不足時先用 `documa source-window --id <id>` 取相鄰 block、`documa block-xref --id <id>` 看父子關係，最後才整節讀取。
4. **跨文件問題**：廣度問題（「哪幾份文件提到 X」）用 `documa search-collection --query "..." --group-by-document`——每份文件一列 rollup（精確命中數、最佳 snippet、至多 3 個可直接讀取的 top_blocks），再用 `--document-id doc-XXXX --per-document-limit 2` 鎖定候選文件深挖。事實問題直接 `documa search-collection --query '"資本適足率"'`——查詢詞預設全部必須命中（AND）、引號片語比對相鄰字序、snippet 以命中詞置中；全無命中時自動降級為任一詞命中並在 `match_mode` 標示。回應同樣內建 `recommended_next` 與 `hints`；`--max-response-tokens` 可設回應硬上限。命中的 `read_ref` 直接餵給 `documa block` 讀取。`documa ingest`／`delete-document` 預設會增量維護集合索引（ingest 完立即可搜）；`index-collection` 全量重建只在 `doctor` 回報索引陳舊或版本過期時作為修復手段。注意：`ingest-mailbox` 的信件**不會**進集合索引，要跨文件搜尋 email 請對 `.eml`/`.msg` 逐檔 `documa ingest`。
5. **收尾**：`documa cite-block` 產生穩定引用；agent 端另有 `documa_verify_citations` 工具（MCP／tool-calling 層）可在宣稱已驗證前做核對。

## 處理真實文件

Documa 目前支援這些輸入格式：

| 格式 | 副檔名 |
| --- | --- |
| PDF | `.pdf` |
| Markdown / text | `.md`, `.markdown`, `.mdp`, `.txt` |
| Word | `.docx` |
| PowerPoint | `.pptx` |
| HTML | `.html`, `.htm`, `.xhtml` |
| Email | `.eml`, `.msg` |
| Jupyter Notebook | `.ipynb` |

單一文件使用：

```powershell
documa process "<path-to-file>" --out ".\out\document" --export-format block-json
```

如果只想測試 adapter boundary，不跑完整 pipeline：

```powershell
documa parse "<path-to-file>" --out ".\out\parsed"
```

## 批次處理 email 資料夾

實務上 email 常常不是單封處理，而是讀取某個收件夾匯出的多個 `.eml` / `.msg` 檔案。這種情境請使用 collection-oriented CLI：

```powershell
documa ingest-mailbox ".\mailbox" `
  --out ".\out\mailbox" `
  --recursive `
  --progress jsonl
```

輸出會包含：

| 輸出 | 用途 |
| --- | --- |
| `documa.mailbox.manifest.json` | mailbox manifest，列出每封 email 的來源、輸出路徑與錯誤。 |
| `messages\...\documa.ir.json` | 每封 email 各自的 Documa IR。 |
| `messages\...\documa.rag.json` | 每封 email 的 retrieval chunks。 |
| `messages\...\documa.blocks.json` | 每封 email 的 block tree。 |
| `messages\...\assets\...` | 每封 email 的附件。 |
| `documa.mailbox.progress.jsonl` | 使用 `--progress jsonl` 時的逐封處理事件。 |

預設行為是：某一封 email 失敗時繼續處理其他信件，並把錯誤寫入 manifest。

如果你希望第一個錯誤就停止：

```powershell
documa ingest-mailbox ".\mailbox" --out ".\out\mailbox" --fail-fast
```

## 文件識別、引用與品質評測

以下能力讓 Documa 從「能處理文件」進一步支援「可稽核的 agent 回答」：穩定的 document_id、可回溯頁碼與座標的 citation、IR 格式契約，以及可量測的品質分數。

### ingest：取得穩定的 document_id

`documa ingest` 一次完成 parse + pipeline + 登錄，回傳 content-addressed 的 `document_id`（同內容重複 ingest 會去重）：

```powershell
documa ingest fixtures\pdf\real\annual-report.pdf
```

```json
{
  "status": "ok",
  "document_id": "doc-590798d84418a2a9",
  "deduplicated": false,
  "ir_path": ".documa\\documents\\doc-590798d84418a2a9\\documa.ir.json",
  "page_count": 3,
  "chunk_count": 7,
  "parser": "pymupdf"
}
```

之後所有吃 `ir_path` 的指令與工具都同時接受 document_id（規則：路徑存在優先，否則查 `./.documa` registry）。管理指令：`documa list-documents`、`documa delete-document <id> --yes`、`documa ingest --rebuild-index`（registry 損毀時從 `documents/` 重建索引）。

### collection search：跨 active 文件搜尋

多份文件 ingest 進同一個本地 store 後，可以建立 default collection index，再跨 active 文件搜尋 block。第一版使用 Python 標準庫 SQLite FTS5，索引是可重建的衍生資料；IR 與 registry 仍是 source of truth。

```powershell
documa index-collection --store-dir .\.documa
documa search-collection --store-dir .\.documa --query "Revenue" --limit 20
```

`search-collection` 的每筆結果會同時回傳 registry 的 `document_id`、IR 內部 `document_id`、`block_id`、heading path、score、snippet、page refs、bbox refs 與 citation string。`read_ref` 可直接餵給 `documa_read_block` / `documa cite-block` 類工具繼續讀取與引用；預設只索引 registry 中 `active` 文件，`superseded` 版本不會出現在搜尋結果中。

### citation：把 block / chunk 回溯到頁碼與座標

```powershell
documa search-blocks doc-590798d84418a2a9 --query "Revenue"
documa cite-block doc-590798d84418a2a9 --id <block_id>
```

```json
{
  "status": "ok",
  "page_label": "PDF p.2",
  "grounding": "visual",
  "bboxes": [{ "page": 2, "x0": 56.0, "y0": 240.0, "x1": 486.0, "y1": 328.0 }],
  "excerpt": "Region | Revenue (M) | Growth | Share Asia-Pacific | 412.5 | 18% | 41% ...",
  "citation_string": "[PDF p.2, bbox(56,240,486,328)]"
}
```

沒有座標的來源（例如 Markdown）會降級為 `grounding: "logical"`，仍給出頁碼 / 章節層級的 citation。相關指令與工具：`cite-chunk`、`source-window`（讀 block 前後文）、`documa_render_citation`（`page-bbox` / `markdown` / `inline` 三種格式）、`documa_verify_citations`（檢查引用的 block id 存在且帶頁碼——這是 id 存在性檢查，不是語義驗證）。

### OCR：掃描與 image-only 文件（選配）

```powershell
python -m pip install -e ".[ocr]"   # RapidOCR（ONNX、免 GPU、支援中英文）
documa process scan.pdf --out out --ocr
documa ingest scan.pdf --ocr
```

OCR 為明確 opt-in：低文字密度頁會整頁辨識，一般頁只辨識內嵌圖片。所有 OCR 產物都標記 `origin: "ocr"`、`ocr_engine`、`ocr_confidence`，不會混入 parser 原生文字；頁面平均信心低於 0.3 會標 `ocr_low_confidence`。未安裝 extra 時指令不會失敗，只在 payload `warnings` 中回報。第一次執行會下載辨識模型（需網路），之後使用本機快取。

### IR 契約：schema 與 validate-ir

`ir_version` 採 semver 語意（minor 只允許 additive 欄位），完整契約見 [docs/spec/ir-compatibility.md](docs/spec/ir-compatibility.md)。`schema/documa.schema.json` 由 `scripts/generate_schema.py` 從 dataclass 生成（CI 檢查同步，禁止手改）。

```powershell
documa validate-ir out\documa.ir.json   # exit code 0/1，violations 帶 JSON pointer
```

### 品質評測：benchmark --mode quality 與 diff

```powershell
documa benchmark --mode quality    # 對 gold 標註計分：table TEDS/TEDS-S、reading-order NED
documa diff actual.ir.json expected.ir.json   # 結構化差異：block 新增/缺失/易位、表格 cell
```

Gold 標註放在 `fixtures/pdf/gold/<case_id>/expected.partial.json`（格式見該目錄 README，支援表格 HTML（含 colspan/rowspan）、閱讀順序錨點、關係連結、頁首頁尾角色與 OCR 期望文字，並可逐 case 覆寫門檻）。目前 13 個 gold case 的實測：11 passed（含雙欄/三欄/sidebar 閱讀順序 1.0、五類表格 1.0、中英混排、TOC 連結、頁首頁尾分類），2 個為**蓄意保留的 failed**——footnote 與 caption 連結缺口，已在 gold 註記中標明「不得調低門檻掩蓋」，是下一輪的修復標的。

每個 block 都帶 reading-order trace（`metadata.reading_order`：zone/欄/規則/套用的 Gestalt 原則），benchmark 失敗時可直接定位排錯的區域；CI 的 quality job 會把逐 case 分數表顯示在每次 run 的 summary 頁。

## 產生 human viewer

Documa core 不是 UI 產品，但可以產生一份可檢視的 viewer artifact，方便人類檢查 parsing / block 結果。

```powershell
documa view .\out\sample\documa.ir.json `
  --from-ir `
  --format html `
  --out .\out\sample\viewer.html `
  --include-body
```

也可以直接從來源文件建立 viewer：

```powershell
documa view $env:DOCUMA_SAMPLE_PDF --format markdown --query "風險"
```

## 匯出給下游系統

如果你已經有 `documa.ir.json`，可以再匯出成其他格式：

```powershell
documa export .\out\sample\documa.ir.json --format markdown --out .\out\sample\documa.md
documa export .\out\sample\documa.ir.json --format rag-json --out .\out\sample\documa.rag.json
documa export .\out\sample\documa.ir.json --format block-json --out .\out\sample\documa.blocks.json
```

| Export format | 適合用途 |
| --- | --- |
| `json` | 完整 IR。 |
| `markdown` | 人類閱讀或簡單 diff。 |
| `rag-json` | Retrieval / RAG pipeline。 |
| `block-json` | Agent progressive reading。 |

## Python 用法

最小範例：

```python
from documa.adapters import PyMuPDFAdapter
from documa.adapters.base import ParseOptions
from documa.pipeline import PipelineContext, run_default_pipeline

document = PyMuPDFAdapter().parse(
    "tmp/sample.pdf",
    ParseOptions(languages=["zh-Hant", "en"]),
)

pipeline_run = run_default_pipeline(
    document,
    PipelineContext(settings={"max_chars": 1200}),
    include_chunking=True,
)

processed = pipeline_run.document
print(processed.id)
print(len(processed.document_blocks))
```

如果你要把 Documa 接到 agent runtime，通常會從 `documa.interfaces` 開始：

```python
from documa.interfaces import call_documa_tool, openai_tool_schemas

tools = openai_tool_schemas(strict=True)

result = call_documa_tool(
    "documa_search_blocks",
    {
        "ir_path": "out/sample/documa.ir.json",
        "query": "回滾",
        "limit": 5,
    },
)
```

## Tool-calling 與 MCP

列出 Documa tools：

```powershell
documa tools
```

啟動 MCP server：

```powershell
documa-mcp
```

常用 tools：

| Tool | 用途 |
| --- | --- |
| `documa_parse` | 將單一來源文件 parse 成 Documa IR。 |
| `documa_process` | Parse 後執行預設理解 pipeline。 |
| `documa_ingest_mailbox` | 批次 ingest `.eml` / `.msg` email 資料夾。 |
| `documa_view` | 建立 universal viewer payload 或輸出檔。 |
| `documa_list_blocks` | 列出文件 blocks，不展開全文。 |
| `documa_search_blocks` | 搜尋單一文件的 block metadata、preview 與 body snippets。 |
| `documa_index_collection` | 從本地 registry 的 active documents 重建 collection search index。 |
| `documa_search_collection` | 跨 active documents 搜尋 citation-ready block results。 |
| `documa_read_block` | 讀取指定 block body。 |
| `documa_block_tree` | 回傳完整 block hierarchy。 |
| `documa_block_xref` | 查 block 的 parent、children、來源與 relation refs。 |
| `documa_doctor` | 檢查環境與 optional dependencies。 |

## 核心概念

新手可以先記住四個名詞：

| 名詞 | 白話說明 |
| --- | --- |
| Adapter | 負責讀某種格式，例如 PDF、DOCX、EML。Adapter 會把 parser-specific 結果轉成 Documa 的通用資料。 |
| DocumentIR | Documa 的主要資料結構。下游不要直接依賴 PyMuPDF 或 extract-msg 的原生物件。 |
| Pipeline | 在 IR 上做整理，例如 reading order、table normalization、block tree、chunking、provenance。 |
| Block | Agent 最常讀的單位。先 search/list blocks，再只讀相關 block，可以避免一次載入整份文件。 |

Documa 的設計原則：

- Core 不直接依賴特定 parser object。
- 不靜默覆蓋原文；保留 raw text 與 normalized text。
- JSON 輸出使用 UTF-8，並保留非 ASCII 文字。
- CLI、Python tool layer、OpenAI tool schema 與 MCP wrapper 回傳結構化結果。
- 新增格式或 public tool 時，同步考慮測試、schema、CLI 與 MCP 入口。

## 專案結構

| 路徑 | 用途 |
| --- | --- |
| `src/documa/core/` | IR models、serialization、encoding、language 與 text normalization。 |
| `src/documa/adapters/` | 各格式 parser adapter。 |
| `src/documa/collections/` | 多文件 ingestion、registry 與 SQLite collection search index。 |
| `src/documa/pipeline/` | Parser-neutral understanding stages。 |
| `src/documa/exporters/` | JSON、Markdown、RAG JSON、block JSON exporters。 |
| `src/documa/interfaces/` | CLI / MCP / tool-calling 共用工具函式與 schemas。 |
| `src/documa/viewer.py` | Universal viewer payload 與 renderer。 |
| `examples/` | 範例 workflow。 |
| `tests/` | Unit / regression tests。 |

## 開發與測試

安裝開發依賴：

```powershell
python -m pip install -e ".[dev,documents,mcp]"
```

跑全部 unittest：

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests
```

若已經 editable install，也可以直接：

```powershell
python -m unittest discover -s tests
```

部分測試（snapshot 回歸）需要 pytest：

```powershell
python -m pytest
```

Snapshot 回歸測試（`tests/test_ir_snapshot_regression.py`）把 3 份真實 PDF 的完整 pipeline 輸出與 golden files 比對。只有在「預期內的輸出變更」時才能用 `python -m pytest --force-regen` 重建 golden files，且 commit 訊息必須說明變更原因；重建前先確認 diff 只包含預期欄位。

環境診斷：

```powershell
documa doctor
```

benchmark fixture 檢查（readiness 驗檔案，quality 對 gold 計分）：

```powershell
documa benchmark
documa benchmark --mode quality
```

CI 預期在 Python 3.10、3.11、3.12 上安裝 `.[dev,documents]`，執行 `python scripts/generate_schema.py --check`（schema 與 dataclass 同步閘門）、測試與 `documa doctor`。

## 新增格式或 public tool

新增 parser adapter 時：

1. 在 `src/documa/adapters/` 新增 adapter。
2. Adapter 只回傳 Documa IR objects，不把 parser-native object 洩漏到 core。
3. 在 `src/documa/adapters/registry.py` 註冊副檔名。
4. 補 unit / integration tests。
5. 更新 README 的支援格式表。

新增 public tool 時，至少同步更新：

- `src/documa/interfaces/tools.py`
- `src/documa/interfaces/tool_schemas.py`
- `src/documa/interfaces/mcp_server.py`
- `src/documa/cli.py`，如果需要 CLI command
- `tests/`
- README 使用說明

## 常見問題

### `documa` 指令找不到

先確認 virtual environment 已啟用，並重新安裝：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,documents]"
```

如果 console script 還不能用，可以改用 module 入口：

```powershell
$env:PYTHONPATH="src"
python -m documa.cli --help
```

### PDF 讀不到或出現 PyMuPDF 錯誤

安裝 PDF extra：

```powershell
python -m pip install -e ".[pdf]"
```

或安裝常見文件格式組合：

```powershell
python -m pip install -e ".[documents]"
```

### `.msg` 讀取失敗

Outlook `.msg` 需要 `extract-msg`：

```powershell
python -m pip install -e ".[msg]"
```

### `documa-mcp` 無法啟動

安裝 MCP extra：

```powershell
python -m pip install -e ".[mcp]"
```

### block 搜尋結果不符合預期

先檢查 `documa.ir.json` 與 `documa.blocks.json`，確認文件是否真的被 parse 到正確文字。PDF 的 natural reading order 取決於來源 PDF 的產生方式；在改 pipeline heuristics 前，請先補 fixture coverage。

### OCR-only PDF 沒有文字

Documa core 不內建 OCR pipeline。你需要先用 OCR 工具產出可抽取文字的 PDF，或新增 OCR-aware adapter。

## 已知限制

- Documa core 不是 UI product。UI code 應放在 examples 或 downstream applications。
- Documa 不從零實作底層 parser。PDF、DOCX、PPTX、HTML、MSG 與 IPYNB extraction 透過 adapters 委派給專門 library；EML 使用 Python 標準庫 MIME parser。
- 目前 pipeline stages 是保守 baseline，不是完美的 document understanding model。`documa benchmark --mode quality`（13 gold cases）持續量測：雙欄/三欄/sidebar 閱讀順序已達 1.0；**兩個已知連結缺口蓄意保留為 failed**——footnote marker 連不到註腳本文、圖片 caption 連不到內嵌圖片，詳見對應 gold 檔的註記。
- 閱讀順序 v2 對「欄中還有欄」的巢狀版面不遞迴切割，退為保守排序並在 trace 標記 `fallback_row_major`。
- OCR 為選配（`documa[ocr]`，RapidOCR/CPU）：無 GPU 加速路徑；第一次執行需下載模型；render zoom 2 下引擎會漏掃部分行（ocr-scanned gold case 門檻因此暫定 0.6，附註記）。
- Registry 以 filelock 保護跨程序寫入（單機語意）；store 目錄請放本機磁碟，網路磁碟（NFS/SMB）上的鎖語意較弱。
- DOCX 與 HTML 目前使用 logical flow / DOM order；PPTX 使用 slide order；EML / MSG 使用 logical email page；IPYNB 使用 cell order。
- 選用 LLM usage 只出現在 demos 或 downstream integrations。Core parsing 與 processing 是 deterministic，可離線執行。

## 背景與延伸閱讀

這些外部文件是 Documa README 與設計邊界的參考：

- GitHub 說 README 應回答專案做什麼、為什麼有用、如何開始、去哪裡求助：[About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)。
- Diataxis 將技術文件分成 tutorial、how-to、reference、explanation，有助於避免 README 同時塞太多不同任務：[Diataxis](https://diataxis.fr/)。
- Python Packaging User Guide 說明如何確認 Python / pip 與安裝套件：[Installing Packages](https://packaging.python.org/en/latest/tutorials/installing-packages/)。
- PyMuPDF 文件說明 PDF text extraction 不一定保留 natural reading order，但 block / word extraction 可提供 layout repair 線索：[PyMuPDF text recipes](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)。
- PyMuPDF 也把 Markdown extraction 明確放在 RAG / LLM 情境下討論，這也是 Documa README 用 PDF 作為第一個範例的原因之一：[PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/)。
- Python `email.parser` 提供 MIME message parser，可從 serialized email bytes 建立 `EmailMessage` 並遍歷 body 與 attachments：[Python email.parser](https://docs.python.org/3/library/email.parser.html)。
- Python `mailbox` 提供 Maildir、mbox、MH、Babyl 與 MMDF 等 mailbox collection 介面；Documa 因此把 email folder ingestion 放在 collection layer：[Python mailbox](https://docs.python.org/3/library/mailbox.html)。
- `extract-msg` 提供 Outlook `.msg` 解析入口，Documa 只在 adapter boundary 內使用該 parser：[extract-msg documentation](https://msg-extractor.readthedocs.io/en/latest/extract_msg/index.html)。
- MCP tools 使用 structured results 與 tool schemas，Documa 的 MCP wrapper 依此暴露工具：[MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)。

## License

MIT.
