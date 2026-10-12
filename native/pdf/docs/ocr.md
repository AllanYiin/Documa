# Direct-image OCR

本專案的 OCR 快速路徑不會先把整頁 PDF rasterize。pdf-core 先解析 content stream
與 graphics state，只有在頁面沒有可用原生文字、恰有一個非 artifact 的 painted image，
且該 image 覆蓋至少 90% 頁面時，才直接取出 Image XObject 的 JPEG 或 raw samples。

## 支援範圍

- JPEG（DCTDecode）直接解碼。
- 未壓縮或 FlateDecode 的 1-bit/8-bit DeviceGray、8-bit DeviceRGB、
  8-bit DeviceCMYK。
- OCR 偵測框由 image pixel space 仿射映射至 canonical LayoutSpace，保留旋轉與 skew。
- RapidOCR/PaddleOCR 相容的 detection ONNX、recognition ONNX 與配套 dictionary。
- 模型、dictionary 與 ONNX Runtime 都不由 pdf-core 內嵌；核心仍不依賴深度學習框架。

目前 direct path 會保守拒絕 soft mask、image mask、多張 painted image、非全頁 image，
以及尚未解碼的 CCITT/JBIG2/JPX 等 codec。這些頁面會回傳穩定 reason code，不會暗中
rasterize 或產生看似成功的錯誤 OCR。

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


det/rec/dictionary 必須是互相匹配的 PaddleOCR/RapidOCR 模型組合。本專案不下載或
重新散布模型；使用者需自行確認模型來源、雜湊與授權。

## Rust

pdf-core::PdfDocument::analyze_direct_ocr 提供 PDF-aware 候選與座標映射。
pdf-ocr::ocr_document 接受任何實作 ImageOcrEngine 的後端；啟用
pdf-ocr/rapidocr-onnx 後可使用 RapidOcrOnnxEngine。

## Python 與 browser WASM

Python wheel 以 Cargo feature ocr-onnx 建置時，rust_pdf.ocr_direct(...) 會執行
native ONNX；未啟用時會明確拋出 RuntimeError。rust_pdf.analyze_direct_ocr(...)
永遠可用。

WASM 的 analyzeDirectOcr(data, minPageCoverage) 回傳候選影像 bytes、格式、尺寸與
placement quad，讓 JavaScript 端接 ONNX Runtime Web 或其他 OCR engine。WASM package
不會拉入 native ONNX Runtime。
