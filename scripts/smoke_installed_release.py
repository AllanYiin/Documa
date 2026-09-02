"""Run with an isolated wheel installation; no external LingXi is permitted."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import documa
from documa.adapters.lingxi_binding import lingxi_binding


async def smoke() -> dict:
    assert documa.__version__ == importlib.metadata.version("documa") == "0.8.0"
    assert importlib.util.find_spec("lingxi") is None, "Run in an environment without external LingXi"
    module, version = lingxi_binding()
    assert version == "0.4.5"
    for name in ("rust_office._core", "rust_pdf._native"):
        importlib.import_module(name)

    with TemporaryDirectory(prefix="documa-release-smoke-") as temporary:
        root = Path(temporary)
        source = root / "report.md"
        source.write_text(
            "# 文件理解驗證\n\n😀本系統不得改寫原文。人工智慧協助文件檢索。\n\n"
            "- 2026年保留全部來源。\n- 必須逐段附上證據。\n",
            encoding="utf-8",
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-I", "-X", "utf8", "-m", "documa.interfaces.mcp_server"],
            cwd=str(root),
            env={**os.environ, "DOCUMA_MCP_PROFILE": "agent", "DOCUMA_MCP_RUNTIME_DIR": str(root / "runtime")},
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert {"documa_process", "documa_summarize", "documa_search_blocks"} <= names
                # Regression: MCP 2 redirects fd 0; the watchdog must not exit.
                await asyncio.sleep(4.5)
                processed = await session.call_tool("documa_process", {"source": str(source), "out": str(root / "out")})
                assert not getattr(processed, "is_error", getattr(processed, "isError", False)), processed
                ir = root / "out/documa.ir.json"
                assert ir.is_file()
                result = await session.call_tool("documa_summarize", {"ir_path": str(ir), "top_k": 2})
                assert not getattr(result, "is_error", getattr(result, "isError", False)), result
                payload = json.loads(next(item.text for item in result.content if item.type == "text"))
                assert payload["status"] == "ok" and payload["provider_version"] == "0.4.5"
                assert payload["sentences"] and payload["uses_llm"] is False and payload["llm_tokens_used"] == 0
                assert all(row["source_block_ids"] and row["block_ids"] for row in payload["sentences"])
                return {
                    "status": "PASS", "documa": documa.__version__, "lingxi": version,
                    "documa_path": documa.__file__, "lingxi_path": module.__file__,
                    "external_lingxi": False, "native_parsers": ["rust_pdf", "rust_office"],
                    "mcp_transport": "stdio", "mcp_idle_seconds": 4.5,
                    "tool_count": len(names), "summary_rows": len(payload["sentences"]),
                    "source_refs": "PASS", "uses_llm": False,
                }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), ensure_ascii=False, indent=2))
