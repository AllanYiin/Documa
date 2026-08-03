# Error code reference

## Overview／概覽

Version／版本：`0.2.0`／Stage 11。Public fatal errors always contain a stable `code`, optional
input byte `offset`, and human-readable `message`. Successful text/image results may also contain
stable warnings when output is usable but fidelity is reduced.

## Parameters／欄位

Fatal error object：

| Field | Type | Meaning |
|---|---|---|
| `code` | string | Stable machine-readable discriminator |
| `offset` | integer or null | Best-known byte position in original input |
| `message` | string | Human-readable diagnostic; never an API discriminator |

Text warning：

| Field | Type | Meaning |
|---|---|---|
| `code` | string | Stable recoverable-condition discriminator |
| `page_index` | integer | Zero-based page index |
| `font_resource` | string or null | Font resource when applicable |
| `message` | string | Human-readable context |

Callers must branch on `code`, not `message`.

## Responses／front-end shape

Example fatal JSON：

```json
{"code":"invalid_header","offset":0,"message":"PDF header is missing"}
```

- Rust：`PdfError { code: ErrorCode, offset, message }`。
- CLI：stderr 外層為 `{"ok":false,"error":{...}}`，並回非零 exit code。
- Python：`PdfParseError` message 是同三欄位的 JSON string。
- WASM：rejection value 是同三欄位的 JavaScript object。

CLI exit classes：I/O = 3、limit = 4、unsupported feature = 5、其他 parser/option error = 2。

## Errors／fatal codes

| Code | Meaning |
|---|---|
| `unexpected_eof` | Input ended inside a required construct |
| `invalid_token` | Lexical token is malformed |
| `invalid_option` | Front-end option name, value, type, or combination is invalid |
| `invalid_object` | Object, page, font, CMap, marked-content, or content structure is malformed |
| `invalid_reference` | Reference is invalid, cyclic, or exceeds bounded resolution |
| `limit_exceeded` | A configured resource budget was exceeded |
| `invalid_string` | Literal/PDF string syntax or required encoding is malformed |
| `invalid_hex` | Hex string or escape is malformed |
| `invalid_stream` | Stream dictionary, range, filter data, or decoded payload is malformed |
| `invalid_header` | PDF header is absent or malformed |
| `invalid_startxref` | Final startxref marker or offset is invalid |
| `invalid_xref` | Classic, stream, hybrid, or Prev xref data is invalid |
| `invalid_trailer` | Trailer keys or values are invalid |
| `object_not_found` | Requested object is absent, free, or has another generation |
| `object_id_mismatch` | Xref and declared/compressed object identifiers disagree |
| `unsupported_feature` | Valid PDF feature is outside the compatibility matrix |
| `io` | CLI-side metadata/read operation failed |

`serialization_error` is a binding-side failure code used if Python/WASM/CLI cannot serialize an
otherwise successful DTO; it is not a PDF parser `ErrorCode` variant.

## Warnings／text fidelity

Warnings do not change success status. Repeated glyph-level conditions are bounded and aggregated by
page／code／font or condition class to prevent warning amplification.

| Code | Meaning／recovery |
|---|---|
| `actual_text_invalid` | property list、ActualText encoding 或 marked-content nesting 無效；保留 enclosed text，或在 missing EMC 時有界地隱式關閉 |
| `marked_content_invalid` | MCID, Alt, or non-ActualText marked-content properties are invalid; preserve visible text |
| `tagged_structure_invalid` | Optional StructTreeRoot, element, kid, page, role, or structure string is malformed; skip only the invalid association |
| `tagged_structure_cycle` | A structure, RoleMap, or ParentTree cycle was detected and bounded |
| `tagged_mcid_missing` | A structure MCID has no collected page-content node |
| `tagged_mcid_ambiguous` | An MCID has duplicate structure or content associations; keep deterministic first order and aggregate the warning |
| `parent_tree_mismatch` | ParentTree does not confirm the page, MCID slot, or owning structure element |
| `tagged_object_reference_unsupported` | OBJR or stream-associated MCR is deferred; visible source text remains available |
| `font_not_found` | content 使用不存在的 font resource；相關字元無法正常 decode |
| `font_fallback_encoding` | 缺 ToUnicode，使用明示 simple/composite fallback；結果需視為近似 |
| `unicode_mapping_invalid` | ToUnicode destination 不是有效 UTF-16BE；輸出 U+FFFD |
| `unicode_mapping_missing` | 使用到沒有 mapping 的 source code；輸出 U+FFFD |
| `reading_order_ambiguous` | rotation、overlap、vertical/multi-column 或 XY-cut depth 邊界無法可靠排序；局部回退 deterministic order |
| `page_furniture_ambiguous` | margin text repetition or page-label evidence is insufficient; preserve it in inferred order and main flow |
| `vector_path_invalid` | optional path syntax or graphics state is unusable for table detection; preserve extracted text and omit vector tables |
| `tagged_table_invalid` | Table/TR/TH/TD hierarchy, attributes, page ownership, or span topology is invalid; preserve source nodes and omit only the table |
| `table_detection_ambiguous` | geometry or text alignment is insufficient for safe table acceptance; preserve ordinary text |
| `table_evidence_conflict` | tagged, vector, or text evidence materially conflicts; retain higher-precedence evidence |
| `table_cell_unassigned` | a logical cell has no assignable collected node; retain the cell with honest optional geometry |
| `table_cell_overlap` | span placement or accepted cell geometry overlaps; reject or retain higher-precedence topology |

`unicode_mapping_invalid` 與 `unicode_mapping_missing` 是不同契約：前者表示 mapping 存在但
Unicode 無效，後者表示 source code 根本沒有 mapping。不要把兩者合併統計。

## Warnings／image extraction

| Code | Meaning／recovery |
|---|---|
| `unsupported_image_codec` | codec 未解碼；保留 encoded bytes 與 metadata |
| `jpeg_dimension_mismatch` | JPEG header dimensions 與 PDF dictionary 不一致；保留資料並揭露 warning |
| `raw_sample_length_mismatch` | decoded raw sample length 與 dimensions/components/BPC 不一致 |

## Error handling example

Rust caller 依 enum 分支：

```rust
use pdf_core::{ErrorCode, PdfDocument};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    match PdfDocument::parse(b"not a PDF") {
        Err(error) if error.code == ErrorCode::InvalidHeader => {
            eprintln!("rejected malformed header at {:?}", error.offset);
        }
        Err(error) => return Err(error.into()),
        Ok(_) => unreachable!("fixture is intentionally invalid"),
    }
    Ok(())
}
```

`limit_exceeded`、`invalid_xref` 或 `unsupported_feature` 不應以重試同一 bytes 的方式處理；
只有可信 caller 才可在理解風險後調整 Rust `ParseLimits`。Warnings 應隨 text/image result 保存，
而不是轉成 fatal error 或靜默丟棄。