# Documa metadata 與後續篩選設計

本文適合要整合 Documa 搜尋、RAG、MCP 或 agent workflow 的開發者，說明 Documa metadata 的分層方式、可提供的篩選訊號，以及目前公開搜尋介面與尚未公開為硬篩選條件的邊界。

適用範圍：Documa 0.6.4、DocumentIR 0.2。

## 概覽

Documa 將跨 parser 的穩定事實、pipeline 擴充資訊與可重建的搜尋訊號分開保存。這項設計決策（design decision）的理由是：原始文件與證據定位必須長期穩定，而檢索演算法與 provider 會持續演進。In short, metadata is layered because source truth and retrieval hints have different compatibility and lifecycle requirements.

### 為什麼需要 metadata 分層

Documa 的 metadata 不是單一平面字典，而是讓系統在載入完整內文前，先完成以下工作：

1. 定位文件與章節。
2. 篩選、排序與去重候選區塊。
3. 判斷證據類型、來源品質與預估讀取成本。
4. 精讀選中的原文，並回到頁面或結構位置產生引用。
5. 驗證索引是否仍對應目前的來源與處理版本。

這構成 Documa 的 progressive disclosure 路徑：**先讀 navigation metadata，再讀必要證據，而不是先把全文送入模型**。

## 分層資訊

| 層級 | 主要資訊 | 可支援的篩選或判斷 |
| --- | --- | --- |
| 文件層 | `document_id`、`source_name`、parser／adapter／pipeline 版本、頁數、引用型態 | 限定文件、確認來源格式、判斷索引是否需要更新 |
| 結構層 | `block_type`、`title`、`parent_id`、`child_ids`、`depth`、`order_index` | 限定 section／leaf、限制章節子樹、維持閱讀順序 |
| 語意層 | `keyword_terms`、`new_word_terms`、`search_terms`、詞頻與子節點支持度 | 關鍵詞召回、專有詞發現、相關性排序 |
| 來源定位層 | `page_refs`、`bbox_refs`、`source_block_ids`、`source_range`、`heading_path` | 限定頁面或章節、回到原始位置、產生引用 |
| 內容品質層 | `confidence`、OCR origin／engine／confidence、reading-order trace | 區分 OCR 與原生文字、檢查抽取與閱讀順序品質 |
| 檢索衍生層 | `doc_region`、`answer_tags`、`flags`、coverage、`dedupe_key`、讀取成本 | 降低非正文雜訊、找數字／日期／表格、去重與控制 context |

## 穩定欄位與開放 metadata

Documa 將跨 parser、需要穩定相容的資訊放在明確型別欄位：

- `DocumentIR`：文件識別、來源名稱、IR／producer／adapter 版本及 pipeline profile。
- `DocumentBlockIR`：區塊類型、階層、頁面、座標、內容雜湊與 confidence。
- `BlockIR`／`SpanIR`：來源版面 block、字型、樣式、語言與幾何座標。
- `TextContent`：同時保存 `raw_text` 與 `normalized_text`，正規化文字不會覆蓋原文。

容易隨 adapter 或 pipeline 擴充的資訊放在各物件的開放 `metadata` dict，例如 OCR trace、閱讀順序、關鍵詞 provider 或表格抽取策略。公開後需要穩定依賴的 metadata key 會另列入相容性契約；新增 optional IR 欄位則遵循 `ir_version` 的 additive minor-version 規則。

## 結構與導航 metadata

每個 `DocumentBlockIR` 可提供：

- `type`：`document`、`section`、`page`、`paragraph`、`table`、`image`、`footnote`、`table_of_content`、`metadata` 等。
- `parent_id`／`child_ids`：文件樹關係。
- `depth`／`order_index`：階層深度與閱讀順序。
- `title`／`text_preview`：不讀完整 body 時的候選摘要。
- `source_block_ids`／`source_chunk_ids`：回溯來源證據。
- `page_refs`／`bbox_refs`：頁面與幾何位置。
- `content_hash`：內容識別、去重與變更判斷。
- `confidence`：抽取或推論信心。

目前單文件搜尋可用 `scope_block_id` 限制到某個 block 及其後代，並用 `granularity=section|leaf|mixed` 選擇結構層級。當 section 命中時，agent 可先列出子節點，而不是直接載入整個章節。

## 關鍵詞與檢索 metadata

`BlockKeywordExtractionStage` 會替文件區塊產生：

- `keyword_terms`：主要關鍵詞。
- `new_word_terms`：依詞頻、跨子節點支持度與 CJK 左右鄰接熵發現的候選新詞。
- `search_terms`：標題、關鍵詞與新詞的去重集合。
- `keyword_provider`：實際使用的 `lingxi` 或 `ngram` provider。
- `keyword_provider_requested`、provider 版本與 fallback 原因。
- `keyword_thresholds`：最低支持度、最低詞頻、leaf 數量、文字長度與聚合策略。
- `keyword_stats`：`term_freq`、`child_support` 及 provider score 等診斷資訊。

關鍵詞採 bottom-up aggregation。文字葉節點可由 LingXi 選詞；祖先節點聚合子節點統計，避免相同證據在每一層階層重複成為高排名命中。

Collection index 使用 SQLite FTS5。BM25 欄位權重為：

| 欄位 | 權重 |
| --- | ---: |
| title | 4 |
| keywords | 3 |
| heading path | 2 |
| preview | 1.5 |
| body | 1 |

因此標題與關鍵詞命中會優先於只在長篇 body 中出現的同一個詞；IDF、詞頻飽和與 body 長度正規化也會降低高頻詞或長區塊的不當優勢。

## 文件區域與證據型態

Documa 會從 heading path、title、block type、來源 block type 與 role 推導 `doc_region`：

- `body`
- `toc`
- `header_footer`
- `footnote`
- `references`
- `metadata`
- `appendix`

非正文區域不會被完全排除，以保留明確搜尋目錄或參考文獻的能力；一般搜尋則會降權：

| 區域 | 分數乘數 |
| --- | ---: |
| TOC | 0.3 |
| header／footer | 0.3 |
| footnote | 0.45 |
| references | 0.6 |
| metadata | 0.6 |
| body／appendix | 1.0 |

搜尋時還會依候選內容衍生 selection metadata：

- `answer_tags`：`definition`、`trend`、`comparison`、`cause`、`numeric`、`date`、`table`。
- `flags.has_numeric`、`has_date`、`has_table`。
- `flags.is_reference`、`is_header_footer`。
- `neighbors.prev`／`next` 及 `needs_next`。
- `char_count`、精確 token counter 可用時的 `token_estimate`。
- `recommended_read_chars`。
- `dedupe_key`。

`nav` response profile 只回傳導航所需的精簡欄位；`evidence` 會增加引用與 selection metadata；`debug` 再增加命中欄位、new words 與去重診斷。

## 來源定位與引用

`page_refs` 不假設所有格式都有 PDF 頁碼。Documa 依來源模型建立引用：

- PDF：`PDF p.12`，可同時保存 printed page label 或 PDF page label。
- PPTX：`Slide 5`。
- XLS／XLSX：`Worksheet "收入表"`。
- DOCX：structural document location。

相關資訊包括：

- `page_ref_kind`
- `printed_page_label`
- `pdf_page_label`
- `citation_label`
- `bbox_refs`

搜尋結果只需要帶足以導航與讀取的識別；實際 bbox 等較重資訊可在 citation 工具階段取得，避免每筆搜尋命中重複傳輸。

## 去重、索引新鮮度與可重建 sidecar

`content_hash`／`dedupe_key` 用於：

- 抑制同一內容在父章節與子段落重複出現。
- 抑制跨文件完全相同的命中。
- 判斷 ingest 是否需要更新索引。
- 避免從過期 collection index 讀取證據。

單文件 search sidecar 另外記錄：

- `source_digest`
- `document_id`／`ir_version`
- `feature_version`
- `normalizer_version`
- `tokenizer_version`
- `keyword_provider_signature`
- ANN vector／dimension／construction 版本

來源內容、IR、normalizer、tokenizer 或 keyword provider 改變後，既有 sidecar 會被判定為 stale 或重新建立。Sidecar 與 SQLite index 都是可刪除後重建的 derived data，不是原始文件事實來源。

## 目前公開的搜尋收斂參數

公開搜尋介面目前可直接用下列參數收斂候選或改變結果組織方式：

| 參數 | 適用路徑 | 作用 |
| --- | --- | --- |
| `document_ids` | Collection | 將搜尋限定在指定的 registry document IDs |
| `scope_block_id` | 單文件 | 將搜尋限定在某個 block 及其後代 |
| `granularity` | 單文件 | 選擇 `section`、`leaf`、`mixed`，或交由 `auto` 決定 |
| `search_body` | 單文件 | 決定是否將完整正文納入搜尋欄位 |
| `query` | 兩者 | 提供主要 lexical query units 與 quoted phrases；單文件以 coverage 參與排序，Collection 優先嘗試全詞命中，必要時明示降級為 any-term recall |
| `any_of` | 單文件 | 補充非重複的同義詞、拼法或雙語變體，以 OR 方式擴充召回 |
| `group_by_document` | Collection | 將 block hits 組織成「哪些文件有命中」的文件層級彙總 |
| `limit`／`offset` | 兩者 | 控制結果分頁 |
| `per_document_limit` | Collection | 限制單一文件佔用的 block 結果數量 |

Collection registry 只會將 active 文件納入正常索引與搜尋；搜尋後也會以目前 registry 的 `content_hash` 排除 stale rows。

## 設計取捨

- **明確欄位與開放 dict 並存**：明確欄位提供跨 parser 的穩定契約；開放 metadata 允許新增 OCR、reading order 或 provider trace。代價是 consumer 必須區分已承諾的 key 與內部診斷資訊。
- **非正文降權而非排除**：可以搜尋 TOC、footnote 或 references，但一般正文查詢可能仍看見低排名的非正文結果。
- **搜尋結果保持精簡**：bbox、完整 selection diagnostics 等資訊延後到 evidence／debug 或 citation 階段取得。這減少 token 傳輸，但需要額外一次有界讀取或引用工具呼叫。
- **索引是 derived data**：sidecar 與 collection index 可以重建，也必須在來源或處理版本改變時失效。這提高證據安全性，但 provider／normalizer 升級可能觸發重建成本。
- **metadata 先支援軟排序**：先驗證訊號的實際檢索價值，再決定是否升級成 public hard filter，可避免過早固定不成熟的 API；代價是部分已有欄位目前仍需由 agent 在結果層判斷。

## 已知限制：尚未成為硬篩選的資訊

下列資訊目前主要參與排序、引用或回傳給 agent 判斷，尚未形成通用的 public filter predicates：

- `block_type`
- page／slide／worksheet range
- `doc_region`
- `answer_tags`
- confidence threshold
- `origin=ocr|native`
- language
- `has_table`／`has_numeric`／`has_date`
- 任意 `metadata_equals`

因此，Documa 已具備這些後續篩選需要的資料基礎，但不能把它們描述成目前已公開的硬篩選 API。若擴充介面，較穩定的方向是新增結構化 `filters` 物件，並明確區分：

1. **硬篩選**：候選不符合條件就不進入排名。
2. **軟排序**：保留候選，但依 metadata 加權或降權。
3. **回傳投影**：只影響 `nav`／`evidence`／`debug` 回應中呈現哪些資訊。

## 相關實作與文件

- IR models：[`src/documa/core/ir.py`](../../src/documa/core/ir.py)
- IR 相容性契約：[`docs/spec/ir-compatibility.md`](../spec/ir-compatibility.md)
- 關鍵詞 metadata：[`src/documa/pipeline/block_keywords.py`](../../src/documa/pipeline/block_keywords.py)
- 文件區域規則：[`src/documa/core/doc_regions.py`](../../src/documa/core/doc_regions.py)
- 頁面引用：[`src/documa/pipeline/page_refs.py`](../../src/documa/pipeline/page_refs.py)
- 單文件搜尋與 selection metadata：[`src/documa/interfaces/tools.py`](../../src/documa/interfaces/tools.py)
- 單文件 sidecar：[`src/documa/search/sidecar.py`](../../src/documa/search/sidecar.py)
- Collection index：[`src/documa/collections/sqlite_index.py`](../../src/documa/collections/sqlite_index.py)

## 維護注意事項

當 metadata 或篩選能力變更時，至少同步檢查：

1. `DocumentIR` dataclass 與產生的 JSON schema 是否一致。
2. metadata key 是否已有公開相容性承諾。
3. 單文件與 collection ranking 是否使用一致的 doc-region／去重規則。
4. CLI、MCP 與 function-calling schema 是否公開相同參數。
5. sidecar digest 或 index version 是否需要更新，以免沿用舊衍生資料。
6. `nav` 預設回應是否仍維持精簡，新增資訊是否只在 `evidence`／`debug` 出現。
