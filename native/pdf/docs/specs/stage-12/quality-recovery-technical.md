# 規格整理 v 1.2.0

## 技術規格文件

### 假設與前提

- Stage 0–6D 已完成；Stage 6D memory/determinism gate PASS，但預設 provider 仍為 PyMuPDF。
- 目前 7 PDFs / 1,113 pages 的 normalized character F1 為 0.960813，tagged-order proxy 為 0.940546。
- `crates/pdf-core` 繼續擁有所有 PDF-aware 規則；Python、CLI、WASM 與 Documa 只轉型／映射。
- PyMuPDF 僅可作離線 shadow oracle，不得成為 Rust runtime dependency，也不得把其輸出當人工 gold。
- 本階段不加入 OCR、renderer、LLM semantic reasoning、加密、repair 或新的 PDF-aware dependency。

### 背景

Stage 6D 已使完整 Rust Documa 路徑達到 1.056367x PyMuPDF RSS，並保持 5.807007x 速度優勢；剩餘阻礙是品質，而非傳輸或記憶體。逐文件結果顯示 580 頁文件（F1 0.944646）與 AI Index（F1 0.964368）主導總體缺口；另兩份文件已超過 0.995，因此任何全域 heuristic 都有回歸風險。

PDF content order、作者 tagged logical order、幾何 inferred order 與排除 artifacts 後的 main flow 必須繼續分離。有效 tagged structure 優先；inferred order 只能在缺失或不可用時使用，並保留 confidence、fallback reason 與 provenance。

### 目標與量化成功標準

| Gate | Stage 7 完成條件 |
|---|---|
| 文字完整性 | frozen corpus normalized character F1 ≥ 0.995；任何既有 ≥0.995 文件不得下降超過 0.0005 |
| 字序代理 | character-bigram F1 不低於 0.99，且不得用刪除／重複文字投機提高 |
| Human order | 人工 gold pairwise precedence ≥0.95；tagged-order proxy ≥0.95 且 source/tagged/inferred/main-flow 不混用 |
| Artifact | 人工 gold 的 header/footer/page-number main-flow false-positive rate ≤1% |
| Table | 私有人工標註 TEDS-S ≥0.90；合成 exact fixtures 維持 1.0 |
| Image/Figure | 人工標註 occurrence precision/recall 與 caption-link F1 各 ≥0.95 |
| Silent loss | 0；所有 extraction warning 有 stable code 與 page context |
| Determinism | 每 provider/document/options 三次 canonical hash 100% 一致 |
| Memory | 完整 adapter RSS ≤1.2x PyMuPDF；不得以關閉必要 evidence 換分數 |
| Speed | 不低於 Stage 6D Rust throughput 的 0.90x；品質優先於速度 |

### 範圍

1. 建立逐頁 privacy-safe differential profiler，輸出 F1、precision/recall、字數差、Unicode 類別差、block/span/role/count、warning-code counts，不寫原文或完整 IR。
2. 先定位、再用最小 synthetic fixture 重現每個文字根因；一次修一類 mapping/visibility/duplicate/form/font 問題。
3. 建立 redistributable human-reading-order gold fixtures，覆蓋單欄、多欄、sidebar、跨欄標題、list、caption、table、重複 furniture、旋轉與直排。
4. 針對 gold 中的 inversion 類型改良 bounded inferred-order 規則；author tagged order 不得被 geometry 靜默覆寫。
5. 提供私有 table/image annotation manifest 與 validator；人工標籤才是 gate truth。
6. 重新跑 Rust core、四前端、Documa、frozen corpus 與 rollback gate。

### 不做什麼

- 不把 PyMuPDF/pdfminer/pdfplumber、PDFium、MuPDF、Poppler 或其 wrapper 加入 Rust parser。
- 不用 OCR 或影像模型補文字 F1；這會改變產品邊界與 benchmark 公平性。
- 不在 Rust 寫 domain/LLM semantic classification；Rust 只做 deterministic structural/geometric rules。
- 不把 provider 對 provider 的 table/image count 當準確率，也不自動產生 gold 後自評。
- 不因 memory 已 PASS 就切換預設 provider。

### 系統與核心流程

1. 驗證 corpus SHA/page count，分別啟動 PyMuPDF 與 Rust worker。
2. Worker 逐頁產生短生命週期文字 counters 與結構摘要；parent 對齊頁碼後計算差距並刪除 counters。
3. 報告只保留 aggregate/page metrics、warning codes、原因分類與 hash；privacy audit 阻擋原文、URL、path、完整 counter 落地。
4. 根據 worst-page clusters 建立最小公開 fixture；沒有公開 regression fixture 的修正不得進 parser。
5. 修正 `pdf-core` 後依 source/tagged/inferred/main-flow 分開驗證；四前端只檢查 DTO parity。
6. 人工 gold validator 驗證標註完整性與 schema，再計算 order/table/image gate。
7. 全部 gate PASS 才允許 Documa 預設 provider 切換；保留顯式 `pdf_provider="pymupdf"` rollback。

### 模組架構圖（SVG）

```svg
<svg viewBox="0 0 1200 420" xmlns="http://www.w3.org/2000/svg">
  <rect width="1200" height="420" fill="#f8fafc"/>
  <g font-family="sans-serif" font-size="18" fill="#0f172a">
    <rect x="40" y="70" width="210" height="90" rx="12" fill="#dbeafe" stroke="#2563eb"/>
    <text x="75" y="120">Frozen corpus / gold</text>
    <rect x="310" y="40" width="220" height="90" rx="12" fill="#dcfce7" stroke="#16a34a"/>
    <text x="355" y="92">Rust pdf-core</text>
    <rect x="310" y="190" width="220" height="90" rx="12" fill="#fef3c7" stroke="#d97706"/>
    <text x="350" y="242">Shadow oracle</text>
    <rect x="600" y="70" width="240" height="120" rx="12" fill="#ede9fe" stroke="#7c3aed"/>
    <text x="635" y="120">Privacy-safe diff</text><text x="645" y="148">+ gold scorer</text>
    <rect x="910" y="70" width="240" height="120" rx="12" fill="#fee2e2" stroke="#dc2626"/>
    <text x="960" y="120">Gate report</text><text x="945" y="148">PASS / NO-GO</text>
    <path d="M250 115H300 M530 85H590 M530 235C570 235 570 160 600 160 M840 130H900" stroke="#334155" stroke-width="3" fill="none"/>
    <text x="390" y="345" fill="#475569">產品執行路徑只包含 Rust；oracle 僅存在於離線驗證。</text>
  </g>
</svg>
```

### 專案目錄規劃

```text
crates/pdf-core/src/                 # PDF-aware extraction/order rules
crates/pdf-core/tests/               # synthetic exact/edge/contract tests
tools/stage12_page_quality_diff.py   # Stage 7A privacy-safe page profiler
tools/stage12_order_gold.py          # human-order annotation validator/scorer
tools/stage12_table_image_gold.py    # table/image annotation validator/scorer
tests/fixtures/stage12/quality/      # redistributable minimal fixtures + labels
tests/fixtures/stage12/private-*.example.json # private label templates only
docs/specs/stage-12/quality-*.md     # Stage 7 contracts and evidence
target/stage12-stage7*/              # ignored private-derived reports; never IR
```

命名以 `stage12_` 開頭供 benchmark tools；公開 fixture 必須有生成器、SHA 與明確 expected order/text。私有 PDF、原文、URL、完整 counters 與 annotation 實例不得進 repository。

### 核心資料模型

Page quality record 至少包含：`document_id`、`page_number`、兩 provider 的 non-whitespace length、character precision/recall/F1、bigram F1、block/span counts、Unicode category delta、Rust warning-code counts、tagged/artifact/furniture counts、`reason_candidates`。禁止包含文字、原始 character keys、URL、source path 或完整 IR。

Human-order gold 以穩定 fixture node IDs 表示 precedence pairs、main-flow membership 與 artifact role。Private table/image manifest 只在 secured local path 存在；repository 僅保留 schema/example 與 validator。

Stage 7.2 校準後，文字品質分成三層：raw parser extraction、Documa adapter integration、human semantic gold。PyMuPDF `find_tables()` 可能以重建表格文字替換原始 blocks，因此 complete-adapter F1 不得再被稱為 raw parser text F1。Stage 7.3 的 human-order manifest 每頁至少兩位獨立 reviewer；若有分歧，adjudication 必須附非內容 reason code。缺少 private labels 時 scorer 固定回報 `BLOCKED`，不得以 tagged proxy 或自動標註代替人工 truth。

### State、持久化與恢復

Benchmark 狀態為 `unconfigured → verified → measured → audited → accepted/rejected`。報告只能在 privacy audit 通過後寫入 `target/`；worker counters 使用 temporary directory 並在成功／失敗時刪除。中斷後重新驗證 SHA 並整個 case 重跑，不合併半成品 measurement。

### 介面與相容性

Stage 7A 不改公開 Rust/Python/WASM/CLI API。若品質修正需要新 metadata，必須 additive、bounded、stable-code，並先在 Layout IR schema 記錄；bindings 只轉型。Documa 保留 `pdf_provider="rust"|"pymupdf"`，切換前後皆可明確 rollback。

### 錯誤、回退與可觀測性

- corpus/gold 缺失：hard fail，不能以 skip 宣稱 gate PASS。
- hash/page mismatch：hard fail，錯誤含 case ID，不含私有路徑。
- optional PDF metadata malformed：保留可見文字、輸出 stable warning；limit error 立即終止。
- 指標：per-stage elapsed、pages/s、peak RSS、output bytes、warning-code counts、reason cluster counts。
- trace：每個修正由 failing fixture → rule ID → regression test → private aggregate 改善串接；不記錄私有文字。

### Edge / Abuse cases

- 畫面相同但 content stream 順序不同；tagged/inferred 結果應一致。
- 隱形／裁切／重疊文字、Type3、Form 重複 paint、ActualText、缺失 ToUnicode、垂直字、旋轉、極端 glyph count。
- Tagged tree 有 cycle、duplicate MCID、錯 page、部分缺失或與幾何矛盾；不得把壞 tag 當真值。
- 多欄含 sidebar、跨欄 heading、caption 穿插、table cell 與 footer；不得單純 y/x sort。
- annotation manifest 重複 ID、跨頁 reference、未涵蓋 cell、非法 bbox；validator 必須拒絕。

### 驗收條件與測試

- Stage 7A tool self-test、privacy test、worker cleanup test、exact page alignment test PASS。
- 每個 text/order 修正先有一個公開 failing fixture；修正後 legacy fixtures byte-compatible或有審核過的 additive baseline。
- `cargo fmt --all --check`、workspace all-target/all-feature Clippy `-D warnings`、workspace tests、wasm32 check/Clippy、exact wheel tests、Node WASM tests PASS。
- Documa focused/full/Ruff/doctor PASS；PyMuPDF 預設在 final cutover gate 前不變。
- frozen corpus 一 warm-up三 measured，所有 quality/determinism/memory/speed/gold gates 同時 PASS 才可切換。

### 外部研究依據（查證日：2026-07-29）

- PDF Association：content order 與 logical reading order 可能不同；tagged order 可用時優先。<https://pdfa.org/what-you-may-be-missing-when-you-search-pdf-documents/>
- PyMuPDF：原始順序與 `sort=True` 的近似左上至右下排序是不同模式。<https://pymupdf.readthedocs.io/en/latest/app1.html>
- pdfminer.six：`LAParams`／`boxes_flow` 以版面參數影響文字框排序。<https://pdfminersix.readthedocs.io/en/latest/topic/converting_pdf_to_text.html>
- Apache PDFBox：位置排序是 opt-in，不是 logical order 保證。<https://pdfbox.apache.org/2.0/cookbook/textextraction.html>
- Docling、Marker、Unstructured 官方 repositories 顯示 layout、reading order、table 往往分階段，且複雜多欄／巢狀結構仍需明確失敗與回退契約。<https://github.com/docling-project/docling>、<https://github.com/datalab-to/marker>、<https://github.com/Unstructured-IO/unstructured>
### 風險與未決事項

最大風險是把 PyMuPDF 的額外隱形／重複文字誤認成正確答案；Stage 7A 必須先區分「Rust 漏失」與「oracle 多算」。第二風險是 private human/table/image gold 需要人工判讀；沒有人工標籤時可以完成工具與模板，但不能宣稱相應 gate PASS。
