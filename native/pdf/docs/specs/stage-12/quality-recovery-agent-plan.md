# 規格整理 v 1.2.0

## Codex / Claude Code 分階段開發計畫

### 共通不變條件

所有階段都必須遵守根目錄 `AGENTS.md`、DEVNOTE-first handoff、`pdf-core` 唯一 PDF-aware owner、`#![forbid(unsafe_code)]`、bounded input-derived work、stable error codes、不得加入既有 PDF parser。PyMuPDF 只可用於離線 shadow；預設 provider 在所有 global gates 通過前不得改動。

### Stage 7.0：凍結品質契約與基準

目標：把 Stage 6D 後的每文件指標、品質門檻、privacy 規則與 rollback 邊界寫成可執行契約。

Codex Instructions

```text
[建議貼用方式] 直接貼給 Codex；長期不變條件同步到根 AGENTS.md，動態數據寫 DEVNOTE.md。
[任務範圍] 新增 Stage 7 technical/nontechnical/agent-plan 與 contract test；不改 parser。
[檔案] docs/specs/stage-12/quality-recovery-*.md、crates/pdf-core/tests/stage12_contract.rs、tests/fixtures/stage12/。
[步驟] 凍結 7-doc per-case F1、global gates、privacy 禁止欄位、rollback；加入 include_str contract。
[輸出] 規格與 privacy-safe DoD，不落地 private IR/text/path/URL。
[測試] stage12_contract、cargo fmt --check、workspace Clippy -D warnings、workspace tests。
[DoD] 契約可執行、門檻無模糊詞、預設 provider 明確不變。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code；持續規則寫入 CLAUDE.md 或 .claude/CLAUDE.md。
[範圍/檔案] 只建立 Stage 7 規格、DoD、contract test；不要改 Rust extraction rules。
[步驟] 讀 DEVNOTE 與 Stage 6D report，凍結 per-case/global metrics、privacy、rollback。
[輸出] 三份完成稿與可執行 contract assertions。
[測試/DoD] contract、format、Clippy、workspace tests 全過；PyMuPDF default 不變。
```

風險與回滾：規格若和 Stage 6D 報告不一致就拒絕進 7.1；回滾僅移除 Stage 7 文件／contract。

### Stage 7.1：建立逐頁 privacy-safe 差距定位

目標：找出文字 F1 缺口集中頁與原因類別，不保存原文。

Codex Instructions

```text
[建議貼用方式] 直接貼給 Codex。
[任務範圍] 新增離線 page quality profiler；不修改 pdf-core 或 Documa mapping。
[檔案] tools/stage12_page_quality_diff.py、crates/pdf-core/tests/stage12_contract.rs、tests/fixtures/stage12/stage7a-dod.md。
[步驟] provider worker 分離執行；temporary counters 對齊頁碼後計算 precision/recall/F1、bigram、Unicode category delta、counts/warning codes；parent 只寫 aggregate/page metrics；加入 cleanup/privacy/self-test。
[輸出] target/stage12-stage7a-page-quality/report.json，不含 text、character keys、path、URL、IR。
[測試] self-test、7-doc one-run localization、privacy audit、contract、fmt、Clippy、workspace tests。
[DoD] global/per-document totals重現 Stage 6D；worst-page ranking deterministic；temporary data 成功/失敗皆刪除。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code；可建立 .claude/commands/stage7-page-diff.md 重複執行。
[範圍/檔案] 實作 privacy-safe page differential worker/parent 與 DoD；不碰 parser。
[步驟] 分離 provider process、逐頁短生命週期 counters、只輸出類別化差距、加入 privacy denylist 與 cleanup。
[輸出] deterministic JSON report + concise console summary。
[測試/DoD] self-test、private 7-doc aggregate parity、無 private IR/text/path/URL、所有 repository gates PASS。
```

風險與回滾：character counters 可能洩漏原文；只能存在 temporary worker output，final report 僅保留分類／分數。失敗即刪除工具與報告，不影響產品。

### Stage 7.2：修復文字完整性根因

目標：以 7.1 原因 clusters 為順序，一次修一個可公開重現的文字缺失／重複根因。

Codex Instructions

```text
[建議貼用方式] 每個 root cause 單獨貼給 Codex，不要合併多個 heuristic。
[範圍] 只改 pdf-core 的確定性 extraction rule；不以 PyMuPDF runtime 複製答案。
[檔案] crates/pdf-core/src/{text,font,cmap,content,layout_ir}.rs 中最小必要集合、對應 tests、公開 fixture generator、stage7b-dod.md。
[步驟] 從 worst cluster 建最小 synthetic failing PDF；先證明 failure；實作 bounded rule；驗證 content/layout/auto與四前端；重跑 page diff。
[輸出] 每個修正都有 fixture、rule ID、warning/error、before/after aggregate。
[測試] focused exact/boundary/malformed、private corpus all modes、fmt/Clippy/workspace/wasm/wheel/Node。
[DoD] global character F1 ≥0.995；既有 ≥0.995 cases regression ≤0.0005；silent loss 0；memory/speed gates維持。
```

Claude Code Instructions

```text
[建議貼用方式] 每個 root cause 開獨立 Claude Code task；長期規則留在 CLAUDE.md。
[範圍] 先 fixture 後修 pdf-core；不得加入 PDF-aware dependency或 corpus-specific file-name/page special case。
[檔案] 最小 core module、focused tests、fixture generator、DoD。
[步驟] 重現→根因→bounded fix→四前端 parity→private aggregate驗證。
[輸出] 可審查的單一根因 diff 與數據。
[測試/DoD] F1、silent-loss、determinism、memory、speed與所有 stage gates PASS。
```

風險與回滾：oracle 可能包含隱形／重複文字；無人工／PDF語意證據的差異不得盲目追平。每個根因可獨立回滾。

### Stage 7.3：建立 human reading-order gold

目標：用人工標註取代 tagged proxy 作為最終 human-order truth。

Codex Instructions

```text
[建議貼用方式] 直接貼給 Codex；人工標註本身由使用者/reviewer完成。
[範圍] 建 schema、公開 fixtures、annotation validator/scorer；不先改 ordering rule。
[檔案] tools/stage12_order_gold.py、tests/fixtures/stage12/quality/order/、private-order-manifest.example.json、quality-recovery-technical.md。
[步驟] 定義 node IDs、precedence pairs、main-flow membership、artifact roles；覆蓋單/多欄、sidebar、跨欄 heading、list、caption、table、furniture、旋轉/直排；加入 inter-rater欄位與缺標拒絕。
[輸出] public gold + private template；private labels不進repo。
[測試] schema invalid/duplicate/cycle/missing fixture、exact scorer、privacy、contract。
[DoD] 公開 gold reviewer一致；private gold完整可驗證；無 gold 時 gate 顯示 BLOCKED 而非 PASS。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code；可建 .claude/skills/order-gold-review/SKILL.md 支援重複審核。
[範圍] 只建立 gold schema/validator/scorer與公開反例，不修改排序演算法。
[步驟] 定義 precedence/main-flow/artifact label、validator、scorer、人工審核流程與privacy規則。
[輸出] 可人工編輯的 template、明確錯誤、aggregate report。
[測試/DoD] malformed labels全拒絕、公開 scorer exact、private labels不提交、缺標必為BLOCKED。
```

風險與回滾：human order 有主觀性；至少雙人 review 爭議頁，保留 adjudication note 但不含 private text。

### Stage 7.3A-D：把逐節點標註改為人類 block 審閱

舊 v6 click-per-node workbench 只保留為歷史工程證據，不得直接投入人工
審閱。Stage 7.3 依下列順序重新開 gate，任何子階段失敗都不得跳級。

| 子階段 | 交付 | 驗收條件 | 後續解鎖 |
|---|---|---|---|
| 7.3A BBox fidelity | 字型 metrics、有效 Tm/CTM 字高、可見 overlay 裁切 | 公開 scaled-text 回歸、私人 28 頁幾何統計/視覺 QA、文字與速度無回退 | 7.3B |
| 7.3B Block gold v2 | block membership、block role、block precedence、validator/scorer | 每個 node 恰屬一個 block、block DAG 完整、privacy/malformed fixtures、exact scorer | 7.3C |
| 7.3C Blind brush UI | brush/eraser、merge/split、單 stroke undo、neutral reviewer mode | 不揭露 Rust role/order；桌機/平板/手機與 pointer/keyboard QA；Reviewer A/B 隔離 | 7.3D |
| 7.3D Pilot and audit | 雙人 pilot、時間/錯誤率、adjudication、文件/回歸 | pilot 可完成且 validator 接受；兩位真人完整 gold 前仍為 BLOCKED | 7.4 gate review |

7.3B 的持久化資料是 node ID 到 block ID 的 membership 與 block 間偏序；
brush stroke 只是一種輸入手勢，不保存 raster mask。Scorer 先對每對 gold
blocks 計算 candidate node-order concordance，再以 block pair 等權平均，避免
大 block 因 node 數較多而壟斷分數。Artifact、page_header、page_footer、
page_number 是 block role，不要求 reviewer 細排其內部 bbox。

7.3C reviewer mode 必須 blind：所有未標註框使用中性色，不顯示 Rust role、
inferred order、confidence 或 feature hint。Pointer down 到 pointer up 是一個
undo transaction；stable block ID 由 page-local commit order 決定。Reviewer A/B
維持獨立 locked workspace，adjudicator 只能在兩份 reviewer-only manifest 都
通過 schema/identity 驗證後比較。

風險與回滾：BBox 仍是字型幾何近似而非 glyph outline；Type3 FontMatrix 與
真正直排保留明確 fallback。任何 schema/UI 變更都不得修改 parser reading
order，也不得把 private labels、文字、圖片或路徑提交進 repository。

### Stage 7.4：改良 bounded inferred order

目標：依 gold inversion 類型改善多欄、sidebar、caption、跨欄 heading 與 furniture，而不覆寫有效 tagged order。

Codex Instructions

```text
[建議貼用方式] 逐一 inversion class 貼給 Codex。
[範圍] pdf-core reading_order only；不改 text extraction，不用 ML/LLM。
[檔案] crates/pdf-core/src/reading_order.rs、stage12_reading_order.rs、公開 order fixtures、stage7d-dod.md。
[步驟] 凍結 tagged/source優先序；建立反例；改 bounded XY-cut/line/zone rule；輸出 order_source/confidence/fallback；驗證 artifacts/main flow。
[輸出] pairwise/Kendall-style/adjacency/artifact metrics與rule provenance。
[測試] exact boundaries、rotation/depth fallback、tag conflict、四前端、private gold、full gates。
[DoD] human gold pairwise ≥0.95、tagged proxy ≥0.95、artifact FP ≤1%、text SHA不變。
```

Claude Code Instructions

```text
[建議貼用方式] 每類 inversion 開獨立 task。
[範圍] deterministic geometric/structural order；不得讓 geometry 靜默改 author tagged order。
[檔案] reading_order core + focused tests/fixtures/DoD。
[步驟] failing gold→bounded rule→confidence/fallback provenance→cross-front-end/private score。
[輸出] 指標與可回溯 rule ID。
[測試/DoD] pairwise/tagged/artifact gates PASS；文字、table、image、memory不回歸。
```

風險與回滾：全域 y/x sort 可能破壞多欄；每個 rule 有觸發條件與 fallback，可按 rule獨立回滾。

### Stage 7.5：建立並驗證 table/image gold

目標：補齊目前無法判定的 private table/image correctness gates。

Codex Instructions

```text
[建議貼用方式] 工具實作貼給 Codex；人工標註由 reviewer 執行。
[範圍] annotation schema、validator、scorer、sampling plan；不由 parser自產gold。
[檔案] tools/stage12_table_image_gold.py、private-table-image-manifest.example.json、公開 exact fixtures、stage7e-dod.md。
[步驟] table標rows/cols/spans/headers/cells；image標occurrence/figure/artifact/caption link；定義sampling與double review；計算TEDS-S與precision/recall/F1。
[輸出] privacy-safe aggregate；缺gold為BLOCKED。
[測試] malformed/partial/cross-page labels、covered cells、duplicate image IDs、exact scorer。
[DoD] table TEDS-S ≥0.90；image occurrence與caption-link F1各≥0.95；公開 fixtures維持exact。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code；可建立 annotation-review skill，但不得內嵌private labels。
[範圍/檔案] 建 table/image label template、validator、scorer與review SOP。
[步驟] schema→公開 exact→private template→double review→aggregate score。
[輸出] 不含文字/圖片bytes/URL/path的報告。
[測試/DoD] invalid labels拒絕；gold完整；TEDS-S與image gates達標或誠實FAIL/BLOCKED。
```

風險與回滾：人工成本高且 provider counts不可互比；工具可先完成，但無 labels 不得開 gate。

### Stage 7.6：補齊整合、回歸與邊界測試

目標：倒數第二階段，執行完整跨介面／私有 corpus／安全／效能矩陣。

Codex Instructions

```text
[建議貼用方式] 直接貼給 Codex並持續到所有gate有終態。
[範圍] 測試與修正不完整工作；不新增功能或放寬門檻。
[檔案] existing tests、benchmark reports、stage7f-dod.md；只在失敗根因必要時修source。
[步驟] Rust focused/full/doctest/MSRV/fuzz；native/wasm Clippy；wheel/Node；Documa focused/full/Ruff/doctor；7-doc三次shadow；order/table/image gold；privacy/security。
[輸出] PASS/FAIL/BLOCKED matrix與exact commands/hashes。
[測試] 所有上述矩陣。
[DoD] 任何 failed/blocked gate 都禁止default cutover；無 flaky/skip 被誤報PASS。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code作release-candidate audit。
[範圍] 只補測試/修回歸；不改門檻、不隱藏warning。
[步驟] 執行完整矩陣，逐項記command/output/hash，失敗回到所屬stage修正後重跑。
[輸出] 可追溯gate matrix。
[測試/DoD] 全部required gate PASS；否則明確NO-GO/BLOCKED。
```

風險與回滾：長時間測試可能被外部環境打斷；記錄case級進度但每次measurement group仍需完整重跑。

### Stage 7.7：文件化、切換與交付

目標：最終階段，只在所有 gate PASS 時切換 Documa 預設 parser；保留 renderer 與 rollback。

Codex Instructions

```text
[建議貼用方式] 直接貼給 Codex；若任一gate非PASS，改為文件化NO-GO，不做切換。
[範圍] provider default、migration/rollback docs、release evidence；不移除PyMuPDF renderer/OCR。
[檔案] Documa registry/README/CHANGELOG/DEVNOTE、rust-pdf-parser README/spec/DoD/release docs。
[步驟] 確認gate matrix；切default或記NO-GO；保留pdf_provider="pymupdf"；文件化座標/order/限制/監控/rollback；重跑兩repo final gates。
[輸出] release/NO-GO decision、操作與回復說明、artifact hashes。
[測試] default/explicit provider tests、full Documa/Rust/release package matrix。
[DoD] 只有全PASS才default=rust；renderer仍PyMuPDF；一個設定可rollback；文件與版本一致。
```

Claude Code Instructions

```text
[建議貼用方式] 直接貼給 Claude Code作final handoff。
[範圍] 根據final gate切換或記錄NO-GO；保留rollback與renderer。
[步驟] 審gate→最小default變更→migration/rollback/known limits→全套測試→release evidence。
[輸出] 可直接交付的決策與文件。
[測試/DoD] default與explicit provider皆可用；任何gate失敗則不得切換；兩repo驗證全過。
```

風險與回滾：生產文件可能出現 gold 未涵蓋版面；先保留顯式 provider override，Stage 8 只在真實 production evidence 後才考慮移除 rollback。
