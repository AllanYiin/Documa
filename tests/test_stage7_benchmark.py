import json
import tempfile
import unittest
from pathlib import Path

from documa.cli import main
from documa.core.ir import FixtureIssueType
from documa.interfaces import call_documa_tool, list_documa_tools
from documa.quality import BenchmarkOptions, run_fixture_benchmark


def _manifest_without_files(tmp: str) -> Path:
    """Copy the repo manifest into tmp with every case's file cleared."""
    manifest = json.loads(Path("fixtures/pdf/manifest.json").read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        case["file"] = None
    path = Path(tmp) / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


class Stage7BenchmarkTests(unittest.TestCase):
    def test_benchmark_reports_cases_as_skipped_when_files_undeclared(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = BenchmarkOptions(manifest_path=_manifest_without_files(tmp), fixtures_dir=Path(tmp))
            payload = run_fixture_benchmark(options)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["case_count"], len(FixtureIssueType))
        self.assertEqual(payload["summary"]["skipped"], len(FixtureIssueType))
        self.assertEqual(payload["summary"]["failed"], 0)

    def test_benchmark_require_files_fails_missing_fixture_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = BenchmarkOptions(
                manifest_path=_manifest_without_files(tmp), fixtures_dir=Path(tmp), require_files=True
            )
            payload = run_fixture_benchmark(options)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["summary"]["failed"], len(FixtureIssueType))
        self.assertEqual(payload["cases"][0]["checks"][1]["name"], "fixture_file_declared")

    def test_repo_manifest_declares_existing_fixture_files_for_all_cases(self):
        payload = run_fixture_benchmark(BenchmarkOptions(require_files=True))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["summary"]["passed"], len(FixtureIssueType))
        self.assertEqual(payload["summary"]["skipped"], 0)

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

