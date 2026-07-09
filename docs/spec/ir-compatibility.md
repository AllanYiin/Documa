# Documa IR 相容性契約（ir_version）

本文件定義 `DocumentIR.ir_version` 的版本語意，是外部 consumer（MCP host、RAG pipeline、OpenAI tools 使用者）可以依賴的穩定協定。schema 本身發佈於 `schema/documa.schema.json`，由 `scripts/generate_schema.py` 從 `src/documa/core/ir.py` 的 dataclass 生成——dataclass 是唯一事實來源，schema 檔禁止手改，CI 以 `--check` 強制同步。

## 版本語意（semver）

`ir_version` 形如 `MAJOR.MINOR`：

| 變更類型 | 需要的版本 | Consumer 義務 |
| --- | --- | --- |
| 新增 optional 欄位（有預設值） | MINOR +1 | 忽略不認識的欄位即可，既有讀取邏輯不受影響 |
| 新增 enum 成員 | MINOR +1 | 對未知 enum 值採寬容處理（fallback / unknown） |
| 移除欄位 | **MAJOR +1** | 需明確遷移 |
| 欄位改型別 | **MAJOR +1** | 需明確遷移 |
| 欄位語意改變（名稱不變但含義不同） | **MAJOR +1** | 需明確遷移 |
| 必填欄位增加（原 optional 變 required） | **MAJOR +1** | 需明確遷移 |

讀取端規則：`documa.core.serialization.document_from_plain_data` 對缺失欄位一律以預設值補齊，因此任何 0.x 的檔案都能被 0.y（y ≥ x）的程式讀取。`documa validate-ir` 對未知 MAJOR 直接報錯，對已知 MAJOR 的任何 MINOR 都接受。

## 0.1 → 0.2 變更清單（純 additive）

| 欄位 | 型別 | 語意 |
| --- | --- | --- |
| `producer_version` | `string \| null` | 產出此 IR 的 documa 套件版本 |
| `adapter_version` | `string \| null` | adapter 依賴版本，如 `pymupdf/1.26.6` |
| `pipeline_profile` | `string \| null` | pipeline 組態摘要：`default` / `ocr` 等 |

同時移除了從未被使用的 `JobState` enum（Python API 層；不影響 IR 檔案格式——沒有任何 0.1 檔案含有 JobState 資料）。

相容性由 `tests/test_schema_compatibility.py` 持續驗證：固定的 0.1 fixture（`fixtures/ir/v0_1_document.ir.json`）必須永遠可讀、可通過 schema 驗證，且其所有欄位必須存在於現行 schema 中。

## 判斷表：這個變更需要 major 嗎？

1. 舊版程式讀新版檔案會壞嗎？（欄位消失、型別不符）→ 會就是 MAJOR。
2. 新版程式讀舊版檔案，補預設值後語意正確嗎？→ 不正確就是 MAJOR。
3. 兩者皆否 → MINOR，並在本文件補上變更清單。

## Reading-order trace 的格式保證（0.2 期間新增，metadata 開放結構）

閱讀順序 v2 在既有 `metadata` dict 內記錄排序依據，keys 為契約的一部分（欄位在開放結構內，
schema 不變、不觸發版本升級；一旦發佈即不得改名或改語意）：

- BlockIR：`metadata.reading_order = {strategy, zone_id, column_index, rule, gestalt}`
  - `strategy`: `"zone_column_v2"`
  - `rule`: `"spanner" | "column_flow" | "single_column" | "grid_row_major" | "fallback_row_major"`
  - `column_index`: 0-based int；spanner 與 fallback 為 null
  - `gestalt`: 套用的知覺組織原則標籤（`proximity` / `continuity` / `similarity` / `figure/ground` / `common region`）
- PageIR：`metadata.reading_order_trace = {zones: [...], gutters: [...]}`
  - zone: `{zone_id, kind: "content"|"grid"|"banded", y0, y1, column_count?, block_count}`
  - gutter: `{zone_id, x0, x1}`
- 由多個 block 合併而成的 paragraph 繼承第一個成員的 `reading_order`。

Consumer 可以據此解釋「為什麼這個 block 排在這裡」；quality benchmark 以 trace 統計
`fallback_block_ratio` 作為欄偵測健康度信號。

## OCR 產物的格式保證

OCR 文字不新增 IR 欄位，一律放在既有 `metadata` dict 中，keys 為契約的一部分：

- BlockIR/ImageIR：`metadata.origin == "ocr"`、`metadata.ocr_engine`、`metadata.ocr_confidence`
- PageIR：`metadata.ocr`（mode/engine/confidence_avg）、`metadata.ocr_low_confidence`、`metadata.suppressed_native_blocks`

Consumer 可以依 `origin == "ocr"` 區分辨識文字與 parser 原生文字；Documa 自身的 exporter / 搜尋 / chunking 不對 OCR 文字降權。
