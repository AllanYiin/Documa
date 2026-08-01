# PDF Chat-Like Progressive Reading Example

This example shows the same access pattern as the Markdown+ chat demo, but the
source document is a PDF:

1. Load one PDF into Documa IR.
2. Build progressive document blocks and keyword metadata.
3. Answer questions through keyword-first tool-like steps. Documa first expands
   the query into deterministic bilingual terms, runs `search_blocks ->
   read_block`, and with `--llm` sends only the selected evidence to the
   Responses API for the final Traditional Chinese answer.
4. Emit a JSON trace that can be rendered by any chat UI or inspected by an LLM.

The example is intentionally CLI-first. Documa phase 1 does not ship a UI; this
folder demonstrates the mechanism that a UI can call.

## Requirements

The default install includes PDF support and local `tiktoken` counting:

```powershell
python -m pip install .
```

LLM answer generation additionally needs the demo extra and `OPENAI_API_KEY`:

```powershell
python -m pip install ".[demo]"
```

Search itself is local and
starts from `search_blocks`. The default Responses API model is `gpt-5.4-mini`;
set `OPENAI_MODEL` to override it.

## Run One Question

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like\pdf_chat_example.py "D:\文件\report.pdf" `
  --question "這份文件的主要風險是什麼？" `
  --out "D:\tmp\documa-pdf-chat" `
  --llm
```

The command prints a JSON payload and writes:

- `documa.ir.json`: processed Documa IR.
- `documa.blocks.json`: block tree export.
- `pdf_chat_trace.json`: chat-like tool trace.

## Run Multiple Questions

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like\pdf_chat_example.py "D:\文件\report.pdf" `
  --question "這份文件討論哪些成本？" `
  --question "有沒有提到部署風險？" `
  --out "D:\tmp\documa-pdf-chat"
```

The PDF is parsed once; each question then reuses the in-memory block index.

## Interactive Mode

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like\pdf_chat_example.py "D:\文件\report.pdf" --interactive
```

Type a question and press Enter. Type `exit` or `quit` to stop.

## Trace Shape

Each turn contains ordered events:

- `tool_call`: the local document operation and arguments.
- `tool_result`: structured data returned by that operation.
- `answer`: final answer with evidence. When `--llm` is enabled, this event
  carries the Responses API usage for that turn; there is no separate
  `plan_query` or `synthesize_answer` model step.

Within one in-memory PDF session, LLM answers chain `previous_response_id` so
follow-up questions can use prior model context. The per-turn `token_usage`
field is still the current turn's Responses API usage. `cumulative_token_usage`
is the session total accumulated from Responses API usage.

This mirrors the mechanism in the Markdown+ chat page without requiring a web
frontend or external LLM call.

The web example keeps `list_blocks` available only for explicit compatibility
requests. The main chat path starts from `search_blocks`; the load response
includes document-level token usage and top keyword groups, not a full block
list.
