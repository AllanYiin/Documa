# Direct-image OCR

Version：`0.2.0` development line `priority-2026-10-12`。

## Overview／概覽

本專案的 OCR 快速路徑不會先把整頁 PDF rasterize。pdf-core 先解析 content stream
與 graphics state，只有在頁面沒有可用原生文字、恰有一個非 artifact 的 painted image，
且該 image 覆蓋至少 90% 頁面時，才直接取出 Image XObject 的 JPEG 或 raw samples。

## 支援範圍

- JPEG（DCTDecode）直接解碼。
- 未壓縮或受支援通用 filters（Flate、ASCIIHex、ASCII85、RunLength、LZW）的 1-bit/8-bit DeviceGray、8-bit DeviceRGB、
  8-bit DeviceCMYK。
- OCR 偵測框由 image pixel space 仿射映射至 canonical LayoutSpace，保留旋轉與 skew。
- RapidOCR/PaddleOCR 相容的 detection ONNX、recognition ONNX 與配套 dictionary。
- 模型、dictionary 與 ONNX Runtime 都不由 pdf-core 內嵌；核心仍不依賴深度學習框架。

目前 direct path 會保守拒絕 soft mask、image mask、多張 painted image、非全頁 image，
以及尚未解碼的 CCITT/JBIG2/JPX 等 codec。這些頁面會回傳穩定 reason code，不會暗中
rasterize 或產生看似成功的錯誤 OCR。

也會拒絕 Decode arrays、explicit Mask、content clipping、Form BBox clipping、ExtGState／
transparency groups、inline images 與額外向量繪製。此策略可能保守拒絕視覺上沒有改變影像的
graphics state；它不嘗試模擬 rendering。支援 samples 的列尾 padding 會正確略過，JPEG
實際尺寸必須與 PDF 宣告一致，頁面覆蓋率使用 quad 與 CropBox 的實際交集。

## CLI

候選分析不需要 ONNX feature：

~~~console
cargo run -p pdf-cli -- ocr-candidates scan.pdf --min-page-coverage 0.9
~~~

實際 OCR 使用可選的 native ONNX feature：

~~~console
cargo run -p pdf-cli --features ocr-onnx -- ocr scan.pdf \
  --det-model models/det.onnx \
  --rec-model models/rec.onnx \
  --dictionary models/keys.txt \
  --min-confidence 0.5 --json
~~~
原生 feature 採 ORT 動態載入；執行時需讓系統找到相容的 ONNX Runtime shared library。
Windows 預設檔名是 onnxruntime.dll，Linux 是 libonnxruntime.so，macOS 是
libonnxruntime.dylib；也可用 ORT_DYLIB_PATH 指向完整路徑。這讓 parser 與 wheel
不必綁定 TensorFlow、PyTorch 等深度學習框架。

目前 `ort 2.0.0-rc.13` 的相依功能需要 Runtime C API 27；本機整合 fixture 固定使用
ONNX Runtime 1.27.1。遺失或過舊的 runtime 會回 `ocr_model`，不再由 loader panic 結束。
原生 feature 的 Rust MSRV 為 1.89；未啟用時維持 1.88。


det/rec/dictionary 必須是互相匹配的 PaddleOCR/RapidOCR 模型組合。本專案不下載或
重新散布模型；使用者需自行確認模型來源、雜湊與授權。

目前 backend 要求 recognition model 的固定正數高度，且 output classes 必須恰等於 dictionary
行數加一個 CTC blank。動態高度、錯誤 tensor shape、字典不匹配均明確拒絕。
可重跑的模型準備與真實 ONNX 驗證見 [public integration fixture](../tests/fixtures/ocr/README.md)：
準備工具會固定辨識高度為 48、加入明確的 space token，並驗證來源與準備後的兩組 hash。

## Rust

pdf-core::PdfDocument::analyze_direct_ocr 提供 PDF-aware 候選與座標映射。
pdf-ocr::ocr_document 接受任何實作 ImageOcrEngine 的後端；啟用
pdf-ocr/rapidocr-onnx 後可使用 RapidOcrOnnxEngine。

逐頁分析使用 `PdfDocument::direct_ocr_pages()`／`direct_ocr_pages_selected()`；它們只解碼
實際被繪製且合格的 Image XObject，沒有整份 Layout/image queue。`analyze_direct_ocr()` 是
collecting API，累計 retained image bytes 不得超過 `ParseLimits.max_total_decoded_bytes`。
`ocr_pages()`／`ocr_pages_selected()` 逐頁辨識，`ocr_pages_owned()` 可讓 iterator 擁有 engine。
Drop iterator 可取消之後的工作；正在執行的一次 ONNX inference 不會被強制中斷。

## Parameters／文件級預算

`OcrOptions` 保留原 signature；新增的 `OcrDocumentLimits` 與 collecting／streaming API 分開傳入。

| 欄位 | 預設 | 意義 |
|---|---:|---|
| `max_total_text_bytes` | 64 MiB | 跨頁合成文字，包含換行 |
| `max_total_blocks` | 65,536 | 跨頁接受的 OCR blocks |
| `max_total_result_bytes` | 256 MiB | 三份文字加頁面／block struct 的邏輯 payload 上限；不是 RSS |
| `max_decoded_image_bytes` | 128 MiB | 每張 decoded OCR image 的保守大小預算 |

每頁仍受 `OcrOptions.max_blocks_per_page`、`max_total_text_bytes_per_page` 與 confidence 門檻限制。
模型本身及 ONNX Runtime 內部分配不屬這些 PDF/image/output 預算。

Example：`ocr-limits.json` 可只有 `{"max_total_text_bytes":1048576,"max_total_blocks":1000}`。

```console
rust-pdf ocr-candidates scan.pdf --pages 1,3-5 --ndjson
rust-pdf --limits limits.json ocr scan.pdf --pages 1 --ndjson --ocr-limits ocr-limits.json --det-model det.onnx --rec-model recognition-h48.onnx --dictionary dictionary.txt
```

## Python 與 browser WASM

Python wheel 以 Cargo feature ocr-onnx 建置時，rust_pdf.ocr_direct(...) 會執行
native ONNX；未啟用時會明確拋出 RuntimeError。rust_pdf.analyze_direct_ocr(...)
永遠可用。

WASM 的 analyzeDirectOcr(data, minPageCoverage) 回傳候選影像 bytes、格式、尺寸與
placement quad，讓 JavaScript 端接 ONNX Runtime Web 或其他 OCR engine。WASM package
不會拉入 native ONNX Runtime。

Python 也提供 `Document`、`direct_ocr_pages()`、`ocr_direct_stream()`；原生計算釋放 GIL。
`limits` 是 partial `ParseLimits` mapping，`ocr_limits` 是 partial `OcrDocumentLimits` mapping。

```python
import rust_pdf

with rust_pdf.Document(data, limits={"max_pages": 100}) as document:
    with document.ocr_direct_stream(
        detection_model, recognition_model, dictionary,
        pages="1", ocr_limits={"max_total_text_bytes": 1048576},
    ) as pages:
        for page in pages:
            print(page["text"])
```

WASM `new PdfDocument(bytes, limits).directOcrPages({pages: "1"})` 提供 `{done, value}` iterator；
`close()` 取消後續候選分析，`free()` 釋放 wrapper。

## Responses／Errors

候選與 OCR page result 保留原始 `page_index` 及 reason；rejected page 不含候選或 OCR text。
collecting OCR result 的形狀仍是 `pages`／`text`。NDJSON 每行是一個 page result；程序 exit code
非零代表失敗，即使先前已送出部分頁面。

初始化錯誤為 `ocr_model`，backend 推論錯誤為 `ocr_inference`。一次 backend error 後必須重建
engine，以避免第三方 session pool 在錯誤後失去 session 而永久等待。所有 confidence 必須為
有限的 `[0,1]` 值。限額錯誤在 Python 為 `ocr_limit_exceeded`，CLI 映射到既有 `limit_exceeded`。
