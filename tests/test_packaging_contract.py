import ast
import json
import re
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _section(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*\n(.*?)(?=^\[|\Z)",
        source,
    )
    if match is None:
        raise AssertionError(f"Missing [{name}] section")
    return match.group(1)


def _array(section: str, key: str) -> list[str]:
    match = re.search(rf"(?ms)^{re.escape(key)}\s*=\s*(\[.*?\])", section)
    if match is None:
        raise AssertionError(f"Missing {key} array")
    return ast.literal_eval(match.group(1))


def _distribution_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    if match is None:
        raise AssertionError(f"Invalid requirement: {requirement}")
    return match.group(0).casefold().replace("_", "-")


def _string(section: str, key: str) -> str:
    match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]+)"', section)
    if match is None:
        raise AssertionError(f"Missing {key} string")
    return match.group(1)


class PackagingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PYPROJECT.read_text(encoding="utf-8")
        cls.project = _section(cls.source, "project")
        cls.optional = _section(cls.source, "project.optional-dependencies")

    def test_default_install_is_complete_non_ocr_agent_runtime(self):
        requirements = _array(self.project, "dependencies")
        names = {_distribution_name(item) for item in requirements}

        self.assertTrue(
            {
                "filelock",
                "pymupdf",
                "python-docx",
                "python-pptx",
                "beautifulsoup4",
                "extract-msg",
                "nbformat",
                "mcp",
                "tiktoken",
                "pyyaml",
            }.issubset(names)
        )
        self.assertNotIn("rapidocr-onnxruntime", names)
        mcp_requirement = next(item for item in requirements if _distribution_name(item) == "mcp")
        self.assertIn("<2", mcp_requirement)

    def test_all_extra_adds_ocr_only(self):
        all_requirements = _array(self.optional, "all")
        self.assertEqual(
            [_distribution_name(item) for item in all_requirements],
            ["rapidocr-onnxruntime"],
        )

    def test_granular_extras_remain_backward_compatible(self):
        for name in ("pdf", "ocr", "docx", "pptx", "html", "msg", "ipynb", "documents", "mcp", "tokens"):
            self.assertTrue(_array(self.optional, name), name)

    def test_code_extra_only_adds_optional_multilanguage_adapters(self):
        requirements = _array(self.optional, "code")
        self.assertEqual(
            {_distribution_name(item) for item in requirements},
            {"tree-sitter", "protobuf"},
        )

    def test_plugin_mcp_launch_avoids_locked_console_script(self):
        configs = (
            ROOT / "plugins" / "claude-code-documa" / ".mcp.json",
            ROOT / "plugins" / "codex-documa" / ".mcp.json",
        )
        for path in configs:
            entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["documa"]
            self.assertEqual(entry["command"], "python", path)
            self.assertEqual(entry["args"], ["-m", "documa.interfaces.mcp_server"], path)

    def test_plugin_versions_and_install_pins_match_runtime(self):
        version = _string(self.project, "version")
        manifests = (
            ROOT / "plugins" / "claude-code-documa" / ".claude-plugin" / "plugin.json",
            ROOT / "plugins" / "codex-documa" / ".codex-plugin" / "plugin.json",
            ROOT / "plugins" / "openclaw-documa" / "openclaw.plugin.json",
            ROOT / "plugins" / "openclaw-documa" / "package.json",
        )
        for path in manifests:
            plugin_version = json.loads(path.read_text(encoding="utf-8"))["version"]
            self.assertEqual(plugin_version.split("+", 1)[0], version, path)

        readmes = (
            ROOT / "plugins" / "README.md",
            ROOT / "plugins" / "claude-code-documa" / "README.md",
            ROOT / "plugins" / "codex-documa" / "README.md",
            ROOT / "plugins" / "openclaw-documa" / "README.md",
        )
        for path in readmes:
            self.assertIn(f"documa=={version}", path.read_text(encoding="utf-8"), path)

    def test_codex_zip_contains_dynamic_skill_loader_bootstrap(self):
        with zipfile.ZipFile(ROOT / "plugins" / "codex-documa.zip") as archive:
            names = set(archive.namelist())
        self.assertIn("skills/documa-skill-loader/SKILL.md", names)
        self.assertIn("skills/documa-skill-loader/agents/openai.yaml", names)
        self.assertIn("skills/documa-codegraph/SKILL.md", names)

    def test_claude_zip_contains_codegraph_skill(self):
        with zipfile.ZipFile(ROOT / "plugins" / "claude-code-documa.zip") as archive:
            names = set(archive.namelist())
        self.assertIn("skills/documa-codegraph/SKILL.md", names)


if __name__ == "__main__":
    unittest.main()
