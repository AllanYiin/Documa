import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool, list_documa_tools, openai_tool_schemas
from documa.interfaces.mcp_server import create_mcp_server
from documa.interfaces.tools import process_document_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_REPORT = REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf"


def make_ir_file(tmp: str) -> Path:
    block = BlockIR(
        id="b1",
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent("工具可讀內容"),
        bbox=(40, 40, 200, 60),
        order_index=1,
    )
    doc = DocumentIR(
        id="d1",
        source_name="工具測試.pdf",
        pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=[block])],
    )
    path = Path(tmp) / "documa.ir.json"
    path.write_text(json.dumps(to_plain_data(doc), ensure_ascii=False), encoding="utf-8")
    return path


class Stage6ToolInterfaceTests(unittest.TestCase):
    def test_direct_tool_call_returns_mcp_compatible_structured_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = make_ir_file(tmp)

            result = call_documa_tool("documa_inspect", {"ir_path": str(ir_path)})

            self.assertFalse(result["isError"])
            self.assertEqual(result["content"][0]["type"], "text")
            self.assertEqual(result["structuredContent"]["status"], "ok")
            self.assertEqual(result["structuredContent"]["document_id"], "d1")
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
            self.assertNotIn("\n", result["content"][0]["text"])

            summary = call_documa_tool(
                "documa_inspect",
                {"ir_path": str(ir_path)},
                text_mode="summary",
            )
            self.assertEqual(summary["content"][0]["text"], "Tool completed with status=ok.")

    def test_direct_tool_call_reports_unknown_tool_as_tool_error(self):
        result = call_documa_tool("missing_tool", {})

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "error")
        self.assertIn("Unknown Documa tool", result["content"][0]["text"])

    def test_direct_tool_call_wraps_invalid_arguments_as_error(self):
        result = call_documa_tool("documa_export", {"ir_path": []})

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "error")

    def test_export_tool_can_write_rag_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = make_ir_file(tmp)
            out_path = Path(tmp) / "chunks.json"

            result = call_documa_tool(
                "documa_export",
                {"ir_path": str(ir_path), "format": "rag-json", "out": str(out_path)},
            )

            exported = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["output_path"], str(out_path))
            self.assertEqual(exported["chunks"][0]["page_content"], "工具可讀內容")

    def test_cli_tools_lists_tool_calling_schemas(self):
        from io import StringIO
        import sys

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            exit_code = main(["tools"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "ok")
        self.assertEqual({tool["name"] for tool in output["tools"]}, {tool["name"] for tool in list_documa_tools()})

    def test_tool_schemas_include_mcp_annotations_and_output_schema(self):
        schemas = {tool["name"]: tool for tool in list_documa_tools()}

        self.assertTrue(schemas["documa_inspect"]["annotations"]["readOnlyHint"])
        self.assertIn("outputSchema", schemas["documa_parse"])
        self.assertIn("inputSchema", schemas["documa_export"])
        self.assertIn("documa_ingest_mailbox", schemas)
        self.assertEqual(schemas["documa_ingest_mailbox"]["inputSchema"]["required"], ["source", "out"])
        search_properties = schemas["documa_search_blocks"]["inputSchema"]["properties"]
        self.assertEqual(search_properties["response_profile"]["default"], "nav")
        self.assertEqual(search_properties["response_profile"]["enum"], ["nav", "evidence", "debug"])
        self.assertNotIn("default", search_properties["verbosity"])

    def test_fastmcp_process_schema_names_source_and_bounds_large_results(self):
        try:
            tools = asyncio.run(create_mcp_server().list_tools())
        except RuntimeError as exc:
            self.skipTest(str(exc))

        process = next(tool for tool in tools if tool.name == "documa_process")
        input_schema = getattr(process, "inputSchema", None) or process.input_schema
        self.assertEqual(input_schema["required"], ["source"])
        self.assertIn("source", process.description)
        self.assertIn("not input_path", process.description)
        self.assertIn("set out", process.description)

    def test_process_tool_returns_pipeline_summary_and_writes_full_report_to_disk(self):
        # Regression: full per-stage diagnostics once came back inline (~84K chars
        # for a large PDF) and blew past MCP response limits even with out set.
        with tempfile.TemporaryDirectory() as tmp:
            payload = process_document_tool(source=str(ANNUAL_REPORT), out=tmp)

            self.assertEqual(payload["status"], "ok")
            for stage in payload["pipeline"]["stages"]:
                self.assertNotIn("report", stage)
            self.assertLess(len(json.dumps(payload["pipeline"])), 4000)

            report_path = Path(payload["pipeline_report_path"])
            self.assertEqual(report_path.parent, Path(tmp))
            full_report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(full_report["stage_count"], payload["pipeline"]["stage_count"])
            self.assertIn("report", full_report["stages"][0])

    def test_openai_tool_schemas_use_function_shape(self):
        tools = {tool["function"]["name"]: tool for tool in openai_tool_schemas(strict=True)}

        self.assertIn("documa_process", tools)
        self.assertIn("documa_ingest_mailbox", tools)
        self.assertEqual(tools["documa_process"]["type"], "function")
        self.assertTrue(tools["documa_process"]["function"]["strict"])
        parameters = tools["documa_process"]["function"]["parameters"]
        self.assertFalse(parameters["additionalProperties"])
        self.assertIn("source", parameters["required"])


if __name__ == "__main__":
    unittest.main()
