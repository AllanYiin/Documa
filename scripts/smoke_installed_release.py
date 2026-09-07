"""Run with an isolated wheel installation; no external LingXi is permitted."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import documa
from documa.adapters.lingxi_binding import lingxi_binding


def spacing_pdf() -> bytes:
    """Tiny PDF with positional word gaps, without invoking another parser."""
    stream = b"BT /F1 12 Tf 72 720 Td [(We) -250 (propose)] TJ 0 -16 Td [(a) -250 (model.)] TJ ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


async def smoke() -> dict:
    assert documa.__version__ == importlib.metadata.version("documa") == "0.9.0"
    assert importlib.util.find_spec("lingxi") is None, "Run in an environment without external LingXi"
    module, version = lingxi_binding()
    assert version == "0.4.5"
    native_hashes = {}
    for name in ("rust_office._core", "rust_pdf._native", "documa._vendor.lingxi._core"):
        native = importlib.import_module(name)
        native_hashes[name] = hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()

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
                pdf = root / "spacing.pdf"
                pdf.write_bytes(spacing_pdf())
                parsed = await session.call_tool("documa_process", {
                    "source": str(pdf), "out": str(root / "pdf-out"), "pdf_provider": "rust",
                })
                assert not getattr(parsed, "is_error", getattr(parsed, "isError", False)), parsed
                pdf_ir = json.loads((root / "pdf-out/documa.ir.json").read_text(encoding="utf-8"))
                assert pdf_ir["parser"] == "rust_pdf" and len(pdf_ir["pages"]) == 1
                raw_text = "\n".join(block.get("text", {}).get("raw_text", "")
                                     for page in pdf_ir["pages"] for block in page["blocks"]
                                     if block.get("text"))
                assert "We propose" in raw_text, raw_text
                return {
                    "status": "PASS", "documa": documa.__version__, "lingxi": version,
                    "python": platform.python_version(), "machine": platform.machine(),
                    "native_sha256": native_hashes,
                    "documa_path": documa.__file__, "lingxi_path": module.__file__,
                    "external_lingxi": False, "native_parsers": ["rust_pdf", "rust_office"],
                    "mcp_transport": "stdio", "mcp_idle_seconds": 4.5,
                    "tool_count": len(names), "summary_rows": len(payload["sentences"]),
                    "source_refs": "PASS", "uses_llm": False,
                    "rust_pdf_spacing": "PASS",
                }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), ensure_ascii=False, indent=2))
