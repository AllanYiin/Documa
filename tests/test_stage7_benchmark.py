import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import FixtureIssueType
from documa.interfaces import call_documa_tool, list_documa_tools
from documa.quality import BenchmarkOptions, run_fixture_benchmark


class Stage7BenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_all_manifest_cases_as_skipped_without_files(self):
        payload = run_fixture_benchmark(BenchmarkOptions())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["case_count"], len(FixtureIssueType))
        self.assertEqual(payload["summary"]["skipped"], len(FixtureIssueType))
        self.assertEqual(payload["summary"]["failed"], 0)

    def test_benchmark_require_files_fails_missing_fixture_declarations(self):
        payload = run_fixture_benchmark(BenchmarkOptions(require_files=True))

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["summary"]["failed"], len(FixtureIssueType))
        self.assertEqual(payload["cases"][0]["checks"][1]["name"], "fixture_file_declared")

    def test_cli_benchmark_writes_structured_json(self):
        from io import StringIO
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "benchmark.json"
            old_stdout = sys.stdout
            sys.stdout = StringIO()
            try:
                exit_code = main(["benchmark", "--out", str(out_path)])
                output = json.loads(sys.stdout.getvalue())
            finally:
                sys.stdout = old_stdout

            written = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(output["status"], "ok")
            self.assertEqual(written["summary"]["case_count"], len(FixtureIssueType))

    def test_tool_calling_benchmark_returns_structured_content(self):
        result = call_documa_tool("documa_benchmark", {})

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["summary"]["case_count"], len(FixtureIssueType))
        self.assertIn("documa_benchmark", {tool["name"] for tool in list_documa_tools()})

    def test_benchmark_tool_schema_is_read_only(self):
        schema = {tool["name"]: tool for tool in list_documa_tools()}["documa_benchmark"]

        self.assertTrue(schema["annotations"]["readOnlyHint"])
        self.assertIn("outputSchema", schema)


if __name__ == "__main__":
    unittest.main()

