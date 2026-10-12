# 2026-10-12 真人驗收決定

**本輪真人測試已完成並獲使用者核准；Stage 7.4 與後續開發可以繼續。**

使用者完成 Reviewer A 的 3 頁試評後，明確要求將本輪視為真人測試完成，
以便繼續後續階段。本決定將「單人真人試評＋使用者驗收」列為本輪開發閘門的依據，
取代先前必須先完成雙人複核、裁決與計時 pilot 才能開始後續開發的條件。
不得再次以缺少第二位評閱者或計時紀錄阻擋已授權的後續開發。

## 實際證據

- Reviewer A 完成 3/3 頁，共 71 個可選文字框、27 個人工區塊。
- 已核對文件 SHA、頁碼、完整 node IDs、每框恰屬一區塊及閱讀順序無循環。
- 人工標註綁定解析版本 `64c1480`，原始匯出檔和私人圖片只保存在本機。
- 對該版 frozen candidate 的單人診斷：19 個區塊先後關係，macro concordance
  為 0.9878542510。這是單人試評診斷，不是雙人裁決 gold 分數。
- 第二位真人、分歧裁決與主動操作時間未執行；沒有複製 A 的資料冒充 B。

可機讀的開發驗收決定：
[`human-acceptance-2026-10-12.json`](../../evidence/human-acceptance-2026-10-12.json)。
既有 gold/pilot validator 仍如實描述雙人研究流程；其原始 BLOCKED 結果不覆蓋
本次使用者核准的開發放行決定。此核准不代表所有版型、影像 OCR 或上線品質已通過。

## 試評發現與已完成修正

1. 文字座標：`Td`／`TD`／`T*` 與引號換行運算，必須在文字行矩陣座標內位移。
   已修正縮放／旋轉 Tlm 下的行距與位置（ISO 32000-1 Table 108），新增兩個回歸案例。
   受測頁原始 1,305 個字形的 Unicode 雜湊不變；五段列表位置與獨立參考值的最大差
   為 0.000057 pt。底部文字現在分成標題與五段列表。
2. 評閱介面：繁體中文提示、定位未分類框與橘框提示；已分類節點不再顯示「待分類」。
   畫面明確區分「現有框已分類」與「原稿內容完整」。
3. 單人草稿：部分／單人匯出尚未裁決時可以通過格式檢查，仍維持未完成雙人流程的狀態；
   完整雙人 gold 依然要求有效裁決原因。
4. 中間表格：是已識別的 Image XObject，沒有文字層。其影像內文字覆蓋列為後續工作，
   不以文字框全覆蓋冒充表格／OCR 已完成。

座標修正版另存，不覆蓋原始人工成果。第 2 個試評頁的框線完全相同，13 個人工區塊
已原樣保留；第 1、3 頁的新舊框線不同，舊標註保留供對照。修正版複核列為持續驗證，
不重新關閉本次已獲核准的開發閘門。

## 本輪驗證

- Rust 行為測試 265 passed／1 existing manual benchmark ignored；doctest 1 passed。
  合計 266 passed。首次 doctest 被 Windows sandbox 暫存執行檔 ACL 阻擋，限縮重跑通過。
- `cargo fmt --all --check`、全 workspace／all-target／all-feature Clippy `-D warnings` PASS。
- 修正版 Python binding：17 passed／2 ONNX integration cases skipped（此輪未配置外部模型）。
- packet／gold／pilot self-tests、JavaScript syntax、Ruff、Git diff check PASS。
- 瀏覽器驗證定位功能及真人完成 3/3 頁；沒有由代理代填真人標註。
- 本輪未重跑遠端新增 Office 0.2 的完整跨套件測試，也未宣稱 GitHub Actions 已通過。
