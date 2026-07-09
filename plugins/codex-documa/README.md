# Codex Documa Plugin

This plugin exposes Documa to Codex through a plugin-provided MCP server and a reusable evidence workflow skill. It does not bundle Documa itself; install Documa in the Python environment visible to Codex first.

```powershell
python -m pip install -e ".[documents,mcp]"
```

Install or load this plugin using Codex's local plugin flow for `plugins/codex-documa`. Once enabled, verify the MCP server in Codex with:

```text
/mcp
```

Expected workflow:

1. Use `documa_process` to turn a source document into Documa IR and block outputs.
2. Use `documa_search_blocks` or `documa_list_blocks` to find likely evidence.
3. Use `documa_read_block` for only the selected block bodies.
4. Cite block ids, page/source metadata, and evidence boundaries in the answer.

