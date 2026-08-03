# 規格整理 v 1.2.0

主題：Stage 11 Text Fidelity、閱讀順序與文件級資源治理  
狀態：Stage 11.0～11.7 已完成並通過 0.2.0 release validation  
確認日期：2026-07-28

## 技術規格文件

[technical-spec.md](technical-spec.md) 定義：

- `ContentOrder`、`Layout`、`Auto` 三種模式與 legacy compatibility。
- ActualText／ToUnicode／fallback／U+FFFD 的文字 precedence。
- font metrics、positioned glyph、script-aware whitespace 與 deterministic layout。
- document-wide DecodeBudget 與 bounded object-stream cache。
- private real-world corpus、synthetic fixtures、quality metadata 與 acceptance IDs。

## 非技術規格文件

[nontechnical-spec.md](nontechnical-spec.md) 從使用者可看到的結果說明：

- 三種文字整理方式的差異。
- 英文單字與 CJK 多餘空白的改善目標。
- 成功、可繼續但需注意、無法繼續時的提示。
- 檔案隱私、目前限制與完成後交付內容。

## Codex／Claude Code 分階段開發計畫

[agent-plan.md](agent-plan.md) 將 Stage 11 拆成八個已完成、可測、可回滾 substages：

Stage 11.0：鎖定契約、基準與 corpus schema。
Stage 11.1：新增 V2 抽取模式與跨介面契約。
Stage 11.2：保存 glyph geometry 並解析 font metrics。
Stage 11.3：實作 script-aware Auto layout。
Stage 11.4：支援 marked content 與 ActualText。
Stage 11.5：加入文件級 DecodeBudget 與 bounded object-stream cache。
Stage 11.6：補齊整合、回歸、邊界與跨介面測試。
Stage 11.7：完成文件化、套件驗證與交付。

## 實作與驗證證據

- [Stage 11.6 validation matrix](../../../tests/fixtures/stage11/validation-matrix.md)：FID-01～REG-01、兩份 private corpus、四端一致性、fuzz、benchmark、MSRV 與 supply-chain。
- [0.2.0 release runbook/evidence](../../release.md)：Rust core crate、CLI archive、Python wheel、browser WASM paths、SHA-256 與 package smoke。
- [0.2.0 migration guide](../../migration-0.2.md)：legacy compatibility、V2 opt-in、ParseLimits 與 rollback。

## 已採用的決策

1. 採用三種抽取模式；舊 `layout` 行為先維持相容，0.2.0 才引入新的 V2 介面。
2. `/ActualText` 與 marked content 列為 Stage 11 P0。
3. 兩份實檔只保存經驗證的 SHA-256 與 golden assertions，不提交原始 PDF。
4. Stage 11 以 PDF 1.7 行為為主要驗收；PDF 2.0 normative conformance 另案研究。

## 研究來源

研究以 Adobe PDF Reference／Accessibility、Apache PDFBox、pypdf、pdfminer.six 與
Mozilla PDF.js 官方文件或官方 repository 為主，查閱日期為 2026-07-28。來源只用於
行為與測試設計，不成為 parser dependency；完整連結見
[technical-spec.md](technical-spec.md#研究來源)。
