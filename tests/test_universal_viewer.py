import sys
import tempfile
import types
import unittest
from pathlib import Path

from documa.cli import main
from documa.interfaces import call_documa_tool, documa_tool_schemas, openai_tool_schemas


class UniversalViewerTests(unittest.TestCase):
    def test_view_tool_builds_hierarchical_viewer_from_supported_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "manual.md"
            source.write_text(
                "# Documa Viewer\n\n"
                "Documa universal viewer keeps parser metadata visible.\n\n"
                "## Queryable Section\n\n"
                "The body contains a hidden-needle term for search.\n",
                encoding="utf-8",
            )

            result = call_documa_tool(
                "documa_view",
                {
                    "source": str(source),
                    "query": "hidden-needle",
                    "include_body": True,
                    "body_chars": 240,
                },
            )

            self.assertFalse(result["isError"])
            payload = result["structuredContent"]
            viewer = payload["viewer"]
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(viewer["viewer"], "documa_universal_viewer")
            self.assertEqual(viewer["document"]["parser"], "markdown")
            self.assertTrue(viewer["tree"])
            self.assertTrue(any(item["matched"] == ["hidden-needle"] for item in viewer["query_results"]))
            self.assertTrue(any("metadata" in block for block in viewer["blocks"]))
            self.assertTrue(any(block.get("body") and "hidden-needle" in block["body"] for block in viewer["blocks"]))

    def test_view_tool_can_render_existing_ir_to_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "brief.md"
            out_dir = tmp_path / "processed"
            source.write_text("# Brief\n\nMetadata should remain inspectable.", encoding="utf-8")
            process = call_documa_tool("documa_process", {"source": str(source), "out": str(out_dir)})
            self.assertFalse(process["isError"])

            result = call_documa_tool(
                "documa_view",
                {"ir_path": str(out_dir / "documa.ir.json"), "format": "markdown", "query": "Metadata"},
            )

            self.assertFalse(result["isError"])
            content = result["structuredContent"]["content"]
            self.assertIn("# Documa Universal Viewer:", content)
            self.assertIn("## Document Blocks", content)
            self.assertIn("Query Results", content)

    def test_cli_view_writes_static_html_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "playbook.md"
            html_path = tmp_path / "viewer.html"
            source.write_text("# Playbook\n\nA human viewer body.", encoding="utf-8")

            exit_code = main(
                [
                    "view",
                    str(source),
                    "--format",
                    "html",
                    "--out",
                    str(html_path),
                    "--include-body",
                ]
            )

            self.assertEqual(exit_code, 0)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Documa Universal Viewer", html)
            self.assertIn("Hierarchical Blocks", html)
            self.assertIn("Document Metadata", html)
            self.assertIn("human viewer body", html)

    def test_html_viewer_does_not_repeat_heading_title_as_preview_or_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "heading.md"
            html_path = tmp_path / "viewer.html"
            source.write_text("# Documa\n\n## 概覽\n\nBody paragraph.", encoding="utf-8")

            exit_code = main(
                [
                    "view",
                    str(source),
                    "--format",
                    "html",
                    "--out",
                    str(html_path),
                    "--include-body",
                ]
            )

            self.assertEqual(exit_code, 0)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("<summary>Documa", html)
            self.assertIn("<summary>概覽", html)
            self.assertNotIn('<p class="preview">Documa</p>', html)
            self.assertNotIn('<p class="preview">概覽</p>', html)
            self.assertIn("Body paragraph.", html)

    def test_view_tool_validates_source_or_ir_path(self):
        missing = call_documa_tool("documa_view", {})
        self.assertTrue(missing["isError"])
        self.assertIn("exactly one", missing["structuredContent"]["message"])

        both = call_documa_tool("documa_view", {"source": "a.md", "ir_path": "documa.ir.json"})
        self.assertTrue(both["isError"])
        self.assertIn("exactly one", both["structuredContent"]["message"])

    def test_view_tool_schema_and_openai_shape_are_exposed(self):
        schemas = {item["name"]: item for item in documa_tool_schemas()}
        self.assertIn("documa_view", schemas)
        self.assertEqual(schemas["documa_view"]["inputSchema"]["properties"]["format"]["enum"], ["json", "markdown", "html"])

        tools = {tool["function"]["name"]: tool for tool in openai_tool_schemas(strict=True)}
        self.assertIn("documa_view", tools)
        self.assertTrue(tools["documa_view"]["function"]["strict"])

    def test_mcp_server_exposes_universal_viewer_tool(self):
        class FakeFastMCP:
            def __init__(self, *args, **kwargs):
                self.tools = {}

            def tool(self, **kwargs):
                def decorator(func):
                    self.tools[func.__name__] = func
                    return func

                return decorator

        previous = {
            "mcp": sys.modules.get("mcp"),
            "mcp.server": sys.modules.get("mcp.server"),
            "mcp.server.fastmcp": sys.modules.get("mcp.server.fastmcp"),
        }
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")
        fastmcp_module.FastMCP = FakeFastMCP
        sys.modules["mcp"] = types.ModuleType("mcp")
        sys.modules["mcp.server"] = types.ModuleType("mcp.server")
        sys.modules["mcp.server.fastmcp"] = fastmcp_module
        try:
            from documa.interfaces.mcp_server import create_mcp_server

            server = create_mcp_server()
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertIn("documa_view", server.tools)


if __name__ == "__main__":
    unittest.main()
