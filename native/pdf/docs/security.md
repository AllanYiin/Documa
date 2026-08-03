# Security model

所有 PDF bytes 都視為不可信輸入。本文適用 `0.2.0`／Stage 11 release candidate。

## Overview／概覽

本文供 parser 維護者與處理不可信 PDF 的整合者使用，說明安全邊界、resource limits、錯誤處理與
0.2.0 文件級解碼治理。成功條件不是接受所有合法 PDF，而是在輸入超界時 deterministic 地
停止並回 stable error code。

## Why／為什麼需要文件級治理

because 單次 stream limit 只能限制一次呼叫，攻擊者仍可讓數萬個 compressed objects 重複指向
同一 object stream。這個 context 下，安全 decision 是讓 document lifetime budget 與 bounded
cache 同時存在：budget 限制累積工作，cache 移除正常文件中的重複解壓，兩者不能互相取代。

## 保證

- `pdf-core` 使用 `#![forbid(unsafe_code)]`。
- 不連結既有 PDF parser 或 native PDF engine。
- offset、length、加法與乘法使用 checked conversion/arithmetic。
- object、page、Form、xref 與 indirect length resolution 具循環／深度限制。
- Flate 每次以固定 buffer 讀取，並檢查絕對輸出、文件生命週期總輸出、filter depth 與
  expansion ratio。
- CMap mapping（含 invalid source）、marked-content depth、content operation、text span／glyph、image count 與 pixel count 有獨立上限。
- CLI 讀檔前先以 metadata 拒絕超過 `max_file_bytes` 的檔案。
- 預設不記錄抽取內文或 image payload。

## 解碼威脅模型

Stage 11 主要防範四種不可信輸入行為：

- 以高壓縮率 stream 製造 decompression bomb。
- 讓大量 compressed objects 指向同一 `/ObjStm`，誘使 parser 重複解壓相同資料。
- 以大量 object streams、極大 `/N`／`/First` 或錯誤 member range 擴張 cache 與索引。
- 藉由 clone 或另一份文件取得新的隱藏預算、跨文件重用 cache。

對應邊界是每份 `PdfDocument` 一個共享 runtime；clone 共用同一單調遞增 budget 與 cache，
不同文件則完全隔離。沒有 global cache、背景執行緒或 telemetry。

## DecodeBudget 計量語意

- budget 在解析第一個 xref section 前建立，因此 xref stream 也會計量；文件建立成功後，
  同一 budget 繼續供 object stream、page/Form content、ToUnicode 與 Flate image 使用。
- 每個 Flate output chunk 在加入輸出前扣除；TIFF／PNG predictor 建立新輸出 buffer 前，
  predictor output bytes 也會扣除。Predictor 1 重用原 buffer，不重複計量。
- 無 filter 的 raw stream 沒有發生 decode，因此不增加 decoded bytes；仍受 raw stream 與
  downstream collection limits 約束。
- object-stream cache hit 不增加 decode bytes；cache miss 會計量。entry 被 eviction 後若再被
  解析，重新解壓的 bytes 必須再次計量。
- `max_total_decoded_bytes` 是 monotonic document-lifetime 上限，checked addition overflow 或
  超出上限都回穩定 `limit_exceeded`。失敗不會換發新 budget。

`/Type /XRef` 與已先驗證 `/Type /ObjStm` 可依格式推導的 structural size 放寬 expansion-ratio
heuristic，但不能放寬 `max_decoded_stream_bytes` 或文件總量。一般 content、Form、font 與 image
stream 不取得此例外。

## Bounded object-stream cache

cache key 是 object-stream `ObjectId` 加上實際 indirect-object offset，後者作為解析 revision
identity。value 保存 decoded bytes、完整 header index 與已驗證的 member start/end ranges；
cache byte weight 同時計入 decoded bytes 與 range index 配置。

淘汰採不依賴 wall clock 的 deterministic LRU。預設最多 256 entries、64 MiB cache weight；
單一 decoded stream 仍受 256 MiB `max_decoded_stream_bytes`。entry 太大或 cache limit 為零時，
當次解析仍可成功但不寫入 cache；後續重解壓仍會消耗文件總 budget。所有 cache arithmetic 都以
checked operations 驗證。

## 診斷與驗證

`PdfDocument::decode_metrics()` 回傳 point-in-time counters，不包含 PDF 內文。CLI 只在明示
`validate --diagnostics` 時輸出；plain mode 寫到 stderr，`--json` 則加入 `decode_metrics`：

```powershell
cargo run -p pdf-cli -- validate <PDF_PATH> --json --diagnostics
```

AI Index 2026 的 Stage 11.6 gate 驗證 45,151 個 in-use objects：335 次 decode、17,644,788
decoded bytes、33,028 cache hits、334 misses、78 evictions，peak cache weight 17,155,094 bytes。
這些數字是固定測試檔與目前 traversal order 的診斷 baseline，不是公開 API 的效能保證。
Stage 11.6 另以 Auto-enabled `parse_document` fuzz target 完成 165,192-run bounded smoke、0 crash，
並驗證 repeated missing/invalid Unicode mapping 與 unmatched marked-content warnings 會聚合而不會
隨 glyph 數無界放大。完整命令與環境見 validation matrix。

## Trade-offs／取捨

同一文件的 decode/cache 操作會由 mutex 序列化，犧牲單文件內的平行解碼，換取 clone 與並行
caller 不會重複消耗或繞過同一 budget。bounded LRU 可能淘汰稍後仍需使用的 entry；此時選擇
重新解壓並再次計量，而不是無界增長 cache。預設 limits 也可能拒絕極大型但合法的 PDF，caller
只能在理解信任邊界後明示調整。

## 不保證

- 本專案不是 sandbox；caller 仍應在低權限 process 中解析外部檔案。
- 無法保證所有合法 PDF 在預設 limits 下成功；limits 可由 Rust API 調整。
- 未支援的 filter／codec 不會被猜測性解碼。
- %PDF-2.x header 可解析不代表 PDF 2.0 normative conformance；未支援 feature 仍明確拒絕。
- 字型 fallback 與 layout warning 是 fidelity 風險，不是記憶體安全問題。

## Error handling

外部介面應依 stable code 分支，例如 `limit_exceeded`、`invalid_xref`、
`unsupported_feature`。不要依賴 human-readable message。

Python 的 `PdfParseError` message 是 JSON；WASM rejection value 是包含
`code`、`offset`、`message` 的 JavaScript object；CLI error 是 stderr JSON。

## Vulnerability reporting

回報內容至少包含：

- 觸發版本與 target
- 最小 PDF 或可重現 generator
- 實際 error／panic／resource usage
- 預期 limit 或拒絕行為

不要把含敏感內容的原始 PDF 放進公開 issue；先建立最小合成 reproducer。
