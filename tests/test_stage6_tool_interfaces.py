import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool, list_documa_tools, openai_tool_schemas


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
