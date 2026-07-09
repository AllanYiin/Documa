import json
import unittest

from documa.cli import main
from documa.interfaces import call_documa_tool, list_documa_tools
from documa.quality import DoctorOptions, run_doctor


class Stage8ReleaseReadinessTests(unittest.TestCase):
    def test_doctor_reports_environment_readiness(self):
        payload = run_doctor(DoctorOptions(project_root=".", include_benchmark=True))

        repo_cases = len(
            json.loads(open("fixtures/pdf/manifest.json", encoding="utf-8").read())["cases"]
        )
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["summary"]["checks"], 7)
        self.assertEqual(payload["benchmark_summary"]["case_count"], repo_cases)

    def test_direct_doctor_tool_returns_structured_content(self):
        result = call_documa_tool("documa_doctor", {"project_root": ".", "include_benchmark": False})

        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "ok")
        self.assertIsNone(result["structuredContent"]["benchmark_summary"])

    def test_cli_doctor_outputs_json(self):
        from io import StringIO
        import sys

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            exit_code = main(["doctor", "--no-benchmark"])
            output = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old_stdout

        self.assertEqual(exit_code, 0)
        self.assertEqual(output["status"], "ok")
        self.assertIn("python_version", {check["name"] for check in output["checks"]})

    def test_tool_schema_lists_doctor(self):
        schema = {tool["name"]: tool for tool in list_documa_tools()}["documa_doctor"]

        self.assertTrue(schema["annotations"]["readOnlyHint"])
        self.assertIn("outputSchema", schema)


if __name__ == "__main__":
    unittest.main()

