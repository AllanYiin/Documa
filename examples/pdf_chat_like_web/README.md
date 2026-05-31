# PDF Chat Web Example

This example is a browser interface for the PDF progressive reading workflow.
It is intentionally placed under `examples/`: Documa core remains a package and
tooling surface, while this folder shows how a product UI can call it.

The UI mirrors the Markdown+ chat page pattern:

- upload one PDF;
- ask questions in a chat pane;
- show document token usage and generated keyword groups after import;
- show `search_blocks` and `read_block` calls as collapsible tool cards;
- show an extractive answer with page and block evidence.

Search starts locally from `search_blocks` using deterministic bilingual query
terms. When `OPENAI_API_KEY` and the OpenAI SDK are available, the web server
uses the Responses API only for the final Traditional Chinese answer. The
default model is `gpt-5.4-mini`; set `OPENAI_MODEL` to override it. Without
them, it falls back to deterministic evidence extraction.

## Run

```powershell
$env:PYTHONPATH="src"
python examples\pdf_chat_like_web\server.py --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

## API Shape

- `GET /api/health`
- `POST /api/load?name=<filename>` with raw PDF bytes
- `POST /api/ask` with JSON `{ "session_id": "...", "question": "..." }`

The `/api/ask` response contains ordered `events` that the UI renders as
model and tool cards with token usage.

The `/api/load` response also includes `document_token_usage` and
`keyword_groups`. It does not return the full block list by default; pass
`include_blocks=1` only for compatibility tooling that still needs it.
