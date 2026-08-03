# Text fidelity contract

版本範圍：`0.2.0`／Stage 11；Stage 11.0～11.7 已實作並完成 release validation。  
完整設計見 [Stage 11 技術規格](specs/stage-11/technical-spec.md)。

## Overview／適用範圍

本文件提供 Rust library、CLI、Python 與 browser WASM 共用的文字 fidelity reference。
Context 是不渲染頁面的唯讀 PDF 抽取；讀者可用它判斷各 mode 何時保留來源、何時加入閱讀
順序推論，以及如何驗證私有語料。實作證據以 `pdf-core` 測試與下列 stage gate 為準。

## 為什麼需要獨立契約

PDF content operations 的順序主要控制繪製，不保證等於閱讀順序。文字抽取因此必須把
Unicode 解碼、來源順序、幾何排序、空白推論與段落推論分開驗收，不能只以
「命令成功」或「字元數非零」代表文字正確。

## Stage 11 模式

| Mode | 契約 |
|---|---|
| `ContentOrder` | 依來源操作順序輸出，不加入 geometry-derived separator |
| `Layout` | 保留 0.1.x 的近似座標排序，作為 legacy compatibility |
| `Auto` | 使用 font metrics、script、rotation 與可用的 logical hints 改善閱讀文字 |

Stage 11.0 建立 contract 與 baseline；Stage 11.1 提供 V2 API 與 quality DTO；Stage 11.2
提供 positioned glyph、font advance 與 source ordinal；Stage 11.3 已啟用 script-aware `Auto`
separator、方向分組與 ambiguity fallback。

### 四端 V2 mapping

| Front end | V2 entry point | Mode |
|---|---|---|
| Rust | `PdfDocument::extract_text_v2` | `ExtractionMode` |
| CLI | `extract --mode` | `content-order|layout|auto` |
| Python | `rust_pdf.extract_v2` | mode string |
| Browser WASM | `extractWithOptions` | `{ mode, normalizeUnicode, quality }` |

四端對共同 fixture 鎖定相同 text、pages、warning codes 與五個 quality counters；legacy entry
points 維持既有 result shape，不含 `mode`、`glyphs`、`separators` 或 `quality`。

## Unicode precedence

1. 有效 `/ActualText`。
2. 有效 ToUnicode mapping。
3. 明示 font fallback，回 `font_fallback_encoding`。
4. mapping 存在但 destination 不是有效 UTF-16BE：輸出 U+FFFD，回 `unicode_mapping_invalid`。
5. source code 沒有 mapping：輸出 U+FFFD，回 `unicode_mapping_missing`。

無效 UTF-16 mapping 不反轉 surrogate、不從 glyph 外形猜 Unicode。invalid 與 missing source
都計入 `max_cmap_mappings`，並按 page／font／code class 聚合 warning，避免 amplification。結構錯誤
與資源超限維持 fatal error。

## Stage 11.2 geometry 與 font metrics

V2 structured result 的 `glyphs` 會保存每個 PDF character code 的 `source_ordinal`、Unicode、
text origin、font、writing mode、page-space origin／advance、normalized baseline 與 90 度
rotation bucket。`ContentOrder` 依 source ordinal 輸出；所有 glyph vector 受
`max_text_spans` 的文件級上限保護，非 finite 或退化 geometry 不進入排序。

目前支援的 metrics 子集合：

- Simple font：`FirstChar`、`Widths`、FontDescriptor `MissingWidth`；缺值回退 500 units。
- Type0/CIDFont：`DW` 與 `W` 的 array／range forms；缺值回退 1000 units。
- `Identity-V` 等 `-V` encoding 會保留 vertical writing mode；`W2`／`DW2` 尚未支援，
  vertical advance 暫使用 horizontal width magnitude 並明示為已知限制。
- advance 納入 font size、horizontal scaling、`Tc`、`Tw`、`TJ`、text matrix、CTM 與
  Form matrix。
- legacy spans 使用獨立 compatibility matrix，避免新 metrics 改變 0.1.x Layout golden。

## Stage 11.3 Auto layout

`Auto` 以 page-space glyph geometry 為輸入，先按 rotation bucket 與 writing mode 分組，再以
baseline normal distance 做 line clustering。每一行使用沿 baseline 的投影座標排序，
`source_ordinal` 是固定 tie-break，因此相同輸入可重現相同輸出。

Separator 規則如下：

- Latin／數字的 advance-normalized gap 超過門檻時插入 `GeometrySpace`。
- 連續 CJK glyph 不因一般幾何 gap 插入空白；明示 whitespace 會去重但不移除。
- line 與方向群組邊界使用 `GeometryLineBreak`，並累計 quality counters。
- vertical writing、嚴重 overlap 或疑似多欄時只回退該方向群組的 source order，整頁聚合一筆
  `reading_order_ambiguous` warning。
- CJK radicals／compatibility ideographs 在 `Auto` 可讀文字中依 Unicode compatibility
  decomposition 轉為統一漢字；V2 `glyphs[].unicode` 仍保存原始 ToUnicode 值，
  `ContentOrder` 與 legacy `Layout` 不改寫。

分組種類由 90 度 rotation bucket 與 writing mode 限定；主要成本是 group／line sort，單頁為
O(n log n)，沒有 glyph 對 glyph 的無界 O(n²) 掃描。固定 2,000 glyph harness 與兩份私有
PDF contract 都是 Stage 11.3 gate 的一部分。

## Stage 11.4 ActualText 與 marked content

`BMC`／`BDC`／`EMC` 使用受 `max_object_depth` 限制的 stack。`BDC` property list 支援 direct
dictionary、`/Properties` resource name 與 bounded indirect resolution；cycle 或錯誤型別會聚合
`actual_text_invalid` 並保留 enclosed text，只有資源上限錯誤是 fatal。

有效 `/ActualText` 在 `EMC` 時原子取代 enclosed glyph/span，nested 時外層 replacement 優先；
空字串會明確抑制內容。replacement 保存第一個 enclosed glyph 的 geometry，`TextOrigin` 為
`ActualText`，並保留最近的 `MCID`。缺少 `EMC` 時在 content/Form 邊界有界地隱式關閉並回
warning，不允許 stack 跨邊界無限成長。

目前 PDF text string 支援 UTF-16BE／UTF-16LE BOM 與無 BOM ASCII 子集；完整
PDFDocEncoding 尚未支援。兩份私有 PDF 分別鎖定 311 與 248 個 ActualText replacements。

## Stage 11.5 文件級 decode 與 cache

文字抽取涉及 xref、object stream、page/Form content 與 ToUnicode 等多條解壓路徑；現在全部由
同一 `PdfDocument` 的 monotonic `DecodeBudget` 管理，clone 不會重設預算。未 eviction 的
object stream 只解壓一次，後續 member resolution 使用 validated range cache；eviction 後重解壓
會再次計量。這不改寫文字或 glyph DTO，只改變惡意或超出 limits 的文件何時回
`limit_exceeded`。

cache 與 accounting 的完整威脅模型、預設值及 diagnostics 見
[`security.md`](security.md) 與 [`compatibility.md`](compatibility.md)。

## Stage 11.6 integration evidence

Stage 11.6 凍結功能後完成 synthetic、private corpus、cross-interface、exact-boundary、truncated
prefix、malformed nesting、warning amplification、fuzz、benchmark、MSRV 與 supply-chain gates。
兩份 private PDF 的 inspect／validate／三 modes 均成功；Auto required/forbidden fragments 通過。

完整 version、parameters、commands、responses、errors、toolchain/hardware 與未涵蓋項目位於
[`tests/fixtures/stage11/validation-matrix.md`](../tests/fixtures/stage11/validation-matrix.md)。
私有檔案不在 repository；未配置時 `SKIP` 不能宣稱 private gate passed。

## Design decisions and trade-offs／設計決策與取捨

- Decision：CJK compatibility decomposition 只套用到 `Auto` 的可讀文字，because 搜尋與索引需要
  統一漢字，但稽核仍需要原始 ToUnicode；因此 `glyphs[].unicode` 與 `ContentOrder` 不改寫。
- Decision：混合方向先分組再排版，不整頁回退。Trade-off 是跨方向群組只承諾 deterministic
  邊界並回 warning，不宣稱完整 logical order。
- Decision：疑似多欄或嚴重 overlap 時少插 separator。這會犧牲部分可讀性，但避免把不確定
  geometry 包裝成高信心文字。

## Stage 11.0 baseline

| Fixture | Content order | 目前 Layout | Auto 目標 |
|---|---|---|---|
| Latin positioned glyphs | `Artificial Intelligence Index Report 2026` | 每個字母間出現空白 | 恢復完整英文單字與詞間空白 |
| CJK positioned glyphs | `台灣政府動畫宣導影片` | 每個漢字間出現空白 | 連續 CJK 不插入一般 glyph space |

可執行 recipes 位於
`crates/pdf-core/tests/stage11_contract.rs`，golden 位於
`tests/fixtures/stage11/baseline-golden.toml`。

## Private corpus

兩份實際 PDF 不提交 repository。`tests/real-world/manifest.toml.example` 保存：

- 經驗證的 SHA-256。
- PDF 版本、頁數、使用中物件數。
- Stage 11.0 的 Layout／ContentOrder baseline。
- Stage 11 Auto required／forbidden fragments。
- 預期 warning codes。

未配置 corpus 時測試必須明確 `SKIP`；配置後 hash 不符必須失敗。詳細設定見
[`tests/real-world/README.md`](../tests/real-world/README.md)。

## Limitations／已知限制

- legacy `Layout` 的多餘空白是刻意保留的 0.1.x compatibility artifact；新使用者應選 `Auto`。
- Auto 不執行 rendering、OCR 或 tagged structure-tree navigation；多欄、重疊、混合方向只承諾
  deterministic bounded fallback，不承諾完整 logical reading order。
- vertical writing 保留 writing mode，但 `W2`／`DW2` 尚未支援。
- `/ActualText` 無 BOM 時只支援 ASCII 子集；完整 PDFDocEncoding 尚未實作。
- 私有 corpus 未配置時，public gate 只能回報 `SKIP`，不能宣稱實檔已通過。
- PDF 2.0 normative conformance、encryption、repair 與新增 codecs 不在 0.2.0 範圍。
