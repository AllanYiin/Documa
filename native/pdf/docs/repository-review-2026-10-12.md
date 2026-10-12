# Repository 功能盤點與改善建議

日期：2026-10-12。對象：維護者與安排下一階段工作的開發者。
範圍：目前本機 `0.2.0` 工作區；公開 stage 標籤仍是 `stage-11`，但程式已包含 Stage 12 與 direct-image OCR。
本次依原始碼、公開測試、CI、文件與本機驗證整理；不是完整 PDF 標準符合性或資安稽核。

## 概覽

專案已具備相當完整的「唯讀文字與文件結構抽取」骨架。主要缺口是相容性、
OCR 邊界與品質驗收，以及把已有測試納入持續驗證。下一階段應優先提高既有輸出的可靠度。
一般 rendering、PDF 編輯與引入外部 PDF parser 不符合目前產品與架構約束。

| 功能 | 現況與限制 | 主要證據 |
|---|---|---|
| PDF 基礎解析 | objects、classic/incremental/hybrid xref、xref stream、object stream、lazy resolution | `crates/pdf-core/src/{parser,xref,document,object_stream}.rs` |
| 串流解碼 | Flate、TIFF/PNG predictors；一般 filter 僅支援 Flate | `crates/pdf-core/src/filter.rs` |
| 頁面與座標 | page tree、繼承屬性、MediaBox/CropBox、UserUnit、Rotate 與可逆座標轉換 | `crates/pdf-core/src/{page,geometry}.rs` |
| Unicode 文字 | text/Form operators、ToUnicode、simple/CID fallback、ActualText、glyph metrics 與 provenance | `crates/pdf-core/src/{text,font,cmap,font_metrics,marked_content}.rs` |
| 文字順序 | ContentOrder、legacy Layout、Auto；Auto 是 heuristic | `crates/pdf-core/src/{text,layout}.rs` |
| Layout IR | schema、nodes/spans、tagged/source/inferred/main-flow order、角色、視覺注意圖 | `crates/pdf-core/src/{layout_ir,tagged_structure,reading_order}.rs` |
| 表格 | tagged topology、row/col span、vector lattice、保守 borderless detection 與 evidence fusion | `crates/pdf-core/src/table_reconstruction.rs`、`tests/stage12_table_reconstruction.rs` |
| 影像與圖說 | XObject bytes、JPEG header validation、raw samples、painted quad、Figure/Alt/Artifact、caption anchors | `crates/pdf-core/src/{images,figure_flow,layout_ir}.rs` |
| 導覽 | Link、URI/GoTo、named destinations、outline；不執行 action | `crates/pdf-core/src/navigation.rs` |
| 串流處理 | Rust lazy events、Python 逐頁 iterator 與 terminal finalizations；CLI/WASM 仍以完整結果為主 | `crates/pdf-core/src/layout_ir.rs`、`bindings/python/src/lib.rs` |
| OCR | 單張全頁影像快速路徑、pixel→LayoutSpace、外部 ONNX 模型；WASM 提供 candidates | `crates/pdf-core/src/ocr.rs`、`crates/pdf-ocr/src/lib.rs`、`docs/ocr.md` |
| 工程保護 | safe core、stable errors、ParseLimits、decode budget、LRU、回歸測試、基本 fuzz 與跨平台 CI | `crates/pdf-core/src/{lib,error,limits,decode_budget}.rs`、`.github/workflows/ci.yml` |

## 本次確認並修正的問題

### 1. 1-bit 多列影像的 padding 處理錯誤

原先把整張影像當成連續 bit stream，以 `ceil(width * height / 8)` 檢查長度，
忽略每列的 byte alignment。4×2 的合法 1-bit 影像應有兩個 bytes，舊 decoder 卻要求一個 byte；
另一些尺寸會把上一列 padding 解讀為下一列像素。

- `pdf-ocr` 改為逐列取樣，跳過 padding。
- `pdf-core` 的 raw sample 長度檢查改為 `ceil(row_bits / 8) * height`。
- 新增多列黑白像素驗證、短資料拒絕，以及 1/2/4/8/16-bit 長度回歸。

### 2. OCR 的全頁覆蓋率未考慮位置

原先只用影像 quad 面積除以頁面面積。100×100 頁面上，平移到 x=200 的 100×100
影像仍被判定為全頁候選。旋轉或 skew 也不能靠 bounding box 交集判斷。

- 改為 convex quad 與 LayoutSpace 頁面矩形的實際交集面積。
- 保留原始影像 quad，避免破壞 OCR pixel mapping。
- 新增頁外、部分移出、菱形、skew 與恰好 90% 覆蓋率回歸。
- 此修正處理頁面邊界；不代表支援 PDF content clipping path。

### 3. JPEG 尺寸不一致仍進入 OCR

影像擷取允許保留 JPEG 與 `jpeg_dimension_mismatch` warning，但 OCR 用 PDF 宣告尺寸做座標換算。
當 JPEG 真實尺寸不同時，可能產生錯誤比例或超界座標。

- OCR 現在先讀取 JPEG header，確認尺寸等於 candidate metadata，才進行像素解碼。
- 尺寸不符回傳既有 `ocr_image_decode`，一般 image extraction 的保留行為不變。
- 新增真實 JPEG 編碼 fixture，驗證匹配成功、不匹配拒絕。

以上三項都先以新增測試重現失敗，再完成修正；未改變公開 API signature。

### 4. 支援文件與實作不同步

已修正 `docs/compatibility.md` 中 tagged structure 不支援、OCR 不存在與 CropBox 繼承遺漏的描述，
並補 Layout IR、表格、navigation、visual-reading 的實際支援界線。
README 改為 block-brush 工作台現況，修正「RSS gate 未通過」的過期敘述，並加入 `pdf-ocr` workspace 項目。

## 待處理事項與優先順序

P1 表示下一輪可靠度工作，P2 表示後續擴充；真人 gate 是獨立放行條件。

| 優先 | 建議 | 原因／來源 | 建議完成條件 |
|---|---|---|---|
| 放行條件 | 完成真人閱讀順序雙人標註、裁決、timed pilot 與 table/image gold | `tests/fixtures/stage12/stage7c-pilot-dod.md` 仍明示 BLOCKED；歷史 parser text F1 與 RSS 通過不等於整體驗收 | 真實 reviewer artifacts 與 scorer/pilot READY；維持 Stage 7.4 與預設 provider cutover gate |
| P1 | 補 OCR 候選的圖形語意與拒絕策略 | `images.rs`/`ocr.rs` 尚未完整處理 `/Decode`、explicit `/Mask`、content clipping、透明度；raw sample 格式也不是全部可解碼 | 先針對未支援效果明確拒絕或 warning；加入反相、遮罩、裁切、非支援色彩空間等 fixtures，再決定支援範圍 |
| P1 | OCR 改為逐頁候選與輸出，加入文件級結果預算 | `analyze_direct_ocr` 先保留完整 layout 與 images，再 clone candidates；每頁又線性搜尋所有 images。`ocr_document` 僅有每頁文字限額，會累積所有 blocks/text | 多頁掃描壓力測試，量測 RSS；避免重複持有 image bytes；總 OCR 文字/結果 bytes 有明確限額 |
| P1 | 擴充 CI 與 fuzz 覆蓋 | CI native commands 沒有 `--all-features`；WASM job 只 build。`parse_document` fuzz 只測 summary/object/Auto，未走 Layout、tables、navigation、OCR candidates | default + all-feature checks、Node WASM tests；為 Layout/event/image/OCR 加有界 fuzz 與取消/錯誤路徑回歸 |
| P1 | 建立真實 ONNX 整合驗證 | 現有 OCR tests 主要驗證 candidate/mapping/raw decoder；模型與 runtime 外置 | 固定授權與 hash 的 det/rec/dictionary、runtime 版本及少量可散佈 gold；驗證初始化、辨識品質、confidence、座標與模型不匹配 |
| P1 | 擴充常見 stream filters | `filter.rs` 一般 streams 只有 Flate；不支援 filter 可阻擋整份文件的文字抽取 | 先 ASCIIHex/ASCII85/RunLength，再按語料需求安排 LZW；每個 filter 均有 chained decoding、truncation、expansion/absolute limits 測試 |
| P1／依語料 | 完整直排與 CJK 字型度量 | `font_metrics.rs` 保留 writing mode，但沒有 W2/DW2；現有 bounding boxes 仍有估計 | 直排 CJK gold、W2/DW2、字元原點與 advance、混合方向／Form 變換跨端回歸 |
| P2 | 加 page selection、可設定 limits、串流 CLI/WASM | Rust 可設定 ParseLimits，但 CLI/Python/WASM 入口主要固定預設；抽取入口缺頁碼範圍，CLI/WASM 沒有 Python 等價的 page stream | 頁碼範圍契約、NDJSON/events、取消語意、finalization 說明與跨端一致性；需先核定公開 API |
| P2 | 減少重複 parse 與 Python 執行阻塞 | Python 每個 convenience function 重建 PdfDocument，native 函式未釋放 GIL | 評估 reusable document handle 與安全 GIL detach；以並行呼叫/生命週期/資源釋放測試驗證 |
| P2 | 整理版本與發布訊息 | `version_info` 固定 stage-11，已存在 Stage 12/OCR；workspace repository URL 是 placeholder；CI 跟 stable，MSRV 宣告 1.88 | 區分 release baseline 與 development capabilities；發布前設定真實 metadata，加入 MSRV job 並重建四端產物 |

圖形語意與 OCR 記憶體問題涉及 core metadata、graphics state 或輸出生命週期，
本次列為明確的後續工作，未擴張為 renderer 或重設公開 DTO。
加密、損毀 xref repair、AcroForm/附件與更多影像 codecs 可另排需求；不建議優先於上表的抽取可靠度工作。

## 本次驗證

- 新增回歸：core raw row length 1 項、coverage 1 項（含多情境）、OCR decoder 2 項。
- `cargo test --workspace --all-features`：原生部分 239 passed、0 failed、1 ignored（手動 benchmark）。
  私有 corpus 環境變數未配置，相關測試內部 skip，不能解讀為私有 PDF 本次通過。
- 上述命令的 doctest 首次因 Windows sandbox 暫存目錄 ACL 失敗；改將 `TEMP`/`TMP`
  指向 `target/repo-review-tmp` 後，`cargo test --workspace --all-features --doc`：1 passed。
- `cargo fmt --all --check`：PASS。
- `cargo clippy --workspace --all-targets --all-features -- -D warnings`：PASS。
- `cargo clippy -p pdf-core -p pdf-wasm --target wasm32-unknown-unknown -- -D warnings`：PASS。
- README 與 compatibility reference 的 `doc_quality_audit.py`：各 100/100；此為結構檢查，非功能測試。
- 本次未重跑：私有 corpus benchmarks／human gold、Python wheel runtime、Node/browser WASM runtime、ONNX 模型推論、fuzz 與 supply-chain audit。

Rust 驗證使用工作區 toolchain 1.96.0；未重新驗證 MSRV 1.88。
DEVNOTE 中的速度、RSS 與 F1 數字只作歷史背景，本次沒有重現這些量測。

## 維護與回復

本地目錄沒有 Git metadata；本次未建立 commit 或發布產物。
修改前六個檔案保存在 `target/repo-review-backup/`，統一 diff 為 `target/repo-review.diff`。
這些是暫存回復材料，不是版本控制；恢復前應確認沒有後續修改。
本報告由維護者在下一次 OCR、相容性或發布工作後更新；應以當次測試取代本次狀態。
