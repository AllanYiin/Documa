# Documa

Documa is an LLM-ready document understanding package. Stage 0 provides the
core package skeleton, typed intermediate representation, adapter and pipeline
interfaces, encoding utilities, and a CLI entry point.

First-stage scope:

- Python package first, no UI.
- Stable Documa IR for parser adapters and downstream tools.
- CLI surface that can be called by agents.
- Unicode-first text handling with UTF-8 JSON output.
- Future-ready interfaces for tool calling and MCP.

## Quick Check

```powershell
$env:PYTHONPATH="src"; python -m unittest discover -s tests
```

```powershell
$env:PYTHONPATH="src"; python -m documa.cli --version
```

## Stage 2 PDF Parse

If PyMuPDF is available, parse a PDF and write UTF-8 JSON plus assets:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli parse "D:\文件\報告.pdf" --out "D:\tmp\documa-out" --lang zh-Hant,en
```

The command writes:

- `documa.ir.json`
- `assets/previews/page_0001.png`
- extracted image assets when present

## Stage 5 Export

Export an existing IR file for agent workflows:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli export "D:\tmp\documa-out\documa.ir.json" --format rag-json --out "D:\tmp\documa-out\chunks.json"
```

Useful formats:

- `json`: full Documa IR.
- `markdown`: readable Markdown with page markers.
- `rag-json`: chunk records with `page_content` and traceable metadata.
- `block-json`: progressive-disclosure document block tree.

## Stage 6 Tool Interfaces

List tool-calling schemas:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli tools
```

Python callers can also request OpenAI function-tool descriptors:

```python
from documa.interfaces import openai_tool_schemas

tools = openai_tool_schemas(strict=True)
```

Run the optional MCP server after installing the extra:

```powershell
pip install "documa[mcp]"
documa-mcp
```

## Stage 7 Benchmark

Run the fixture readiness benchmark:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli benchmark --out "D:\tmp\documa-benchmark.json"
```

Use `--require-files` when fixture PDFs must exist for a release gate.

## Stage 8 Doctor

Check local package readiness:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli doctor
```

The doctor reports core failures separately from optional dependency warnings
for PDF and MCP integrations.

## Stage 9 Process

Run parse plus the default understanding pipeline:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli process "D:\文件\報告.pdf" --out "D:\tmp\documa-process" --lang zh-Hant,en
```

The command writes `documa.ir.json` and a default `documa.rag.json` retrieval
export when `--out` is provided.

## Stage 10 Block Reading

Documa now exposes document blocks as the primary progressive-reading surface.
Agents can list the block tree, inspect metadata, and read only the selected
block body:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli blocks "D:\tmp\documa-process\documa.ir.json"
python -m documa.cli block "D:\tmp\documa-process\documa.ir.json" --id db_doc_p1
python -m documa.cli block "D:\tmp\documa-process\documa.ir.json" --id db_doc_p1 --read
python -m documa.cli search-blocks "D:\tmp\documa-process\documa.ir.json" --query "資料科學"
```

Chunks remain available for compatibility and retrieval export, but when a
block tree exists they are generated only as intra-block retrieval views with a
`parent_block_id`.

## Stage 11 Block Reading Demo

Run an end-to-end CLI demo that parses a PDF, builds block metadata and
keywords, answers a question through progressive block reading, and records each
step with token usage:

```powershell
$env:PYTHONPATH="src"
python -m documa.cli block-demo "D:\文件\報告.pdf" --question "這份文件的主要風險是什麼？" --out "D:\tmp\documa-demo"
```

The demo writes:

- `documa.ir.json`: processed Documa IR.
- `documa.blocks.json`: block tree export.
- `documa.block_demo.trace.json`: full trace with calls, returned content,
  selected blocks, synthesized answer, elapsed time, and token accounting.

The demo is deterministic and offline. It does not call an external LLM; answer
synthesis is extractive so the trace remains reproducible. If `tiktoken` is
installed it is used for token counts; otherwise Documa marks the count as a
heuristic estimate.

## Examples

The `examples/` directory contains runnable workflows built on top of Documa's
public package surface. These examples are not product UI; they show how an app
or agent can compose Documa tools.

### PDF Chat-Like Progressive Reading

`examples/pdf_chat_like/` demonstrates a Markdown+ chat-style mechanism for
PDFs. It loads one PDF, builds document blocks, then answers questions through
traceable tool-like events:

```text
list_blocks -> search_blocks -> read_block -> synthesize_answer
```

Run it from a repository checkout:

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like\pdf_chat_example.py "D:\文件\report.pdf" `
  --question "這份文件的主要風險是什麼？" `
  --out "D:\tmp\documa-pdf-chat"
```

The example writes `documa.ir.json`, `documa.blocks.json`, and
`pdf_chat_trace.json`. See
`examples/pdf_chat_like/README.md` for multi-question and interactive usage.

### PDF Chat Web Interface

`examples/pdf_chat_like_web/` provides a local browser UI similar to the
Markdown+ chat page, but it reads PDFs through Documa:

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like_web\server.py --port 8765
```

Then open `http://127.0.0.1:8765`, upload a PDF, and ask questions. The page
shows each `list_blocks`, `search_blocks`, and `read_block` step as collapsible
tool cards before displaying the evidence-backed answer.
