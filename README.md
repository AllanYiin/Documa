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
