# Compatibility reference

Version／版本：`0.2.0`／Stage 11。此 reference 只描述已有 fixture、regression 或 private-corpus 證據的
行為，不代表完整 ISO 32000 或 PDF 2.0 normative conformance。

## Overview／概覽

`Supported` 表示已有成功與失敗回歸；`Partial` 表示只支援 Notes 的子集合；`Unsupported`
表示 caller 不可依賴。

| Area | Status | Notes |
|---|---|---|
| PDF header 1.x/2.x syntax | Partial | 接受單位數 `major.minor`；未宣告 PDF 2.0 normative conformance |
| Basic objects | Supported | null、bool、number、name、string、array、dictionary、reference |
| Literal/hex strings | Supported | nested parentheses、escape、octal、odd hex nibble |
| Direct/indirect stream length | Supported | cycle／depth bounded |
| Classic xref | Supported | subsection、free/in-use、incremental `/Prev` |
| Xref stream | Supported | `/W`、`/Index`、type 0/1/2、Flate/Predictor |
| Hybrid xref | Supported | classic trailer `/XRefStm` |
| Object stream | Supported | bounded `/N`、`/First`、validated ranges、document LRU |
| Filters | Partial | FlateDecode/Fl only；single-stream、ratio、document-lifetime limits |
| Predictors | Supported | TIFF 2；PNG 10–15；BPC 1/2/4/8/16 |
| Page tree | Supported | `/Kids` order、cycle/depth/page limits |
| Inherited page attributes | Partial | Resources、MediaBox、Rotate |
| Page contents | Supported | single stream、array、indirect streams |
| Content operators | Text-focused | text state、CTM/text matrix、inline image skip |
| Form XObject | Supported for text | nested resources/matrix/cycle protection |
| ToUnicode CMap | Partial | codespace、bfchar、bfrange、UTF-16BE；invalid/missing distinguished |
| Simple fonts | Partial | WinAnsi、MacRoman、Differences、glyph-name heuristics、Widths |
| Type0/composite fonts | Partial | ToUnicode authoritative；Identity fallback warned；W/DW supported |
| Vertical font metrics | Partial | writing mode retained；W2/DW2 not implemented |
| Marked content／ActualText | Partial | BMC/BDC/EMC、direct/named/indirect properties、nested replacement、MCID |
| Tagged structure tree | Unsupported | no structure-tree logical-order navigation |
| Legacy text layout | Approximate | 0.1.x coordinate sort／separator compatibility path |
| Auto text layout | Approximate | script/metrics/rotation aware；bounded ambiguity fallback |
| JPEG Image XObject | Supported extraction | original bytes retained；header dimensions validated |
| Flate image samples | Supported extraction | decoded raw samples + metadata |
| Other image codecs | Preserved only | encoded bytes + `unsupported_image_codec` warning |
| Encryption | Unsupported | password-protected input fails |
| Rendering/OCR/editing | Out of scope | intentionally absent |
| Damaged-file repair | Unsupported | no speculative xref reconstruction |

## Parameters／public API

| Front end | Legacy entry | V2 entry | V2 mode parameter |
|---|---|---|---|
| Rust | `PdfDocument::extract_text` | `PdfDocument::extract_text_v2` | `ExtractionMode` enum |
| CLI | `extract` without `--mode` | `extract --mode ...` | `content-order|layout|auto` |
| Python | `extract_text`／`extract` | `extract_v2` | `mode="..."` |
| WASM | `extractText`／`extract` | `extractWithOptions` | `{ mode: "..." }` |

共同 V2 options：

| Option | Default | Meaning |
|---|---|---|
| mode | `auto` in Rust/Python/WASM V2; CLI requires `--mode` | extraction order／separator policy |
| normalize Unicode | false | after decoding, apply NFC only when explicitly true |
| quality | true | include aggregate quality object; false omits it |

CLI 的 `--mode` 與 legacy `--no-layout` 互斥。Legacy `layout=false` 對應 ContentOrder，
`layout=true` 對應 legacy Layout；legacy calls 不會默默改成 Auto。

## Mode responses

| Mode | Ordering and separator contract |
|---|---|
| `content-order` | source ordinal，保留 explicit whitespace，不加入 geometry separator |
| `layout` | 0.1.x geometry compatibility path |
| `auto` | font advance、script、rotation／writing-mode group、line clustering；不確定時局部 source-order fallback |

V2 response 包含：

- `mode`、`text`、`pages`、`warnings`。
- `glyphs`：source ordinal、Unicode、text origin、font、writing mode、geometry、MCID。
- `separators`：插入位置與 explicit／geometry provenance。
- optional `quality`：`inserted_spaces`、`inserted_line_breaks`、`fallback_glyphs`、
  `replacement_characters`、`ambiguous_boundaries`。

## Decode 與 cache limits

Rust `ParseLimits::default()`：

| Field | Default | Scope |
|---|---:|---|
| `max_file_bytes` | 256 MiB | input allocation／parse |
| `max_object_depth` | 64 | object、reference、Form、marked-content nesting |
| `max_array_items` | 1,000,000 | single parsed array |
| `max_dictionary_entries` | 100,000 | single parsed dictionary |
| `max_string_bytes` | 64 MiB | single PDF string |
| `max_name_bytes` | 4 KiB | single PDF name |
| `max_xref_entries` | 5,000,000 | document xref entries |
| `max_incremental_updates` | 128 | `/Prev` revisions |
| `max_stream_bytes` | 128 MiB | single encoded/raw stream |
| `max_decoded_stream_bytes` | 256 MiB | single decoded stream or predictor output |
| `max_total_decoded_bytes` | 512 MiB | xref 起算、clone-shared document lifetime |
| `max_cached_object_stream_bytes` | 64 MiB | decoded ObjStm + validated range index weight |
| `max_cached_object_streams` | 256 | resident deterministic LRU entries |
| `max_filter_chain_depth` | 8 | filter stages |
| `max_stream_expansion_ratio` | 200 | ordinary stream per-stage heuristic |
| `max_pages` | 100,000 | page tree leaves |
| `max_content_operations` | 5,000,000 | parsed content operations |
| `max_cmap_mappings` | 2,000,000 | valid + invalid tracked CMap sources |
| `max_text_spans` | 5,000,000 | spans and positioned glyph vectors |
| `max_structure_elements` | 1,000,000 | StructElem traversal and tagged associations |
| `max_structure_kids` | 2,000,000 | K arrays, nested structure work, and ParentTree Kids |
| `max_parent_tree_entries` | 1,000,000 | ParentTree number-tree entries |
| `max_role_map_entries` | 100,000 | RoleMap entries before bounded chain resolution |
| `max_images` | 1,000,000 | extracted image XObjects |
| `max_image_pixels` | 250,000,000 | declared image pixels |

獨立 `decode_stream_with_limits` 沒有 `PdfDocument`，每次呼叫建立 local budget。只有經
`PdfDocument` 執行的 xref、object、page、text、font、image 路徑共享文件 lifetime budget。
cache hit 不增加 decoded bytes；miss 或 eviction 後重新 decode 會再次計量。

0.2.0 and the Stage 12 development line add cache and tagged-structure limit
fields. Rust callers using a complete `ParseLimits { ... }` literal must add the
new fields, or use `..ParseLimits::default()`; see [migration guide](migration-0.2.md).

## Diagnostics response

Rust 可呼叫 `PdfDocument::decode_metrics()`。CLI `validate --diagnostics --json` Example：

```json
{
  "ok": true,
  "validated_objects": 45151,
  "decode_metrics": {
    "decoded_bytes": 17644788,
    "decode_operations": 335,
    "object_stream_cache_hits": 33028,
    "object_stream_cache_misses": 334,
    "object_stream_cache_evictions": 78,
    "peak_object_stream_cache_bytes": 17155094,
    "peak_object_stream_cache_entries": 256
  }
}
```

metrics 是 diagnostics／test data，不會加入 text DTO，也不啟動 telemetry。plain CLI 只把
metrics 寫 stderr，避免污染產品 stdout。

## Responses／serialization

Rust serde、CLI JSON 與 Python JSON 使用 snake_case fields；`ExtractionMode` serialization 是
`content-order`／`layout`／`auto`。WASM options 使用 camelCase（`normalizeUnicode`），result DTO
仍由 core serde shape 產生。

Legacy response 沒有 `mode`、`glyphs`、`separators`、`quality`；這個 shape 在 0.2.0 保持。

## Errors／warnings

fatal error 都有 stable `code`、optional `offset` 與 `message`；limits 回 `limit_exceeded`，無效
mode 回 `invalid_option`。可回復 fidelity 狀況回 warning，不改變成功狀態。完整 code reference 見
[`errors.md`](errors.md)。

## Text fidelity contract

1. 有效 `/ActualText` 原子取代 enclosed sequence；nested 時外層優先。
2. 有效 ToUnicode 是一般 glyph 的主要 Unicode 來源。
3. 無效 UTF-16 destination 輸出 U+FFFD 並回 `unicode_mapping_invalid`。
4. source code 無 mapping 時輸出 U+FFFD 並回 `unicode_mapping_missing`。
5. 缺 ToUnicode 時可採明示 simple/composite fallback，必回 `font_fallback_encoding`。
6. `normalize_unicode` 預設 false；只有 caller 明確要求才套用 NFC。
7. Auto 可讀文字對 CJK radicals／compatibility ideographs做 compatibility decomposition；
   `glyphs[].unicode`、ContentOrder 與 legacy Layout 保存原值。
8. `/ActualText` 無 BOM 時目前只接受 ASCII 子集；其他 bytes 回 `actual_text_invalid` 並保留原文。
9. layout/Auto 是 extraction heuristic，不是 rendering output。

新增 filter、CMap operator、encoding 或 warning 時，必須同步更新本表、errors、golden tests 與
Stage 11 validation evidence。