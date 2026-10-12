# Public ONNX integration fixture

`scan.pdf` contains one original 600×180 grayscale image saying `PDF OCR 12345`.
`scan.png` is its inspectable source raster, drawn using Arial at 40 pixels; no font
program or private document is embedded. This is an engineering integration fixture,
not reading-order/table/image human gold.

`onnx-manifest.json` freezes external model/runtime URLs, sizes, SHA-256 values,
explicit preparation steps and expected text/confidence. Models and runtime binaries
are downloaded under `target/ocr-integration`, never bundled with product artifacts.

The upstream recognition model declares a dynamic height. The current backend requires
a fixed recognition height, so preparation sets that input dimension to 48 without
changing weights. The prepared model has its own pinned hash. A space token is also
explicitly appended to the PaddleOCR dictionary, yielding 6624 tokens plus the CTC blank.
Both the unprepared dynamic model and mismatched dictionary must fail with `ocr_model`.

```console
python -m pip install onnx==1.18.0
python tools/ocr_integration.py fetch
cargo build --release -p pdf-cli --features ocr-onnx --locked
python tools/ocr_integration.py verify --output target/onnx-integration-report.json
```

The checked-in integration configuration covers Windows/Linux x64 and ONNX Runtime
1.27.1. Existing system runtimes are not replaced. Native model allocation is external
to parser and OCR output budgets; run untrusted models only in an appropriate process
boundary. Missing/old runtimes, malformed or incompatible models and dictionary
mismatches are reported as stable model errors.
