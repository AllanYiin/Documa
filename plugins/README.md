# Documa Agent Plugins

This directory contains host-specific plugin wrappers that consume Documa as a third-party package. They intentionally live outside `src/` so Documa core remains a parser-neutral Python package.

All wrappers assume Documa is installed in the host environment:

```powershell
python -m pip install -e ".[documents,mcp]"
```

The shared integration contract is:

1. Use `documa-mcp` as the common MCP server where the host supports MCP.
2. Use `documa` CLI calls where a host-native runtime plugin needs direct tool registration.
3. Keep answers evidence-driven: process documents, search/list blocks, then read only selected blocks.
4. Do not depend on parser-native objects or bypass Documa IR.

## Plugin Layouts

| Directory | Host | Integration style |
| --- | --- | --- |
| `claude-code-documa/` | Claude Code | `.claude-plugin` package with plugin-provided MCP server |
| `codex-documa/` | Codex | `.codex-plugin` package with plugin-provided MCP server |
| `openclaw-documa/` | OpenClaw | Native OpenClaw tool plugin wrapping the `documa` CLI |

## Minimum Smoke Checks

```powershell
documa doctor
documa-mcp
```

For OpenClaw plugin source sanity:

```powershell
node --check .\plugins\openclaw-documa\index.js
```

