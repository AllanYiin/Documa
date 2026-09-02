# 模型資產與授權界線

## Goal｜目標

最初版模型含受限來源，不得公開散布；目前使用的模型已由維護者確認為可散布版本。現行版本以維護者自行蒐集的文本為來源，先經中研院分詞工具處理，再由維護者人工校閱與修正結果。現行 `dict.bin`、`hmm_bmes.bin`、`hmm_pos.bin` 可隨網站、wheel、WASM bundle 或 release 散布。

此授權結論只適用於下列 SHA-256 所識別的現行模型；舊模型、外部語料與未記錄來源的衍生統計仍不得混入發布物：

- `dict.bin`: `8B29D53505374518B81DB1481F658B078068C4909B3FEACDFC9F0A4E1B1DB056`
- `hmm_bmes.bin`: `D15F411F506B68300EEBB343A10C70268CA6DC389C3D6A439FF61ABBCC30FAFA`
- `hmm_pos.bin`（LXA3 i16 定點量化）: `791EBB87CDB13D9CD0BEB53B1D2E5E4BA3B2A284EB8BE0E8279529080E7499E2`

## 執行期需要的檔案

```text
assets/
├── dict.bin
├── hmm_bmes.bin
├── hmm_pos.bin
└── affect.bin       # 可選
```

- `dict.bin`：僅含多字詞的詞典、自動機與正頻率
- `hmm_bmes.bin`：LXA2 二階 BMES 發射／轉移與 `<UNK>` 平滑
- `hmm_pos.bin`：LXA3 二階 fixed-boundary POS 與完整詞彙 `P(tag|word)`；機率陣列以 i16 定點儲存，載入時還原為 f32
- `affect.bin`：由人工維護、可再散布的 taxonomy 與情感詞典轉換；缺少時不影響分詞與 POS

檔案格式由 `lingxi-core` 定義，`tools/lingxi-convert` 負責從本機 JSON 資料轉換。

## 發布版本控管

- 發布前必須核對上述三個模型的 SHA-256；雜湊不同即視為未審查版本。
- `assets/affect.bin` 可由已提交來源重建，仍維持 generated artifact 管理。
- 外層舊專案的 `ModelingData/`、`ModelingData2/`、Resources、原始文本與訓練中間資料不隨模型散布。
- `dist/`、wheel、WASM 與網站封裝可包含上述已核准模型，但仍須檢查 staged diff，避免帶入其他本機資料。

## Prerequisites｜前置條件

- 合法取得且可供本機使用的三個模型檔
- Rust stable toolchain
- 若要建 wheel：Python 3.9+ 與 maturin

## Procedure｜本機使用

### Step 1：準備模型

內部維護者可把合法取得的三個模型放在 `assets/`。下列命令會執行包含真實模型的整合測試：

```bash
cargo test --workspace --locked
```

若資產不存在，模型相關測試會跳過，核心純單元測試仍會執行。

### Step 2：驗證或封裝

Python wheel 建置會把本機資產複製到 package：

```bash
python tools/build_wheel.py
```

產出的 wheel 可包含雜湊吻合的現行模型；若模型雜湊不同，必須先完成 provenance 與散布權限審查。

## Verify｜Git 發布前檢查

```bash
git status --short --ignored
git ls-files
git diff --cached --stat
```

確認模型雜湊吻合、staged files 不含原始文本、語料或未審查模型。若舊版受限模型曾經誤加入 Git 歷史，單純新增 `.gitignore` 不會移除歷史內容，必須先停止發布並清理 Git history。

## Troubleshooting｜故障排除

- 若模型雜湊與核准清單不符，先停止 commit 並確認模型來源。
- 若舊版受限模型曾進入 Git 歷史，新增 ignore 規則不足以補救；停止發布並先清理 history。
- 若無資產的測試不是「跳過」而是失敗，檢查是否新增了未受保護的模型依賴測試。

## 未來模型更新條件

1. 記錄來源、人工校閱方式、版本、轉換步驟與輸出雜湊。
2. 確認來源與衍生成果可散布。
3. 重跑黃金集、語料不變量、效能與四種 binding 驗證。
4. 經發布者確認後，更新本文件的核准雜湊。
