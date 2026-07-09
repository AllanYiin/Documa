# Claude Code Documa Plugin

This plugin exposes Documa to Claude Code through a plugin-provided MCP server. It does not bundle Documa itself; install Documa in the Python environment visible to Claude Code first.

```powershell
python -m pip install -e ".[documents,mcp]"
claude --plugin-dir .\plugins\claude-code-documa
```

Inside Claude Code, verify the MCP server with:

```text
/mcp
```

Expected workflow:

1. Use `documa_process` to turn a source document into Documa IR and block outputs.
2. Use `documa_search_blocks` or `documa_list_blocks` to find likely evidence.
3. Use `documa_read_block` for only the selected block bodies.
4. Cite block ids, page/source metadata, and evidence boundaries in the answer.

