# Documa Expert Review Log

This log treats the current codebase as Draft v0 and records expert-review
rounds requested after Stage 9.

## Review Council

- [文件理解架構師｜林佳蓉] 專長是把 parser-neutral IR、layout reasoning、relation graph 拆成可維護的 processing boundary。
- [PDF/parser 顧問｜張哲維] 專長是檢查 adapter 是否洩漏底層 parser 物件，以及 PDF 低階訊號是否被保留給後續修復。
- [RAG 工程師｜黃柏翰] 專長是檢查 chunk、metadata、provenance 是否足夠支援檢索與回答追溯。
- [MCP/tool-calling 整合｜陳怡安] 專長是檢查工具 schema、structured result、OpenAI/MCP wrapper 是否一致。
- [API 使用者代表｜李雅婷] 專長是從 SDK/CLI 使用者角度找出難用、難猜或容易誤用的介面。
- [QA/benchmark 負責人｜吳宗翰] 專長是把品質要求轉成可執行 regression checks。
- [Release/security reviewer｜周明哲] 專長是檢查 package、CI、錯誤輸出與檔案寫入邊界。

## Round 1 Review Summary

| Reviewer | Required Change | Action Taken | Status |
|---|---|---|---|
| MCP/tool-calling 整合 | MCP server exposed fewer tools than `documa_tool_schemas()`, which would make docs/schema drift from actual MCP capability. | Added `documa_benchmark` and `documa_doctor` to the optional FastMCP wrapper. | Done |
| MCP/tool-calling 整合 | OpenAI function-calling users had no native `type=function` schema helper. | Added `openai_tool_schemas(strict=False)` with optional `strict=True` conversion and `additionalProperties=false`. | Done |
| API 使用者代表 | `process_document_tool(export_formats=...)` was schema-compatible as an array but brittle for direct callers passing one string. | Coerced a single string into a one-item list. | Done |
| QA/benchmark 負責人 | The new schema/tool behavior needed tests before more review rounds. | Added tests for OpenAI schema conversion and single-string export format handling. | Done |

## Round 2 Review Summary

| Reviewer | Required Change | Action Taken | Status |
|---|---|---|---|
| MCP/tool-calling 整合 | FastMCP wrapper still did not fully mirror the schema surface for `documa_process` and `documa_benchmark`. | Added `export_formats` to MCP `documa_process` and `out` to MCP `documa_benchmark`. | Done |
| Release/security reviewer | Tool-call boundaries should not leak unexpected Python exceptions to agents. | Added a final exception guard in `call_documa_tool()` that returns structured `status=error` content. | Done |
| QA/benchmark 負責人 | Invalid argument paths should be covered by regression tests. | Added a direct tool-call test for invalid `documa_export` arguments. | Done |

## Sources Checked

- Model Context Protocol, "SDKs" and tools documentation, accessed 2026-05-11.
- OpenAI, "Function Calling in the OpenAI API" and Structured Outputs documentation, accessed 2026-05-11.
- LlamaIndex, "Ingestion Pipeline", accessed 2026-05-11.
- Python Packaging User Guide, `pyproject.toml` project metadata specification, accessed 2026-05-11.
