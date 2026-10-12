# 回歸驗證與私人語料操作

## 目標與前置條件

供 0.2.0 維護者驗證 parser、Python facade 與 CLI。需先建置／安裝 wheel，並安裝 pytest、jsonschema、xlwt、xlrd、python-docx、openpyxl、python-pptx、psutil。
在 Documa monorepo 中，以下命令的工作目錄為 `native/office`。

## Step 1：公開程式碼驗證

```powershell
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build -p office-cli --locked
python -m pytest -q tests
cargo check --manifest-path fuzz/Cargo.toml --bins
python scripts/benchmark_scaling.py --out reports/scaling-local.json
```

公開測試使用專案產生的合成 fixtures。沒有設定私人語料時，真實文件參數化測試應明確 skip；不能把 skip 視為真實文件驗證通過。
大型測試以 10000 列、16384 欄範圍、20000 populated cells 驗證稀疏抽取，報告記錄時間與 peak RSS，並不宣稱常數記憶體串流。
`reports/` 是本地執行輸出並已列入 ignore。

## Step 2：選用本機私人語料

公開 repo 不包含真實 Office 文件、其抽取結果、golden 或授權收據。`fixtures/real-world/manifest.json` 僅為空白範例。
工具使用 `DOCUMA_OFFICE_CORPUS_DIR` 指定的目錄；未指定時預設 `.local-corpus/`，已從 Git 與 source distribution 排除。

```powershell
$env:DOCUMA_OFFICE_CORPUS_DIR = "D:/private-office-regression"
python scripts/corpus.py add PATH_TO_AUTHORIZED_FILE.docx --license LICENSE_OR_PERMISSION_ID --source SOURCE_REFERENCE --permission-file PATH_TO_PERMISSION.txt --reviewed
```

`add` 會將文件、雜湊、來源、授權及 proposed golden 寫入私人目錄。維護者需對照來源核對內容後，才將該 manifest entry 的 `golden_reviewed` 設為 `true`。
這個動作只建立本地測試資料，不代表允許將文件或抽取內容公開上傳。

```powershell
python scripts/corpus.py check --require-real-world
python scripts/verify_real_world.py --require-real-world --out reports/real-world-parity.json
```

核對使用獨立 python-docx／openpyxl／xlrd／python-pptx 讀取器。沒有語料時，一般驗證回報 `SKIPPED`；指定 `--require-real-world` 則以非零退出。
本機報告與私人目錄不應加入公開提交。

## CI 與發布條件

Documa 根目錄的 `.github/workflows/office-parser.yml` 執行三平台與 Python 3.9／3.13 的 Office 檢查，保存一般程式碼測試與合成 benchmark artifacts。
CI 不下載私人文件；預設空白 corpus 不冒稱通過真實文件品質 gate。
手動設定 `require_release_ready=true` 會要求真實語料 gate，未提供資料時應保持阻擋。
Linux sanitizer fuzz 需由其實際執行結果判定，不能使用 Windows compile check 代替。

## 故障排除（Troubleshooting）與回復

- 重建 native binding 後再執行測試，避免誤用舊版 extension。
- Windows sandbox 的暫存 atomic replace 若遭拒，使用工作區內、Git repo 外的測試暫存目錄；並行寫入仍受阻時應明示驗證限制。
- Golden mismatch 先查來源語意，不要直接重寫 baseline。
- 回滾 parser 與索引時依 [migration guide](migration-0.2.md) 操作。

維護者更新工具參數時應同步更新本頁、CI 與測試；API 欄位見 [reference](api-reference.md)。
