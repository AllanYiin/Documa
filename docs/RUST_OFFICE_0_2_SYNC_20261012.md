# Rust Office 0.2.0 程式碼同步

本次將 Office parser 的 P0–P2 程式碼對應至 `native/office` 與 `src/rust_office`，以最新 main `64c1480` 為基底，保留同時進入 main 的 PDF 與 Documa 0.9.0 更新。
提交依核心、PPTX、DOCX、試算表、Python/Documa binding、CLI、測試工具、CI、文件的順序拆分。

## 公開範圍

公開提交只包含程式碼、合成測試與必要使用文件。原始私人 Office 文件、golden／抽取內容、授權收據、來源工作區 transcript／DEVNOTE 及本機執行報告，均不納入這批提交或其祖先提交。
語料工具改用未納入 Git 的 `.local-corpus/` 或 `DOCUMA_OFFICE_CORPUS_DIR`。沒有私人語料時明確 skip，必要發布 gate 仍回報不具備資料。

## 整合

- Documa exact binding version 為 0.2.0，IR 契約維持 `office-layout-v1`。
- Native error 轉接支援舊 JSON envelope 與新的 typed error 屬性。
- Office options 經 `ParseOptions.metadata` 轉接；facade 與 `py.typed` 同步進入 Documa package。
- 三平台 Office workflow 使用 `native/office` 工作目錄，與既有 PDF workflow 分開。

## 驗證

Office 本體加 Documa binding／adapter：116 passed、1 skipped；skip 為刻意不散布的選用私人語料測試。
這次未宣稱 package release 或跨平台 CI 全部完成。Windows instrumented fuzz 仍受 MSVC sanitizer coverage linker 限制，compile check 不能代替 Linux fuzz 實跑。

原子解析仍保留完成的 IR；列批次是 worksheet event 中的 table blocks。Excel 顯示格式不完整時保留 raw value 並標示近似，不重新計算公式。
