# Stage 11 Codex／Claude Code 分階段開發計畫

規格版本：1.2.0  
依賴規格：[technical-spec.md](technical-spec.md)  
原則：每個 stage 可獨立驗收、失敗時可回滾，不得跨過失敗的 stage gate。

## 全階段共同規則

- 以 repository 根目錄的 `AGENTS.md` 為最高專案規則。
- `crates/pdf-core` 是唯一 PDF-aware layer，並維持 `#![forbid(unsafe_code)]`。
- 不得加入現有 PDF parser 或 native PDF engine。
- 所有 input-derived 長度、offset、decoded bytes、cache、depth、pages、spans 與 warnings
  都必須有上限。
- bindings 只能轉換 options、types、errors 與 results。
- 每個 stage 都執行該 stage 測試、`cargo fmt --all --check`、workspace Clippy
  `-D warnings`；完成 Definition of Done 後才能前進。
- 工作區若有使用者未提交變更，不得覆寫或回復。

## Stage 11.0：鎖定契約、基準與 corpus schema

### 目標

把已確認規格轉成可執行的測試骨架、版本化 manifest 與文件入口。此 stage 不改抽取結果。
既有 workspace 已具備專案骨架、lint、測試與 README，因此 Stage 11.0 的責任是驗證並擴充，
不是重建專案。

### 前置條件

- Stage 0–10 gate 全部通過。
- 兩份實檔可在本機透過 CLI 完成 inspect、validate、extract。
- 已閱讀 `technical-spec.md` 與 `DEVNOTE.md`。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex；長期 invariant 已在根目錄 AGENTS.md，不要另建衝突規則。

[任務範圍]
只建立 Stage 11 測試骨架、corpus schema、baseline 記錄與文件入口。
不得改 text extraction、layout、font decoding 或公開 API 行為。

[需修改／新增的檔案]
- tests/real-world/manifest.toml.example
- tests/real-world/README.md
- tests/fixtures/stage11/
- crates/pdf-core/tests/stage11_contract.rs
- docs/text-fidelity.md
- README.md
- DEVNOTE.md

[具體步驟]
1. 定義 manifest schema_version、document id、file name、SHA-256、授權狀態、
   inspect 指標、required/forbidden fragments、warning expectations。
2. runner 只從 RUST_PDF_REAL_CORPUS_DIR 找檔；未配置時明確 skipped。
3. 不臆造 hash，不複製兩份使用者 PDF。
4. 加入最小 synthetic fixtures：英文逐字 positioning、CJK 逐字 positioning、
   high-ratio object stream、malformed ToUnicode。
5. 建立目前 Layout 與 ContentOrder 的 baseline；Auto 測試先標成未實作 contract，
   不得以 ignored 永久隱藏。
6. README 連結 Stage 11 文件，DEVNOTE 記錄 baseline commands。

[輸出格式要求]
列出新增 schema、fixture 來源、每個 baseline 的實際命令與結果。

[測試要求]
cargo test -p pdf-core --test stage11_contract
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings

[驗收標準 DoD]
- repository 不含兩份受限實檔。
- manifest 可偵測 hash mismatch。
- 未設定 corpus 時輸出 skipped 而非 passed。
- synthetic contract 測試可執行。
- 既有抽取輸出零變更。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code。若需要長期提醒，只引用根目錄 AGENTS.md；不要把整份規格複製到
CLAUDE.md。

[任務範圍]
建立 Stage 11 corpus 與 contract 測試基礎，不修改 parser 行為。

[檔案清單]
tests/real-world/*、tests/fixtures/stage11/*、
crates/pdf-core/tests/stage11_contract.rs、docs/text-fidelity.md、README.md、DEVNOTE.md。

[具體步驟]
依 technical-spec 的 Real-world corpus 規則建立 versioned manifest；使用環境路徑，
驗證檔案指紋；加入可散布 synthetic fixtures 與目前輸出 baseline；所有 fixture 記錄
來源與目的。

[輸出格式要求]
回報檔案變更、baseline 數值、略過 private corpus 的判斷方式與未完成 contract。

[測試要求]
執行 stage11_contract、format 與 workspace Clippy。

[驗收標準 DoD]
不得提交私人 PDF；schema、runner、synthetic fixture、baseline 與文件入口完整；
現有 parser 行為不變。
```

### 風險與回滾

- 風險：baseline 把目前錯誤空白誤當正確結果。  
  對策：目前輸出只記為 before baseline；Auto 的 required/forbidden fragments 才是目標。
- 回滾：刪除新增 corpus skeleton 與文件連結即可，不觸及 parser。

## Stage 11.1：新增 V2 抽取模式與跨介面契約

### 目標

加入 `ContentOrder`、`Layout`、`Auto` 的型別與 V2 entry point。`Auto` 暫時可委派給現有
Layout，但必須由 contract test 明確標示尚未改善，不得改壞 legacy 呼叫。

### 前置條件

- Stage 11.0 corpus schema 與 contract tests 完成。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex。本 stage 的公開相容規則應同步更新 docs/compatibility.md。

[任務範圍]
新增 V2 options、ExtractionMode、quality DTO 與四種 front end adapter。
不實作新 font metrics、ActualText、cache 或新 layout heuristic。

[需修改／新增的檔案]
- crates/pdf-core/src/text.rs
- crates/pdf-core/src/lib.rs
- crates/pdf-core/tests/stage11_modes.rs
- crates/pdf-cli/src/main.rs
- crates/pdf-cli/tests/stage11_modes.rs
- bindings/python/src/lib.rs
- bindings/python/tests/test_stage11.py
- bindings/wasm/src/lib.rs
- bindings/wasm/tests/stage11_web.rs
- docs/compatibility.md
- docs/errors.md

[具體步驟]
1. 新增 ExtractionMode 與 TextExtractionOptionsV2，不移除 legacy struct。
2. legacy false/true 分別映射 ContentOrder/Layout；legacy 預設保持不變。
3. 新增 extract_text_v2；Auto 暫走 Layout 並在 crate-private capability 狀態註記。
4. CLI 新增 --mode，與 --no-layout 衝突時拒絕。
5. Python 新增 extract_v2；WASM 新增 extractWithOptions，保留舊函式。
6. quality DTO 使用向下相容序列化；binding 不寫任何 PDF 判斷。
7. 若新增 invalid_option，更新所有錯誤轉換與 errors.md。

[輸出格式要求]
提供 API 對照表與 legacy compatibility 測試結果。

[測試要求]
stage11_modes、CLI mode tests、Python tests、wasm32 check、完整 workspace gate。

[驗收標準 DoD]
- legacy Rust/CLI/Python/WASM tests 不變。
- V2 四端接受相同 mode 名稱。
- 同 bytes/options 的文字與 warning codes 一致。
- 衝突選項得到穩定非零錯誤。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code；若建立可重複相容性檢查，可放
.claude/commands/check-stage11-modes.md，但不得修改 parser 規則。

[任務範圍]
完成 V2 mode 與跨介面 adapter；Auto 尚不做品質演算法。

[檔案清單]
pdf-core text/lib、pdf-cli main/tests、Python binding/tests、WASM binding/tests、
compatibility.md、errors.md。

[具體步驟]
保留 legacy API；新增 V2 entry point；統一 mode 序列化名稱；檢查 bindings 只轉型；
以 golden JSON 比較四端 text、pages、warnings、quality 欄位。

[輸出格式要求]
回報 public signature、相容策略、跨端 golden 差異。

[測試要求]
執行所有 mode tests、workspace gate 與 wasm32 check。

[驗收標準 DoD]
舊呼叫不變、新呼叫可用、Auto 明確尚未完成、無 binding-side PDF 邏輯。
```

### 風險與回滾

- 風險：新增欄位破壞嚴格反序列化 caller。  
  對策：使用 V2 result／entry point，不修改 legacy shape。
- 回滾：移除 V2 exports 與 adapters；legacy code 不受影響。

## Stage 11.2：保存 glyph geometry 並解析 font metrics

### 目標

先產生忠於 content operations 的 positioned glyph layer，正確計算 advance、baseline、
writing mode 與 source ordinal；尚不改 Auto 的 separator 決策。

### 前置條件

- Stage 11.1 V2 API 與 mode contract 通過。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex；PDF-aware 規則只可放 pdf-core。

[任務範圍]
實作 PositionedGlyph internal model、simple/CID widths 與完整 text-state advance。
不做 ActualText、multi-column 或 object-stream cache。

[需修改／新增的檔案]
- crates/pdf-core/src/text.rs
- crates/pdf-core/src/text_model.rs
- crates/pdf-core/src/font.rs
- crates/pdf-core/src/font_metrics.rs
- crates/pdf-core/src/content.rs
- crates/pdf-core/tests/stage11_geometry.rs
- crates/pdf-core/tests/stage11_font_metrics.rs

[具體步驟]
1. 為每個 glyph 指派 bounded source ordinal。
2. 解析 simple /FirstChar、/Widths、/MissingWidth。
3. 解析 CIDFont /DW、/W；保留 vertical writing metadata。
4. advance 納入 font size、horizontal scale、Tc、Tw、TJ adjustment、text matrix、CTM、
   Form matrix。
5. 所有浮點值需 finite；所有 collection 受 limits。
6. 先以 positioned glyph 重新產生現有 spans，legacy golden 必須不變。

[輸出格式要求]
回報支援的 metrics 子集合、fallback 規則與未支援 vertical metrics。

[測試要求]
geometry/font metrics tests、stage4–6 text tests、fuzz build、workspace gate。

[驗收標準 DoD]
- ContentOrder 完全依 source ordinal。
- synthetic glyph 的 origin/advance 符合 golden tolerance。
- matrix、TJ、Form cases 有測試。
- legacy layout output 尚未改變。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code。

[任務範圍]
建立 source-ordered positioned glyph 中介層與 font advance，不改 separator heuristic。

[檔案清單]
pdf-core text/text_model/font/font_metrics/content 與 stage11 geometry tests。

[具體步驟]
從 content operation 產生 glyph；解析必要 widths；完整套用文字與頁面轉換；對不支援
metrics 明示 fallback；保持 legacy spans 相容。

[輸出格式要求]
提供 geometry golden、limits 對照與已知 gaps。

[測試要求]
新增測試、既有文字抽取測試、fuzz build、workspace gate。

[驗收標準 DoD]
position/advance 可重現、沒有非有限值進入排序、所有 input-derived vector 有上限。
```

### 風險與回滾

- 風險：metrics 解析改變現有文字座標。  
  對策：先保留 legacy span projection，V2 才暴露新增 metadata。
- 回滾：保留資料模型但停止由 legacy path 使用，不需移除 API。

## Stage 11.3：實作 script-aware Auto layout

### 目標

以 glyph geometry 實作 deterministic line clustering、排序與空白推論，修正兩份實檔的
英文拆字與 CJK 多餘空白。

### 前置條件

- Stage 11.2 geometry golden 穩定。
- Stage 11.0 private corpus 可在本機執行。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex。不得以單一真實文件過度擬合。

[任務範圍]
實作 Auto layout、separator provenance、quality counters 與 ambiguity fallback。
不做完整 structure tree、OCR 或機器學習。

[需修改／新增的檔案]
- crates/pdf-core/src/layout.rs
- crates/pdf-core/src/text.rs
- crates/pdf-core/src/text_model.rs
- crates/pdf-core/src/limits.rs
- crates/pdf-core/tests/stage11_layout.rs
- tests/fixtures/stage11/*
- docs/text-fidelity.md

[具體步驟]
1. 按 page/rotation/writing mode 分組。
2. 以 normalized baseline distance 建立 line clusters。
3. stable sort 使用 projection coordinate + source ordinal tie-break。
4. Latin space 使用 advance-normalized gap；連續 CJK 不因一般 gap 插 space。
5. explicit whitespace 去重；separator 附 origin，聚合 quality counters。
6. multi-column/overlap/vertical 不確定時回退並聚合 reading_order_ambiguous。
7. 演算法使用 sort/sweep/index，避免無界 O(n²)。
8. 調整 threshold 必須同時跑 synthetic 與兩份 real-world assertions。

[輸出格式要求]
回報每個 heuristic 的輸入、decision、fallback、complexity 與 golden 變更。

[測試要求]
stage11_layout、real-world private corpus、criterion 或固定 benchmark harness、
workspace gate。

[驗收標準 DoD]
- AI 標題 required/forbidden fragments 通過。
- 台灣標題 required/forbidden fragments 通過。
- ContentOrder 與 legacy Layout 不被 Auto 改寫。
- 同輸入重跑結果 byte-identical。
- 單頁處理未出現可證實的 O(n²) path。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code；可建立 .claude/commands/run-private-corpus.md，只保存命令，
不得保存私人檔案或絕對路徑。

[任務範圍]
完成 Auto 的 line/order/space 推論與 quality metadata。

[檔案清單]
layout.rs、text.rs、text_model.rs、limits.rs、Stage 11 layout fixtures/tests、文件。

[具體步驟]
實作 script-aware boundary；每個推論保留 provenance；用 source ordinal 穩定排序；
不確定時減少推測；跑 synthetic 與 private real-world golden；分析複雜度。

[輸出格式要求]
提供 before/after 片段、quality counters、threshold 理由與 ambiguity cases。

[測試要求]
layout tests、private corpus、benchmark、完整 workspace gate。

[驗收標準 DoD]
兩份實檔文字目標通過；無 CJK 逐字空白；無英文單字內 artifact；結果 deterministic。
```

### 風險與回滾

- 風險：改善單欄文件卻破壞表格或多欄。  
  對策：ambiguous fallback、分群 fixtures、保留其他 modes。
- 回滾：將 Auto 暫時映射回 Layout；ContentOrder／Layout 不受影響。

## Stage 11.4：支援 marked content 與 ActualText

### 目標

處理 `BMC`、`BDC`、`EMC` 與 `/ActualText`，讓 ligature、替代字與輔助閱讀文字依規格
輸出一次，且不重複 enclosed glyphs。

### 前置條件

- Stage 11.3 Auto pipeline 能接受不同 TextOrigin。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex。

[任務範圍]
實作 marked-content stack、ActualText replacement、MCID metadata preservation。
不實作完整 Tagged PDF structure tree。

[需修改／新增的檔案]
- crates/pdf-core/src/content.rs
- crates/pdf-core/src/marked_content.rs
- crates/pdf-core/src/text_decode.rs
- crates/pdf-core/src/text.rs
- crates/pdf-core/src/limits.rs
- crates/pdf-core/tests/stage11_actual_text.rs
- fuzz/fuzz_targets/parse_content.rs
- docs/compatibility.md
- docs/errors.md

[具體步驟]
1. tokenizer/parser 接受 BMC/BDC/EMC operands。
2. property list 支援 direct dictionary 與 resource name resolution。
3. stack depth、property resolution 與 decoded replacement 長度受限。
4. 有效 ActualText 取代 enclosed sequence；nested precedence 不重複輸出。
5. invalid ActualText 聚合 actual_text_invalid 並回退原 glyph。
6. MCID 只保存，不宣稱 logical-order support。
7. 加入 valid、nested、missing EMC、wrong type、cycle、limit、truncation、fuzz cases。

[輸出格式要求]
列出 precedence、suppression 規則、warning aggregation 與 unsupported tagged features。

[測試要求]
stage11_actual_text、content fuzz build/smoke、workspace gate。

[驗收標準 DoD]
- replacement 恰好一次。
- malformed local property 不造成無界失敗或重複文字。
- stack、string、warning counts 有 limits。
- compatibility 明列 Partial。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code。

[任務範圍]
加入 ActualText 最小正確子集合，不擴張成完整標記文件導覽。

[檔案清單]
content、marked_content、text_decode、text、limits、ActualText tests、content fuzz、文件。

[具體步驟]
建立 bounded stack；解析 direct/named property；實作 nested replacement suppression；
invalid 時回退原 glyph並聚合 warning；保留 MCID metadata。

[輸出格式要求]
提供 nested examples、錯誤回退與限制表。

[測試要求]
ActualText suite、truncation、fuzz smoke、workspace gate。

[驗收標準 DoD]
有效 replacement 一次、無效資料可控回退、完整 structure tree 明確不在範圍。
```

### 風險與回滾

- 風險：suppression 範圍錯誤造成文字遺失。  
  對策：父子、兄弟、空區段、跨 Form 的 golden cases。
- 回滾：feature flag 暫停 ActualText consumption，但保留 content parsing。

## Stage 11.5：加入文件級 DecodeBudget 與 bounded object-stream cache

### 目標

避免同一 object stream 重複解壓，並確保所有實際 decoded bytes 都計入文件生命週期總量。

### 前置條件

- Stage 11.0 高壓縮 object-stream fixture 可重現。
- Stage 11.1 V2 limits 相容策略已確認。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex；這是安全敏感 stage，完成前不可只跑 happy-path。

[任務範圍]
新增 per-document DecodeBudget、ObjectStreamCache、limits、metrics 與 cache-aware resolution。
不得建立 global cache、unsafe code 或背景執行緒。

[需修改／新增的檔案]
- crates/pdf-core/src/document.rs
- crates/pdf-core/src/filter.rs
- crates/pdf-core/src/object_stream.rs
- crates/pdf-core/src/decode_budget.rs
- crates/pdf-core/src/object_stream_cache.rs
- crates/pdf-core/src/limits.rs
- crates/pdf-core/tests/stage11_decode_budget.rs
- crates/pdf-core/tests/stage3.rs
- docs/security.md
- docs/architecture.md
- docs/compatibility.md

[具體步驟]
1. 每個 PdfDocument 擁有 monotonic budget 與 bounded cache。
2. 實際 decode 完成後以 checked arithmetic 計量；cache hit 不重複計量。
3. eviction 後重新 decode 必須再次計量。
4. cache key 包含 object id/revision identity；value 含 validated member index/ranges。
5. bytes、entries、single stream、document total 各有 limits。
6. XRef/ObjStm 可放寬 ratio heuristic，但絕對上限不變；一般 stream 不可取得例外。
7. 測 cache hit/miss/eviction、budget exact boundary、cycle、large N/First、WASM profile。
8. 驗證 AI Index 全物件，記錄 decode count 與 peak cache bytes。

[輸出格式要求]
提供 threat model、accounting semantics、cache policy、實檔前後 metrics。

[測試要求]
stage11_decode_budget、stage3、stage9 boundaries、private AI validation、fuzz smoke、
workspace/MSRV/wasm32 gate。

[驗收標準 DoD]
- 同一未 eviction object stream 只實際 decode 一次。
- 超過任何 limit 回 limit_exceeded。
- cache 無法繞過 document budget。
- AI 45,151 objects 驗證成功。
- pdf-core 持續 forbid unsafe。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code；安全設計摘要應同步 docs/security.md，不要另立互相矛盾規則。

[任務範圍]
實作文件級解壓計量與有界 object-stream cache。

[檔案清單]
document/filter/object_stream/decode_budget/object_stream_cache/limits、Stage 3/9/11 tests、
security/architecture/compatibility 文件。

[具體步驟]
先寫 exact-boundary tests；再接入 budget；建立 deterministic cache；驗證 eviction 再解碼
會重新計量；限制 ratio 例外類型；跑 AI Index 全物件。

[輸出格式要求]
列 accounting invariant、cache invariant、攻擊情境與 before/after 數據。

[測試要求]
budget/cache/boundary tests、private AI validation、fuzz、MSRV、WASM、workspace gate。

[驗收標準 DoD]
安全上限不可繞過、無 global state、AI 實檔通過、所有現有安全測試通過。
```

### 風險與回滾

- 風險：新的總預算使既有大型文件較早失敗。  
  對策：0.2.0 release note、明示 limits、以實檔校準 default，但不靜默無限放寬。
- 回滾：保留 budget instrumentation，關閉 cache integration；不得回到無 document budget。

## Stage 11.6：補齊整合、回歸、邊界與跨介面測試

### 目標

這是固定倒數第二 stage。凍結功能，補齊 synthetic、private corpus、cross-binding、fuzz、
效能與安全回歸；不得再加入新 feature。

### 前置條件

- Stages 11.0–11.5 各自 Definition of Done 已完成。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex。此 stage 只修測試揭露的缺陷，不擴張範圍。

[任務範圍]
完成 Stage 11 全矩陣驗證、golden review、跨介面一致性、fuzz 與 benchmark。

[需修改／新增的檔案]
- crates/pdf-core/tests/stage11_*.rs
- crates/pdf-cli/tests/stage11_*.rs
- bindings/python/tests/test_stage11.py
- bindings/wasm/tests/stage11_web.rs
- tests/fixtures/stage11/*
- tests/real-world/*
- fuzz/fuzz_targets/*
- DEVNOTE.md

[具體步驟]
1. 跑 technical spec 的所有 synthetic cases。
2. 兩份 private corpus 跑 inspect/validate/三 modes；核對 required/forbidden fragments。
3. 比對 Rust/CLI/Python/WASM text、pages、warnings、quality。
4. 跑 exact-boundary、truncated-prefix、malformed nesting、warning amplification。
5. 跑 fuzz build 與 bounded smoke；保存 runs/crashes。
6. 建立 before/after benchmark，記錄工具鏈與硬體，不用不穩定絕對時間作唯一 gate。
7. 人工 review 所有 golden 變更；不得批次接受。
8. 執行完整 stage gate、Rust 1.88、cargo deny、cargo audit。

[輸出格式要求]
產出 validation matrix：命令、結果、證據、未涵蓋項目、已知限制。

[測試要求]
所有 workspace tests、private corpus、Python wheel、browser package、fuzz、security、
MSRV、supply-chain checks。

[驗收標準 DoD]
- FID-01～REG-01 全部有當次輸出證據。
- 兩份實檔三 modes 都成功。
- 四端輸出一致。
- 無 panic、無 unsafe、無未界定新增資源。
- 未通過項目不得以 known issue 取代修復，除非規格明確列為 out of scope。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code；可將完整驗證命令整理為
.claude/commands/validate-stage11.md。

[任務範圍]
凍結功能，完成整合、回歸、邊界、效能、安全與跨介面驗證。

[檔案清單]
所有 Stage 11 tests/fixtures/private manifest/fuzz targets 與 DEVNOTE。

[具體步驟]
依 acceptance ID 建矩陣；執行 synthetic/private/cross-binding；檢查 golden diff；
跑 fuzz/MSRV/security/package；只修根因，不新增未規劃功能。

[輸出格式要求]
提供逐項 PASS/FAIL、命令輸出摘要、風險與缺口。

[測試要求]
完整 release candidate validation。

[驗收標準 DoD]
所有必要 gate 通過，validation 可由另一位維護者重現。
```

### 風險與回滾

- 風險：private corpus 在 CI 不存在。  
  對策：分開記錄 public gate 與 private gate；skipped 不等於 passed。
- 回滾：回到最後一個通過 Stage 11.5 gate 的狀態，逐項重做，不刪除失敗測試。

## Stage 11.7：完成文件化、套件驗證與交付

### 目標

這是固定最終 stage。更新所有 public 文件、範例、相容矩陣、錯誤碼、限制與 release
evidence，產出 Rust／CLI／Python／WASM 0.2.0 release candidate。

### 前置條件

- Stage 11.6 validation matrix 全部通過。

### Codex Instructions

```text
[建議貼用方式]
直接貼給 Codex。只有文件與套件驗證通過後才可標記完成。

[任務範圍]
完成 0.2.0 文件、examples、package、release checklist 與 DEVNOTE。
不再更動 parser 行為，除非文件驗證揭露真實 API 錯誤。

[需修改／新增的檔案]
- README.md
- docs/architecture.md
- docs/compatibility.md
- docs/errors.md
- docs/security.md
- docs/release.md
- docs/text-fidelity.md
- bindings/python/pyproject.toml
- bindings/wasm/package.json
- Cargo.toml / crate Cargo.toml（只做已核准版本更新）
- DEVNOTE.md

[具體步驟]
1. 文件說明三 modes、legacy compatibility、ActualText、quality metadata、limits。
2. 明列 rendering/OCR/encryption/repair/PDF 2.0 的限制。
3. 加入四端可執行 examples，輸出與實際 signature 一致。
4. 更新錯誤碼/warnings 與 machine-readable contract。
5. build/test Rust crates、Python wheel、WASM package；檢查 declarations。
6. 跑 dependency tree，確認沒有 forbidden PDF parser。
7. 更新 release evidence、fixture manifest hash、DEVNOTE snapshot/validation/landmines。

[輸出格式要求]
交付檔案清單、package paths、驗證命令、已知限制與升級指南。

[測試要求]
完整 Stage 11.6 gate，加 doctest、README examples、wheel install test、WASM declaration audit。

[驗收標準 DoD]
- 文件與實際 API 一致。
- legacy 升級路徑清楚。
- 四種交付物可建立與使用。
- dependency/security/MSRV gate 通過。
- DEVNOTE 可支援下一次 session resume。
```

### Claude Code Instructions

```text
[建議貼用方式]
直接貼給 Claude Code。若專案使用 CLAUDE.md，只加入「先讀 DEVNOTE 與 AGENTS.md」的
handoff 指引，不重複整份文件。

[任務範圍]
完成文件、examples、版本、套件與 release evidence；功能凍結。

[檔案清單]
README、docs、package manifests、Cargo manifests、DEVNOTE。

[具體步驟]
對照實際 signature 更新四端說明；加入升級指南；驗證 wheel/WASM/Rust/CLI；
檢查 forbidden dependencies、security、MSRV；記錄完整證據。

[輸出格式要求]
列 packages、commands、PASS evidence、known limitations、upgrade notes。

[測試要求]
release candidate 全驗證與文件 example audit。

[驗收標準 DoD]
0.2.0 release candidate 可重建、可安裝、可使用、可追溯，沒有文件漂移。
```

### 風險與回滾

- 風險：版本號更新但 package 尚未全部通過。  
  對策：版本更新放最後，任何 package 失敗都不標記 release complete。
- 回滾：恢復版本 metadata；保留已驗證的文件與測試修正。

## 最終 Definition of Done

- 技術規格 FID-01～REG-01 全部通過。
- `Auto` 修正兩份實檔的已知英文與 CJK 空白 artifact。
- legacy 呼叫維持原行為；新 V2 API 四端一致。
- ActualText replacement、font metrics、layout、decode budget、cache 皆有 valid、invalid、
  boundary 與 fuzz evidence。
- 兩份實檔未被提交到 repository。
- Rust 1.88 MSRV、workspace gate、Python wheel、WASM package、security/supply-chain checks
  全部通過。
- README、architecture、compatibility、errors、security、release、text-fidelity 與 DEVNOTE
  已同步。
