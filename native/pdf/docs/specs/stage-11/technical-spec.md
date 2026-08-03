# Stage 11 技術規格：Text Fidelity、閱讀順序與文件級資源治理

規格版本：1.2.0  
目標產品版本：0.2.0  
狀態：已實作並通過 0.2.0 release validation  
依據日期：2026-07-28；完成驗證：2026-07-29

## 假設與前提

- 專案延續唯讀、不渲染、文字抽取優先的定位。
- `crates/pdf-core` 仍是唯一包含 PDF-aware 規則的 crate。
- 不引入 `lopdf`、`pdf-rs`、PDFium、MuPDF、Poppler、PDFBox、PDF.js、pypdf、
  pdfminer 或其 wrapper 作為 parser 依賴。
- Rust core、CLI、Python 與 browser WASM 必須共用相同解碼、排序、warning 與 limits。
- Stage 11 主要依 PDF 1.7 行為驗收；PDF 2.0 normative conformance 另列研究工作。
- 兩份使用者提供的真實 PDF 未取得再散布授權，因此不得直接 commit 到 repository。

## 背景與實檔證據

Stage 10 後的實檔測試證明 parser 已能載入、驗證並抽取大型 PDF，但也揭露「成功抽出
字元」與「得到可閱讀文字」是不同層次的能力。

| 文件 | 結構驗證 | 預設 layout 結果 | Content order 結果 | 已知問題 |
|---|---:|---:|---:|---|
| AI Index 2026 | 45,151 個使用中物件 | 423 頁、1,090,153 長度、278,308 spans | 851,998 長度 | 預設 layout 產生 `A r tificial`、`Int elligenc e`；27 個 font fallback、2 個 missing mapping |
| 台灣政府動畫宣導影片 | 1,317 個使用中物件 | 15 頁、6,225 長度、2,000 spans | 4,288 長度 | 預設 layout 在連續中文字間插入多餘空白；warnings 為 0 |

AI Index 另包含一個 `/ObjStm`：壓縮資料 11,735 bytes、解碼後 3,740,592 bytes，
壓縮倍率約 318.76。它是合法結構，但超過一般 stream 的 200 倍 heuristic。這證明：

1. 格式結構型 stream 需要受控例外，不能套用單一壓縮倍率規則。
2. 例外只能放寬 heuristic，不能繞過絕對 decoded-byte 上限。
3. 同一 object stream 被多個 member 重複解析時，需要 bounded per-document cache 與
   document-wide decode accounting。

## 目標

1. 建立可測試的文字 fidelity contract，區分 Unicode 正確性、來源順序、閱讀順序、
   空白推論與段落推論。
2. 新增 `ContentOrder`、`Layout`、`Auto` 三種抽取模式，同時維持既有 API 相容性。
3. 使用字型 metrics、文字矩陣與 script-aware 規則改善英文與 CJK 空白品質。
4. 支援 marked content 的 `/ActualText` replacement，避免 ligature、裝飾字或替代文字
   遺失或重複。
5. 將解壓總量改為真正的 document-wide budget，並加入有界 object-stream cache。
6. 建立可合法維護的 synthetic fixtures、private real-world corpus manifest 與 golden
   assertions。

## 成功標準

| ID | 指標 | 驗收門檻 |
|---|---|---|
| FID-01 | AI Index Auto 標題 | 包含精確片段 `Artificial Intelligence Index Report 2026` |
| FID-02 | AI Index 空白 artifact | 不得出現 `A r tificial`、`Int elligenc e`、`Inde x` |
| FID-03 | 台灣文件 Auto 標題 | 包含連續片段 `台灣政府動畫宣導影片` |
| FID-04 | CJK 空白 artifact | 不得輸出 `台 灣 政 府 動 畫 宣 導` |
| FID-05 | 來源忠實性 | `ContentOrder` 不插入任何 geometry-derived space/newline |
| FID-06 | ActualText | 每個有效 marked-content replacement 恰好輸出一次 |
| FID-07 | 跨介面一致性 | 四種 front end 的 text、page count、warning codes 完全一致 |
| SEC-01 | 文件級解壓預算 | 所有實際 decode bytes 均計入單一 `PdfDocument` budget |
| SEC-02 | cache 上限 | cache bytes、entries 與單一 entry 都受 `ParseLimits` 約束 |
| PERF-01 | layout 複雜度 | 單頁 layout 目標為 O(n log n)，不得引入無界 O(n²) 比較 |
| REG-01 | 現有能力 | Stage 0–10 全部測試、Clippy、wasm32 check 持續通過 |

## 範圍

### 納入

- `ExtractionMode::{ContentOrder, Layout, Auto}`。
- V2 抽取 options 與 backward-compatible legacy adapters。
- glyph/span geometry、font advance、writing mode 與 source ordinal。
- Simple font `/Widths`、`/FirstChar`、`/MissingWidth`。
- Type0 descendant font `/DW`、`/W` 與 writing mode 所需的最小 metrics。
- Script-aware space insertion、baseline clustering、rotation bucket 與 deterministic ordering。
- `BMC`、`BDC`、`EMC` 及 `/ActualText`。
- 有界 object-stream cache、document-wide decoded-byte accounting。
- aggregated quality metadata 與穩定 warning codes。
- synthetic、private real-world、cross-binding 三層回歸測試。

### 不納入

- 頁面 raster rendering、縮圖或視覺預覽。
- OCR、影像文字辨識或以 glyph 外形猜 Unicode。
- 密碼、加密 PDF、簽章驗證。
- 損毀 xref 猜測式 repair。
- LZW、ASCII85、RunLength、CCITT、JBIG2、JPEG 2000 等新 codec。
- 以機器學習推測閱讀順序。
- 完整 Tagged PDF structure tree 導覽；本階段只做 `/ActualText` 與後續 logical-order
  所需的資料保留。
- 改變既有 legacy API 的預設 layout 行為。

## Persona 與主要任務

| Persona | 主要任務 | 關鍵要求 |
|---|---|---|
| Rust 開發者 | 從不可信 PDF 取得可稽核 Unicode 文字 | safe Rust、穩定 errors、明確 limits |
| CLI 使用者 | 抽取純文字或取得品質診斷 | 模式易選、stdout 穩定、warnings 可機器讀取 |
| Python 使用者 | 批次建立文字索引 | 結果可序列化、舊呼叫方式仍可用 |
| Browser 開發者 | 在瀏覽器本地抽取文字 | 無檔案 I/O、WASM 記憶體上限明確 |
| QA／維護者 | 把失敗 PDF 固化為回歸案例 | corpus 合法、golden 可重現、差異可解釋 |

## 任務模型與資訊優先級

| 層級 | 任務 | 為何屬於這一層 | 公開介面要求 |
|---|---|---|---|
| 唯一主目標 | 從 PDF 取得可信且可稽核的 Unicode 文字 | 這是產品存在的主要價值 | 所有 front end 都能選 mode 並取得相同 text |
| 次目標 | 判斷哪些內容來自 fallback 或 layout 推論 | 使用者需要評估索引品質 | warnings 與 quality metadata 不混入純文字 |
| 低頻目標 | 查看 object、cache、decode 與 ambiguity 診斷 | 只在追錯或效能調校時需要 | CLI `--diagnostics`／structured result 才揭露 |
| 罕見目標 | 自訂 limits、跑 private corpus、更新 golden | 維護與安全驗證工作 | Rust options／測試工具，不增加一般操作負擔 |

| 資訊項目 | 分類 | 預設是否輸出 | 理由 |
|---|---|---|---|
| 抽取文字 | action-critical | 是 | 主產品結果 |
| page／span 結構 | decision-supporting | JSON／structured mode | 供索引與品質分析 |
| warnings | exception-handling | 詳細結果；純文字走 stderr | 不污染 text，仍可稽核 |
| quality counters | status-feedback | V2 詳細結果 | 說明人工 separator 與 fallback 程度 |
| cache／decode metrics | audit-history | 否 | 只在 diagnostics／test 顯示 |
| compatibility／限制 | reference | 文件 | 不重複塞入每次抽取結果 |

## G3M（Goal、Guardrails、Measures）

- Goal：讓 `Auto` 在兩份實檔與 synthetic corpus 上產出可閱讀文字，同時保留原始順序模式。
- Guardrails：不渲染、不 OCR、不猜 Unicode、不依賴現有 parser、不放寬絕對資源上限、
  不破壞 legacy 預設。
- Measures：FID-01～FID-07、SEC-01～SEC-02、PERF-01、REG-01，以及 private corpus
  required／forbidden fragments。

## Fidelity contract

### Unicode precedence

文字來源必須依下列順序決定，不得跳級猜測：

1. 有效 `/ActualText`：取代整個 marked-content 區段。
2. 有效 ToUnicode mapping：支援一對一與一對多 Unicode scalar sequence。
3. 明示 simple/composite font fallback：輸出並聚合 `font_fallback_encoding`。
4. 無 mapping：輸出 U+FFFD，聚合 `unicode_mapping_missing`。
5. 無效 destination：不得反轉 surrogate 或從 glyph 外形猜值；記錄
   `unicode_mapping_invalid`，若該 source code 被使用，再輸出 U+FFFD。

### 順序與 separator

- `source_ordinal` 是 page/Form traversal 中的單調遞增值，任何 layout pass 不得修改。
- `ContentOrder` 只依 `source_ordinal` 輸出；僅保留 PDF 文字運算元中明確存在的 Unicode
  whitespace。
- `Layout` 依 geometry 產生閱讀順序與 separator，但所有插入值必須標註 origin。
- `Auto` 使用與 `Layout` 相同的分層演算法，並加入 script、font metrics、rotation、
  tagged hints 與 ambiguity 判斷。
- 無法可靠判斷時保留較少的人工空白，並增加 quality counter；不得用大量空白掩飾不確定性。

### Warning 與 quality metadata

Warnings 僅代表可能改變文字內容的 recovery，不為每個正常的 layout decision 發 warning。
高頻事件必須按 page、font、code 聚合，避免 input-controlled warning amplification。

| Code／欄位 | 類型 | 用途 |
|---|---|---|
| `font_fallback_encoding` | warning | 無 ToUnicode，使用明示 fallback |
| `unicode_mapping_missing` | warning | 使用到沒有 mapping 的 source code |
| `unicode_mapping_invalid` | warning | CMap destination 不是有效 UTF-16BE |
| `actual_text_invalid` | warning | `/ActualText` 型別或文字內容無法合法解碼 |
| `reading_order_ambiguous` | warning | 旋轉、重疊或多欄使 Auto 無法可靠排序 |
| `inserted_spaces` | quality counter | geometry-derived spaces 數量 |
| `inserted_line_breaks` | quality counter | geometry-derived newlines 數量 |
| `fallback_glyphs` | quality counter | 使用 fallback 的 glyph 數量 |
| `replacement_characters` | quality counter | 輸出的 U+FFFD 數量 |
| `ambiguous_boundaries` | quality counter | 選擇不插 separator 的不確定 boundary 數量 |

## ExtractionMode 與相容性

```rust
pub enum ExtractionMode {
    ContentOrder,
    Layout,
    Auto,
}

pub struct TextExtractionOptionsV2 {
    pub normalize_unicode: bool,
    pub mode: ExtractionMode,
    pub include_quality_metadata: bool,
}
```

### 相容規則

- 保留 `TextExtractionOptions { normalize_unicode, layout }` 與 `extract_text`。
- 新增 `extract_text_v2`；legacy `layout: false` 對應 `ContentOrder`，`true` 對應 `Layout`。
- legacy API 不會自動改成 `Auto`。
- CLI 新增 `--mode content-order|layout|auto`；既有 `--no-layout` 保留一個 minor release。
- `--mode` 與 `--no-layout` 同時出現時，CLI 必須拒絕並回非零 exit code。
- Python 新增 `extract_v2(data, *, mode="auto", normalize_unicode=False, quality=True)`；
  舊 `extract(..., layout=...)` 保留。
- WASM 保留既有 positional `extract`，新增 `extractWithOptions(bytes, options)`，避免改壞
  已產生的 JavaScript 呼叫。
- 所有 mode parsing 只存在 binding adapter；PDF 規則與 layout 決策仍只能在 core。

## 核心資料模型

```rust
pub struct PositionedGlyph {
    pub page_index: usize,
    pub source_ordinal: u64,
    pub unicode: String,
    pub text_origin: TextOrigin,
    pub font_resource: Option<String>,
    pub font_size: f64,
    pub writing_mode: WritingMode,
    pub origin: [f64; 2],
    pub advance: [f64; 2],
    pub baseline: [f64; 2],
    pub rotation_bucket: i16,
}

pub enum TextOrigin {
    ActualText,
    ToUnicode,
    FontFallback,
    Replacement,
}

pub enum SeparatorOrigin {
    Explicit,
    GeometrySpace,
    GeometryLineBreak,
    BlockBreak,
}

pub struct TextQuality {
    pub inserted_spaces: usize,
    pub inserted_line_breaks: usize,
    pub fallback_glyphs: usize,
    pub replacement_characters: usize,
    pub ambiguous_boundaries: usize,
}
```

實作可使用 crate-private compact representation，但公開 DTO 的語意與欄位必須一致。所有
count、ordinal 與 vector growth 都受既有或新增 limits 約束。

## Font metrics 與 geometry 規則

1. Simple font 優先使用 `/Widths`；缺項時使用 FontDescriptor `/MissingWidth`，再缺時才進入
   明示 heuristic。
2. Type0 font 使用 descendant CIDFont `/DW`、`/W`。遇到不支援的 vertical metrics 時，
   保留 writing mode 並產生 ambiguity，不假裝為水平排版。
3. glyph advance 必須納入 font size、horizontal scaling、character spacing、word spacing、
   `TJ` adjustment 與 text matrix。
4. page space 座標必須套用 text matrix、CTM 與 Form XObject matrix。
5. 非有限浮點數、overflow 或退化 matrix 必須回穩定 error 或 bounded warning，不得進入排序。

## Layout 與 Auto 規則

### 分層演算法

1. 按 page、rotation bucket、writing mode 分組。
2. 使用 baseline 與 glyph height 的相對比例建立 line clusters。
3. 每個 line 以沿 writing direction 的投影座標 stable sort；相同座標以 source ordinal
   tie-break。
4. 以 font advance 與鄰接 gap 的正規化值判斷 boundary。
5. 再以 line box 與大間距建立 block／column 候選。
6. 若 column 判斷不穩定，`Auto` 回退為 line-level layout，不跨 block 猜順序。

### Script-aware separator

- Latin、Greek、Cyrillic 等具常用詞間空白的 script，只有在 normalized gap 超過
  tuning threshold 時插入一個 space。
- 連續 Han、Hiragana、Katakana、Hangul glyph 間不得因一般 glyph gap 插入 space。
- CJK 與 Latin／數字交界依 explicit whitespace、font run 與 gap 決定；不採一律插入。
- 已存在 Unicode whitespace 時不得再插入第二個 space。
- geometry space 最多插入一個；block boundary 最多插入兩個 newline。
- public API 不承諾 tuning 常數，但相同版本、相同 options 的結果必須 deterministic。

## ActualText 與 marked content

- `content.rs` 必須辨識 `BMC`、`BDC`、`EMC`，並保存 property list direct dictionary 或
  resource name。
- stack depth 受 `max_object_depth` 或更小的 `max_marked_content_depth` 保護。
- `/ActualText` 依 PDF text-string 規則解碼，不使用 font ToUnicode。
- 有效 `/ActualText` 代表 enclosed sequence 的整段 replacement；巢狀區段由最內層有效
  replacement 優先，父層不得重複輸出子層 glyph。
- 缺少 `EMC`、錯誤巢狀、錯誤 property type 必須有 valid、invalid、limit regression。
- `/MCID` 在本階段只保存 metadata，不實作完整 structure tree reading order。

## 文件級資源治理

### DecodeBudget

- 每個 `PdfDocument` 建立 monotonic `DecodeBudget`。
- 每次實際 filter decode 後，以 checked arithmetic 增加 decoded bytes。
- predictor 前後的 allocation 都必須受 per-stage 與 document-wide 上限約束。
- cache hit 不重複計入 decode bytes；cache eviction 後若重新 decode，必須再次計入。
- `max_total_decoded_bytes` 在 V2 明確定義為 document lifetime budget，不再只是單次
  filter chain counter。

### ObjectStreamCache

- cache key 為 object-stream `ObjectId` 與解析 revision identity。
- cache value 至少包含 decoded bytes、header index 與 validated member ranges。
- cache 為 per-document、bounded、無 unsafe、不得跨文件共用。
- 新增 limits：`max_cached_object_stream_bytes`、`max_cached_object_streams`。
- 單一 entry 仍受 `max_decoded_stream_bytes`；cache 不得放寬 stream absolute limit。
- eviction 採 deterministic LRU 或 clock policy；測試不得依 wall-clock。
- 結構例外只允許 `/Type /XRef` 與已驗證 `/Type /ObjStm`，一般 content/image stream
  仍受 expansion-ratio heuristic。

## 狀態模型

| State | 進入條件 | 行為 | 離開條件 |
|---|---|---|---|
| `parsed` | header/xref 建立成功 | 尚未解碼頁面文字 | 呼叫抽取或驗證 |
| `decoding` | 要求 object/content stream | 扣除 document decode budget，查詢 cache | decode 成功或 error |
| `collecting` | content operations 可用 | 產生 source-ordered glyphs、處理 ActualText | page glyph collection 完成 |
| `laying_out` | mode 為 Layout／Auto | 分行、排序、插入可追溯 separator | page result 完成 |
| `resolved` | 所有要求頁面完成 | 回傳 text、pages、spans、quality、warnings | caller 釋放 document |
| `blocked` | fatal syntax、unsupported feature 或 limit | 回穩定 error，不回部分成功假象 | caller 更改 input／limits |

## 模組與目錄規劃

```text
crates/pdf-core/src/
  text.rs                 legacy API adapter 與抽取 orchestration
  text_model.rs           PositionedGlyph、origin、quality、V2 DTO
  text_decode.rs          ActualText／ToUnicode／fallback precedence
  layout.rs               line/block clustering 與 deterministic ordering
  font_metrics.rs         simple／CID font widths 與 advance
  marked_content.rs       BMC／BDC／EMC stack 與 ActualText scope
  decode_budget.rs        document-wide accounting
  object_stream_cache.rs  bounded per-document cache

crates/pdf-core/tests/
  stage11_modes.rs
  stage11_actual_text.rs
  stage11_layout.rs
  stage11_decode_budget.rs

crates/pdf-cli/
  src/main.rs             --mode 與 legacy flag 衝突處理
  tests/stage11_modes.rs

bindings/python/
  src/lib.rs              extract_v2 type／error conversion
  tests/test_stage11.py

bindings/wasm/
  src/lib.rs              extractWithOptions
  tests/stage11_web.rs

tests/
  fixtures/stage11/       可散布 synthetic PDF 與 golden
  real-world/manifest.toml.example
  real-world/README.md

docs/
  compatibility.md
  errors.md
  architecture.md
  text-fidelity.md
```

檔案拆分可在實作時微調，但 PDF-aware 規則不得移至 bindings。若單檔仍低於合理維護成本，
可先使用現有模組的 crate-private submodule，避免為了目錄美觀過度拆分。

## 模組架構圖

```svg
<svg viewBox="0 0 1200 640" xmlns="http://www.w3.org/2000/svg">
  <rect width="1200" height="640" fill="#F8FAFC"/>
  <style>
    .box{fill:#FFFFFF;stroke:#334155;stroke-width:2;rx:12}
    .core{fill:#E0F2FE;stroke:#0369A1;stroke-width:2;rx:12}
    .safe{fill:#ECFDF5;stroke:#047857;stroke-width:2;rx:12}
    .t{font:20px sans-serif;fill:#0F172A}
    .s{font:15px sans-serif;fill:#334155}
    .a{stroke:#475569;stroke-width:2;marker-end:url(#arrow)}
  </style>
  <defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3"
    orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#475569"/></marker></defs>
  <rect class="box" x="35" y="55" width="210" height="500"/>
  <text class="t" x="65" y="95">公開介面</text>
  <text class="s" x="65" y="140">Rust library</text>
  <text class="s" x="65" y="180">CLI</text>
  <text class="s" x="65" y="220">Python</text>
  <text class="s" x="65" y="260">Browser WASM</text>
  <text class="s" x="65" y="320">只轉換 options、</text>
  <text class="s" x="65" y="345">結果與 errors</text>
  <rect class="core" x="320" y="55" width="520" height="500"/>
  <text class="t" x="350" y="95">pdf-core：唯一 PDF-aware 邊界</text>
  <rect class="box" x="355" y="125" width="205" height="105"/>
  <text class="s" x="380" y="160">Decode precedence</text>
  <text class="s" x="380" y="190">ActualText / ToUnicode</text>
  <rect class="box" x="595" y="125" width="205" height="105"/>
  <text class="s" x="620" y="160">Font metrics</text>
  <text class="s" x="620" y="190">Glyph geometry</text>
  <rect class="box" x="355" y="270" width="205" height="105"/>
  <text class="s" x="380" y="305">Layout / Auto</text>
  <text class="s" x="380" y="335">可追溯 separators</text>
  <rect class="safe" x="595" y="270" width="205" height="105"/>
  <text class="s" x="620" y="305">DecodeBudget</text>
  <text class="s" x="620" y="335">ObjectStreamCache</text>
  <rect class="box" x="475" y="420" width="205" height="90"/>
  <text class="s" x="505" y="455">Text + spans</text>
  <text class="s" x="505" y="482">quality + warnings</text>
  <rect class="safe" x="910" y="55" width="250" height="500"/>
  <text class="t" x="940" y="95">安全邊界</text>
  <text class="s" x="940" y="145">per-stream limit</text>
  <text class="s" x="940" y="180">document budget</text>
  <text class="s" x="940" y="215">cache bytes / entries</text>
  <text class="s" x="940" y="250">span / page limits</text>
  <text class="s" x="940" y="285">depth / warning limits</text>
  <text class="s" x="940" y="340">checked arithmetic</text>
  <text class="s" x="940" y="375">forbid unsafe</text>
  <line class="a" x1="245" y1="305" x2="320" y2="305"/>
  <line class="a" x1="560" y1="230" x2="560" y2="270"/>
  <line class="a" x1="700" y1="230" x2="700" y2="270"/>
  <line class="a" x1="560" y1="375" x2="560" y2="420"/>
  <line class="a" x1="840" y1="305" x2="910" y2="305"/>
</svg>
```

## Real-world corpus 規則

`tests/real-world/manifest.toml.example` 定義 schema，但不保存受限 PDF：

```toml
schema_version = 1

[[document]]
id = "ai-index-2026"
file_name = "ai_index_report_2026.pdf"
sha256 = "<verified hash>"
redistributable = false
expected_pdf_version = "1.7"
expected_pages = 423
expected_in_use_objects = 45151
required_auto_fragments = ["Artificial Intelligence Index Report 2026"]
forbidden_auto_fragments = ["A r tificial", "Int elligenc e", "Inde x"]
expected_warning_codes = [
  "font_fallback_encoding",
  "unicode_mapping_invalid",
  "unicode_mapping_missing",
]

[[document]]
id = "taiwan-government-animation"
file_name = "台灣政府動畫宣導影片.pdf"
sha256 = "<verified hash>"
redistributable = false
expected_pdf_version = "1.4"
expected_pages = 15
expected_in_use_objects = 1317
required_auto_fragments = ["台灣政府動畫宣導影片"]
forbidden_auto_fragments = ["台 灣 政 府 動 畫 宣 導"]
expected_warning_count = 0
```

- 實際 hash 必須由測試 runner 計算，不可在規格中臆造。
- 私有 corpus 路徑由 `RUST_PDF_REAL_CORPUS_DIR` 提供，不硬編碼使用者目錄。
- CI 未配置 corpus 時回報 skipped，不得假裝 passed。
- 每個真實失敗案例都要另建立可散布、最小化、無敏感內容的 synthetic regression。
- golden 更新必須經人工 review；不得用「接受所有新輸出」的方式消除 regression。

## API／介面 mapping

| 能力 | Rust | CLI | Python | WASM |
|---|---|---|---|---|
| 選擇模式 | `TextExtractionOptionsV2.mode` | `--mode` | `extract_v2(mode=)` | `extractWithOptions({mode})` |
| Legacy layout | `TextExtractionOptions.layout` | `--no-layout` | `extract(layout=)` | legacy `extract(..., layout)` |
| Quality metadata | `ExtractedTextV2.quality` | JSON `quality` | dict `quality` | object `quality` |
| Warnings | `Vec<TextWarning>` | stderr／JSON | list | array |
| Limits | `parse_with_limits` | 預設 profile | 暫沿 core default | browser-safe profile |

## 介面設計與適用性矩陣

本產品是 library、CLI 與 language bindings，沒有圖形工作台。文件中的 SVG 用於說明資料流，
不是新增 UI／viewer 的需求。CLI 遵循 stdout 是產品輸出、stderr 是診斷、JSON 可機器讀取的
既有契約；machine-readable mode 禁止 ANSI color 或互動提示。

| 一般產品規格項目 | 本專案判斷 | Stage 11 規則 |
|---|---|---|
| 圖形 UI、元件、首屏、色彩 | 不適用 | 不建立 dashboard；文件圖採淺色高對比 SVG |
| UI event reporting | 不適用 | library 不發 telemetry；CLI 只在明示 diagnostics 輸出 metrics |
| UI ↔ API mapping | 以四種 front end mapping 取代 | 使用上方 Rust／CLI／Python／WASM 對照表 |
| Create／Update／Delete | 不適用 | parser read-only，不建立可編輯 domain object |
| 專案保存與續編 | 不適用於使用者流程 | 只版本化 fixtures、manifest schema、goldens、benchmark baseline |
| storage migration／seed | 適用於測試資產 | manifest 使用 `schema_version`；synthetic fixtures 是 seed corpus |
| 上傳預覽與等比縮放 | 不適用 | core 接收 bytes；不顯示或縮放頁面 |
| LLM Streaming | 不適用 | 專案沒有生成式模型或生成內容 |
| 背景通知／重試 | 不適用於 core | 同步、無隱藏執行緒；caller 自行管理取消與重試 |

## CLI 狀態與輸出揭露策略

| 狀態 | stdout | stderr | JSON／diagnostics |
|---|---|---|---|
| 成功純文字 | 只有抽取文字 | 聚合 warnings | 未要求時不輸出 |
| 成功 structured | JSON result | 空或僅不可序列化錯誤 | text/pages/spans/quality/warnings |
| 可回復 fidelity 限制 | 可用文字 | warning code、page、count | 聚合 warning 與 recovery action |
| fatal parse／limit | 不輸出部分成功假象 | 穩定 error JSON、非零 exit | error code／offset／message |
| diagnostics | 產品輸出不變 | 不混入高頻 trace | decode/cache/layout counters |

## 錯誤、回退與可觀測性

- Public errors 維持 `{code, offset, message}`；程式只能依 `code` 分支。
- options 衝突使用穩定 `invalid_option` 或 CLI argument error；不得假冒 PDF syntax error。
- warnings 必須有 `code`、page、font、object、count、recovery action 中可取得的欄位。
- CLI `validate` 保留 object id、storage kind、object-stream id/index 的 context。
- 新增 crate-private metrics：decoded bytes、cache hit/miss/eviction、layout input spans、
  line clusters、ambiguous boundaries；library 不啟動背景 telemetry。
- CLI 只有在 `--diagnostics` 或 JSON quality output 時顯示統計，不污染純文字 stdout。

## 背景執行與持久化

本產品是同步 library／CLI，不新增 queue、daemon、network service 或通知。持久化只包含：

1. repository 內的 synthetic fixtures、goldens 與 manifest schema。
2. 使用者本地的 private corpus 路徑設定；parser 不複製或上傳檔案。
3. benchmark baseline 檔，需包含 schema version、工具鏈與硬體描述。

若呼叫端自行放入 background worker，取消與重試由呼叫端管理；core 必須保持 deterministic、
無隱藏執行緒與可安全丟棄的同步運算。

## 測試案例與驗收

### 必備 synthetic cases

- content order 與幾何閱讀順序相反。
- producer 依 font 分批寫入文字。
- Latin 每字獨立定位但應組成單字。
- CJK 每字獨立定位且不應插入空白。
- explicit space、word spacing、character spacing 與 `TJ` adjustment。
- 旋轉 90／180／270 度與 vertical writing marker。
- multi-column ambiguity 回退。
- ligature 的一對多 ToUnicode。
- missing、indirect、cyclic、malformed ToUnicode／Encoding。
- nested `BDC /ActualText ... EMC`，含父子 replacement。
- hidden／overlapping duplicate text。
- 高壓縮 xref/object stream、cache eviction、document budget exhaustion。
- 每個新增 parser path 的 truncated-prefix 與 fuzz reachability。

### Gherkin

```gherkin
Scenario: Auto 模式不拆散英文單字
  Given 一頁以獨立 glyph positioning 表示 "Artificial Intelligence"
  When 使用 Auto 模式抽取
  Then 結果包含 "Artificial Intelligence"
  And 結果不包含 "A r tificial"
  And 每個人工插入空白都計入 inserted_spaces

Scenario: Auto 模式不在連續中文字間插入空白
  Given 一頁以獨立 glyph positioning 表示 "台灣政府動畫宣導影片"
  When 使用 Auto 模式抽取
  Then 結果包含連續文字 "台灣政府動畫宣導影片"
  And 結果不包含 "台 灣 政 府"

Scenario: ActualText 取代 enclosed glyphs
  Given marked content 的 ActualText 是 "ffi"
  And enclosed glyph 是單一 ligature
  When 抽取文字
  Then 結果只包含一次 "ffi"

Scenario: 文件級解壓預算不可被重複 object lookup 繞過
  Given 多個 compressed objects 來自同一高壓縮 object stream
  When 逐一驗證所有 objects
  Then object stream 的 decode 結果可由 bounded cache 重用
  And 每次實際 decode 都計入 document-wide budget
  And 超過 budget 時回 limit_exceeded
```

## Stage gate

每個實作 stage 完成前必須執行：

```text
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo check -p pdf-wasm --target wasm32-unknown-unknown
```

涉及 dependency 或 release 的 stage 另執行 `cargo deny`、`cargo audit` 與 Rust 1.88 MSRV check。
任何 gate 失敗都不得進入下一 stage。

## 風險與決策

| 風險 | 對策 |
|---|---|
| Auto heuristic 對某類文件改善、另一類退化 | 保留 ContentOrder／Layout；golden corpus 分 script 與 producer |
| public DTO 增加欄位造成使用者反序列化失敗 | 使用 V2 API；legacy response 不增加破壞性必填欄位 |
| cache 降低 CPU 卻增加記憶體 | bytes／entries 雙上限；deterministic eviction；WASM 使用較小 profile |
| document budget 使原本可處理文件失敗 | 0.2.0 release note 說明；提供明示 limits；不可靜默放寬 |
| ActualText nested semantics 重複輸出 | stack-based suppression；巢狀 golden cases |
| 真實 PDF 無法公開 | private manifest + synthetic minimization，不提交原檔 |
| PDF 2.0 行為差異 | compatibility 標記 Partial；另做限定章節 normative research |

## 研究來源

- Adobe PDF Reference 1.7：
  <https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.7old.pdf>
- Adobe Accessibility：
  <https://opensource.adobe.com/dc-acrobat-sdk-docs/library/accessibility/index.html>
- Apache PDFBox `PDFTextStripper`：
  <https://pdfbox.apache.org/docs/2.0.3/javadocs/org/apache/pdfbox/text/PDFTextStripper.html>
- pypdf text extraction：
  <https://pypdf.readthedocs.io/en/stable/user/extract-text.html>
- pdfminer.six layout analysis：
  <https://pdfminersix.readthedocs.io/en/latest/topic/converting_pdf_to_text.html>
- Mozilla PDF.js：
  <https://github.com/mozilla/pdf.js>

以上來源查閱日期均為 2026-07-28。它們只作規格與測試設計參考，不成為 parser dependency。
