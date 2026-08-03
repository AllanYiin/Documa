# Migrate from 0.1.x to 0.2.0

## Summary

0.2.0 新增 Stage 11 V2 text extraction、ActualText、quality metadata、document DecodeBudget 與 bounded
object-stream cache。Legacy Rust、CLI、Python 與 WASM entry points 保持 signature 與 output shape；
要取得新的 script-aware text，caller 必須明示選用 Auto。

## Who should upgrade

- 需要改善英文拆字或 CJK 多餘空白的 text-indexing/search pipeline。
- 需要 source order、legacy layout 與 Auto 可明確切換的整合者。
- 需要 ActualText、positioned glyph、separator provenance 或 quality counters 的 caller。
- 解析不可信大型 PDF，需 document-lifetime decoded-byte budget 與 bounded ObjStm cache 的服務。

## Breaking changes

### Rust `ParseLimits` complete literals

0.2.0 新增：

```rust
max_cached_object_stream_bytes: usize,
max_cached_object_streams: usize,
```

完整 struct literal 需要補欄位。建議使用 `..ParseLimits::default()`，讓後續新增 bounded field 時
不必修改每個 caller。

### Warning code set

`unicode_mapping_invalid` 現在和 `unicode_mapping_missing` 分開。若 downstream 對 warning codes 做
exhaustive allowlist，必須加入新 code。Warnings 仍是 successful result，不應改成 fatal exception。

### Version metadata

`version_info()` 現在回 `("0.2.0", "stage-11")` 的等價資料。若監控系統硬編碼 `stage-10`，需更新。

## Compatibility

| Surface | 0.1.x call | 0.2.0 status | New V2 call |
|---|---|---|---|
| Rust | `extract_text(TextExtractionOptions)` | unchanged | `extract_text_v2(TextExtractionOptionsV2)` |
| CLI | `extract [--no-layout]` | unchanged | `extract --mode auto|layout|content-order` |
| Python | `extract_text`／`extract` | unchanged | `extract_v2` |
| WASM | `extractText`／`extract` | unchanged | `extractWithOptions` |

Legacy `layout=false` 對應 ContentOrder；`layout=true` 對應 legacy Layout。0.2.0 不會把 legacy call
默默改成 Auto。

## Upgrade steps

### Step 1: update dependencies and artifacts

將 Cargo、wheel 或 WASM package version 更新到 0.2.0，並確認四端 `version_info`／`versionInfo`
回 `stage-11`。

### Step 2: keep legacy behavior while upgrading

先用原 entry point 跑既有 golden。結果 shape 不應新增 `mode`、`glyphs`、`separators` 或 `quality`。
若 Rust code 使用完整 `ParseLimits` literal，補 cache fields 或改用 default update syntax。

```rust
use pdf_core::ParseLimits;

let limits = ParseLimits {
    max_file_bytes: 64 * 1024 * 1024,
    ..ParseLimits::default()
};
```

### Step 3: opt in to Auto

Rust：

```rust
use pdf_core::{ExtractionMode, TextExtractionOptionsV2};

let options = TextExtractionOptionsV2 {
    normalize_unicode: false,
    mode: ExtractionMode::Auto,
    include_quality_metadata: true,
};
```

CLI／Python／WASM 分別使用：

```text
rust-pdf extract input.pdf --mode auto --json
rust_pdf.extract_v2(data, mode="auto", quality=True)
extractWithOptions(bytes, { mode: "auto", quality: true })
```

### Step 4: consume warnings and quality

保存 warning `code` 與 page/font context。以 `quality` counters 觀察 synthesized separators、fallback、
replacement 與 ambiguous boundaries；不要用單一 counter 斷言全文正確，仍需 golden fragments。

## Verify the upgrade

```powershell
cargo test --workspace
cargo test --workspace --doc
wasm-pack test --node bindings/wasm
python -m pytest bindings/python/tests -q
rust-pdf version --json
```

Expected result：workspace、Python、WASM tests 全綠，version 是 0.2.0/stage-11。對正式 corpus 再比較：

- legacy text、pages 與 result shape 未改。
- Auto required fragments 存在，forbidden spacing artifacts 不存在。
- warning codes 與 quality 在 Rust/CLI/Python/WASM 一致。
- private corpus hash 先通過，再接受任何 golden review。

## Known issues

0.2.0 不提供 rendering、OCR、encryption、damaged-xref repair、完整 PDFDocEncoding、W2/DW2、tagged
structure-tree logical order 或 PDF 2.0 normative conformance。Auto 是 deterministic extraction
heuristic；多欄、重疊與混合方向可能回 `reading_order_ambiguous` 並局部回退 source order。

## Rollback

若尚未依賴 V2 result，切回 legacy entry point即可恢復 0.1.x text/output semantics。若 package smoke
失敗，恢復前一版 artifact 與 lockfile，不覆蓋已發佈 tag。不要刪除 0.2.0 regression tests；修正後
重新執行完整 release runbook。已用 0.2.0 `ParseLimits` 的 source 若降版，需移除兩個 cache fields。