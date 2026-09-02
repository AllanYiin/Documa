# OpenClaw Documa Plugin

<p align="center">
  <img src="assets/documa-logo.png" alt="Documa logo" width="320">
</p>

This is a native OpenClaw tool plugin that wraps the installed `documa` CLI. It does not bundle Documa itself; install Documa in the environment visible to the OpenClaw Gateway first.

```powershell
# 本次交付先使用隨附的 Windows CPython 3.10 x64 wheel：
python -m pip install .\documa-0.8.0-cp310-cp310-win_amd64.whl
# 僅當目標 package index 已發布此版本時使用：
python -m pip install "documa==0.8.0"
openclaw plugins install --link .\plugins\openclaw-documa
openclaw plugins enable documa
openclaw gateway restart
openclaw plugins inspect documa --runtime --json
```

Registered tools:

| Tool | Purpose |
| --- | --- |
| `documa_process` | Parse and process a source document into Documa IR and exports. |
| `documa_search_blocks` | Search block metadata/snippets without loading the full document. |
| `documa_read_block` | Read selected block bodies after search. |
| `documa_doctor` | Check Documa runtime readiness. |

If `documa` is not on `PATH`, set plugin config `documaCommand` to the absolute command path that should be executed by the Gateway process.

