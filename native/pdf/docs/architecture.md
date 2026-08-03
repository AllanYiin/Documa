# Architecture

本文是 explanation，適用於維護 parser core、資源治理或新增 front-end binding 的工程師。
版本範圍：`0.2.0`／Stage 11。

## Overview／概覽

核心 decision 是由一份 safe Rust parser 支援 Rust、CLI、Python 與 browser WASM，並把
Unicode text extraction 視為主要資料產品。這個 context 決定所有 PDF-aware 規則只能存在
`crates/pdf-core`；bindings 不得建立第二套 object、font 或 layout parser。

## Why／為什麼採單一核心

because xref、page inheritance、font decoding、ActualText、layout 與 resource limits 彼此耦合，
若 front end 各自解析，會產生不同文字、安全邊界與 error code。單一核心讓相同 bytes 與 options
在四種介面得到相同 text、pages、warnings 與 quality metadata。

## 邊界

`crates/pdf-core` 是唯一知道 PDF 語法的 crate。CLI、Python 與 WASM 只可：

1. 接收 bytes、mode 與 options。
2. 呼叫 core public API。
3. 轉換 DTO、錯誤與輸出格式。

bindings 不可自行解析 object、xref、page、font、marked content 或 content operator。
`pdf-core` 保持 `#![forbid(unsafe_code)]`，不依賴既有 PDF parser 或 native PDF engine。

## 資料流程

```text
PDF bytes
  -> header + startxref
  -> classic/xref-stream chain + document DecodeBudget
  -> lazy indirect object resolution
  -> bounded object-stream expansion / deterministic LRU cache
  -> page tree + inherited resources
  -> bounded filter decoding
  -> content operations + text/Form matrices
  -> font encoding + ToUnicode + metrics
  -> bounded marked-content stack + ActualText replacement
  -> positioned glyphs + source ordinals
  -> ContentOrder | legacy Layout | script-aware Auto
  -> PageGeometry projection -> versioned Layout IR
  -> pages/spans/glyphs/separators/warnings/quality
```

影像走獨立支線：page/Form resources → Image XObject → codec metadata validation → 原始 encoded
bytes 或 decoded raw samples。這條支線不渲染頁面。

## 主要模組

- `lexer.rs`：byte-preserving tokenizer。
- `parser.rs`：direct／indirect object 與 stream boundary。
- `xref.rs`：classic、xref stream、hybrid 與 incremental chain。
- `object_stream.rs`、`object_stream_cache.rs`：type 2 entry、validated member ranges 與 LRU。
- `decode_budget.rs`、`filter.rs`：文件級 decoded-byte accounting、Flate 與 Predictor。
- `page.rs`：page tree、inherited attributes、content stream 串接。
- `content.rs`：content operands／operators 與 inline image isolation。
- `cmap.rs`、`font.rs`、`font_metrics.rs`：Unicode precedence、fallback 與 glyph advance。
- `marked_content.rs`：property list、ActualText 與 MCID。
- `text_model.rs`：positioned glyph、origin、writing mode 與 separator provenance。
- `layout.rs`、`text.rs`：三 modes、Form traversal、Auto reconstruction、warnings 與 quality。
- `geometry.rs`: canonical PDF, layout, and display coordinate transforms.
- `layout_ir.rs`: versioned block/span DTO, provenance, capabilities, and explicit orders.
- `reading_order.rs`: bounded line clustering, recursive XY-cut, paragraph/list grouping, and page furniture.
- `tagged_structure.rs`, `table_reconstruction.rs`: bounded author table hierarchy, span placement, and source-node mapping.
- `images.rs`：Image XObject extraction 與 codec validation。
- `limits.rs`：所有 public input-derived resource bounds。

## 三種文字路徑

`ContentOrder` 依 `source_ordinal` 輸出，僅保留明示 Unicode whitespace。`Layout` 使用獨立的
legacy compatibility matrix，以維持 0.1.x 結果。`Auto` 使用 page-space advance、rotation bucket、
writing mode 與 script-aware separator；遇到多欄、嚴重 overlap 或方向不確定時，只回退受影響群組
的 source order 並聚合 `reading_order_ambiguous`。

`/ActualText` 在 glyph layout 前處理：有效 replacement 原子取代 enclosed sequence；nested 時外層
優先。原始 ToUnicode 仍保存在未被取代的 `PositionedGlyph`，Auto 的可讀文字與 provenance 分離，
因此 caller 可同時取得可搜尋文字與稽核資料。

## 文件 runtime 與同步模型

`PdfDocument::parse_with_limits` 在讀取第一個 xref stream 前建立 `DecodeBudget`。xref chain 完成後，
budget 與空的 `ObjectStreamCache` 一起放入 `Arc<Mutex<DocumentRuntime>>`；因此
`PdfDocument::clone` 只複製 handle，不會重設 limits、budget 或 cache。

一般 stream decode 在持有 runtime lock 時執行並立即計量。compressed-object resolution 先在 lock
外解析並驗證 container dictionary，再以 `(ObjectId, revision offset)` 查 cache；cache miss 的解壓、
header/range validation、insert 與 member parse 在同一同步區段完成。這可避免並行 caller 對同一
miss 重複解壓，也不讓已 eviction 的 entry 留在 accounting 之外。core 不建立 worker thread。

LRU 以單調 access counter 排序，tie-break 使用 cache key，不讀取 wall clock。cache weight 是
`decoded.len + member_count * size_of(CachedObjectMember)`；entries、bytes、單一 decoded stream
與文件總 decoded bytes 各有獨立 limit。

## Error 與 recovery

Fatal parser errors 使用 `ErrorCode`，包含 stable `code`、optional input `offset` 與 human-readable
`message`。程式不得依賴 message 字串分支。strict parser 不掃描整份檔案猜測損毀 xref，避免把
stream bytes 誤認為 object boundary。

可產出內容但可能降低 fidelity 的狀況使用聚合 warning，例如 `unicode_mapping_invalid` 或
`reading_order_ambiguous`。Warning 不等於完整支援，caller 應保存 code 與 page context。

## 資源限制

`ParseLimits` 約束 file bytes、nested depth、collections、xref、page、content operations、filter
chain、單一與文件總 decoded bytes、object-stream cache、CMap mappings、text spans／glyphs、image
count 與 pixels。所有 input-derived offset、length、index、加法與乘法都必須 checked。

新增 parser feature 時必須：

1. 把 PDF-aware 邏輯放在 core。
2. 指定既有或新增的 bounded resource。
3. 加入 valid、invalid、exact-boundary 與 truncated-prefix regression。
4. 讓至少一個 fuzz target 可觸達新路徑。
5. 更新 compatibility、errors、安全與 release evidence。

## Trade-offs／取捨

strict xref、顯式 warnings 與 bounded ambiguity fallback 的 decision 犧牲 damaged-file repair 率與
部分複雜頁面的流暢文字，換取 deterministic、可稽核、可限制的結果。不做 rendering 可降低範圍
與 native 依賴，但 Auto 仍只是 extraction heuristic，不能代表像素級版面或完整 tagged logical order。

document runtime mutex 犧牲同一文件的平行 decode，換取 clone 無法繞過 budget。bounded LRU 可能
淘汰稍後仍要用的 ObjStm；重解壓會再次計量，而不是讓 cache 無界增長。

## Limitations／限制

0.2.0 不處理 encryption/password、page rendering、OCR、editing、damaged-xref repair、完整 tagged
structure-tree navigation，以及 Flate 之外的大多數 filters/codecs。parser 能讀取符合已支援 syntax
的 `%PDF-2.x` header 不等於 PDF 2.0 normative conformance。完整矩陣以
[`compatibility.md`](compatibility.md) 為準。

## Stage 12 Layout IR extension

Stage 1A establishes `PdfUserSpace`, unrotated top-left `LayoutSpace`, and
rotation-applied `DisplaySpace`. Stage 1B projects text through `PageGeometry`
into schema version 1. The four order arrays are independent: source order is
always complete; Stage 2 populates tagged order only from validated structure-tree
associations. Stage 3 independently populates inferred order and main flow from
LayoutSpace geometry, preserving but excluding confirmed furniture and Artifact
only from main flow.

The core owns projection, span merging, IDs, provenance, confidence, and stable
warnings. CLI, Python, and WASM serialize or convert the shared DTO only. Default
serialization omits timings and debug glyphs so identical bytes and options are
deterministic. Native positioned glyphs remain in PDF user space for legacy
compatibility and must not be mixed with Layout IR geometry.

The complete Stage 1B private corpus serializes to about 271.9 MB. Consumers
should avoid retaining both JSON bytes and decoded object graphs for an entire
large document. A page-level or streaming binding is a later integration concern;
it must preserve this schema and keep all PDF-aware decisions in `pdf-core`.

Stage 2 keeps all PDF-aware tagged logic in `pdf-core`. Marked-content tags,
MCIDs, Alt, ActualText metadata, and inherited Artifact state travel internally
with positioned glyphs. `tagged_structure` traverses bounded StructTreeRoot/K/Pg,
RoleMap, and ParentTree Nums/Kids data, then joins associations through a per-page
MCID index. ParentTree arrays are resolved once per page, avoiding quadratic
association scans. Malformed optional structure preserves visible source text and
emits stable aggregated warnings; structure limit breaches remain fatal.

Stage 3 flattens spans without losing marked-content metadata, clusters horizontal
lines, applies a depth-bounded deterministic XY-cut, groups paragraphs and list
items, then classifies repeated margin furniture across the document. Rotated or
vertical text and exhausted recursion use warned deterministic fallback. Source,
tagged, inferred, and main-flow orders remain independent; bindings serialize the
shared result without layout logic.
Stage 4 retains Table/TR/TH/TD ancestry and table attributes during structure
tree traversal, places RowSpan/ColSpan into a bounded rectangular grid, and maps
cell MCIDs through a per-page node index. It also collects stroke-painted paths
with bounded graphics/Form state for vector lattices and applies conservative
row/alignment rules for borderless tables. Text and vector analysis share each
page's parsed top-level operations and release them per page.

Tagged topology has precedence. A compatible vector lattice refines LayoutSpace
BBoxes and produces fused evidence without replacing spans or header roles; an
overlapping incompatible topology preserves the tagged result and emits a stable
conflict warning. Accepted tables remain additive to semantic nodes and all four
orders. An empty logical tagged cell uses absent geometry rather than a fabricated
box. CLI, Python, and WASM expose the same shared Rust DTO.
Stage 5A extends the same per-page bounded graphics/Form traversal to collect every
painted Image XObject occurrence without decoding pixels. The active `q/Q/cm` CTM
and nested Form matrices transform the PDF image unit square before exactly one
LayoutSpace projection. Quads retain object-corner identity while BBoxes are only
their normalized envelope; CropBox/UserUnit apply and page Rotate remains unapplied.
Repeated paints retain page-local paint ordinals even when an invalid occurrence is
skipped. Optional geometry failures emit `image_placement_invalid`; bounds remain
fatal. CLI, Python, and WASM serialize the shared core DTO without PDF logic.
Stage 5B carries marked-content Figure/MCID/Alt/Artifact state into each image
occurrence and joins it to validated StructTree associations. Author Figure and
Caption evidence outranks geometry. The geometry fallback accepts only explicit
caption-like text within a fixed LayoutSpace gap and horizontal overlap, excluding
table-owned nodes, repeated furniture, and artifacts. Ambiguity stays unlinked and
warned. Links are additive `source_node_ids`; semantic nodes and the source,
tagged, inferred, and main-flow arrays are unchanged.

Stage 5C performs bounded page-annotation, destination dictionary/name-tree, and
outline traversal in `navigation`. Link rectangles and QuadPoints pass through the
same `PageGeometry` exactly once. Typed targets retain URI or GoTo metadata; unsafe
or unknown actions are descriptive data only and are never executed. Malformed
optional entries warn without suppressing text or later valid entries, while
annotation, destination, outline, and depth limits fail closed. Stage 5D confirms
that all front ends expose the same DTO and carries the 29.0143% serialized-size
increase into Stage 6 as a page-level or streaming integration constraint.
