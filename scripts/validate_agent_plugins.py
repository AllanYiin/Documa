from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGIN_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
AGENT_PLUGIN_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}


def expected_documa_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.MULTILINE)
    if not match:
        raise ValidationError("pyproject.toml must define static project.version")
    return match.group(1)


def version_base(value: str) -> str:
    return value.split("+", 1)[0]


def require_documa_version(data: dict[str, Any], key: str, path: Path) -> str:
    version = require_string(data, key, path)
    expected = expected_documa_version()
    if version_base(version) != expected:
        raise ValidationError(
            f"{path.relative_to(ROOT)} field {key!r} must match Documa version {expected!r} "
            f"or use build metadata on that base version; got {version!r}"
        )
    return version


def require_documa_install_pin(path: Path) -> None:
    expected = expected_documa_version()
    needle = f"documa=={expected}"
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise ValidationError(
            f"{path.relative_to(ROOT)} must pin the matching runtime in its install command: {needle}"
        )


class ValidationError(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path.relative_to(ROOT)} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{path.relative_to(ROOT)} must contain a JSON object")
    return data


def require_string(data: dict[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{path.relative_to(ROOT)} must define non-empty string field {key!r}")
    return value


def require_relative_path(plugin_root: Path, value: Any, field: str, path: Path) -> None:
    if not isinstance(value, str) or not value.startswith("./"):
        raise ValidationError(
            f"{path.relative_to(ROOT)} field {field!r} must be a plugin-relative path starting with './'"
        )
    target = (plugin_root / value[2:]).resolve()
    try:
        target.relative_to(plugin_root.resolve())
    except ValueError as exc:
        raise ValidationError(f"{path.relative_to(ROOT)} field {field!r} escapes the plugin root") from exc
    if not target.exists():
        raise ValidationError(f"{path.relative_to(ROOT)} field {field!r} points to missing path {value!r}")


def validate_server_entry(entry: Any, context: str, *, portable: bool = False) -> None:
    if not isinstance(entry, dict):
        raise ValidationError(f"{context} must be an object")
    if portable and entry.get("type") != "stdio":
        raise ValidationError(f"{context}.type must be 'stdio'")
    command = entry.get("command")
    if command != "python":
        raise ValidationError(f"{context} must use command 'python'")
    args = entry.get("args", [])
    expected_args = ["-m", "documa.interfaces.mcp_server"]
    if args != expected_args:
        raise ValidationError(f"{context}.args must be {expected_args!r}")
    env = entry.get("env", {})
    if not isinstance(env, dict):
        raise ValidationError(f"{context}.env must be an object")
    if portable and {"PLUGIN_ROOT", "PLUGIN_DATA"} & set(env):
        raise ValidationError(f"{context}.env must not override reserved plugin variables")


def validate_hermes_skill(plugin_root: Path, skill_name: str) -> None:
    skill_path = plugin_root / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise ValidationError(f"hermes-documa must include skills/{skill_name}/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^description:\s*(.+?)\s*$", text)
    if match is None:
        raise ValidationError(f"{skill_path.relative_to(ROOT)} must define a description")
    description = match.group(1).strip().strip('"\'')
    if len(description) > 60 or not description.endswith("."):
        raise ValidationError(
            f"{skill_path.relative_to(ROOT)} description must be <=60 characters and end with a period"
        )


def validate_skill(plugin_root: Path, plugin_name: str, skill_name: str = "documa-evidence") -> None:
    skill_path = plugin_root / "skills" / skill_name / "SKILL.md"
    if not skill_path.exists():
        raise ValidationError(f"{plugin_name} must include skills/{skill_name}/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")
    needles = (
        ("documa_load_skill", "documa_read_skill_resource")
        if skill_name == "documa-skill-loader"
        else ("documa_process", "documa_search_blocks", "documa_read_block")
    )
    for needle in needles:
        if needle not in text:
            raise ValidationError(f"{skill_path.relative_to(ROOT)} must mention {needle}")


def validate_codex_plugin() -> None:
    plugin_root = PLUGINS / "codex-documa"
    require_documa_install_pin(plugin_root / "README.md")
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = read_json(manifest_path)
    for key in ("name", "description"):
        require_string(manifest, key, manifest_path)
    require_documa_version(manifest, "version", manifest_path)
    if manifest["name"] != "codex-documa":
        raise ValidationError("Codex plugin name must be 'codex-documa'")
    require_relative_path(plugin_root, manifest.get("skills"), "skills", manifest_path)
    require_relative_path(plugin_root, manifest.get("mcpServers"), "mcpServers", manifest_path)

    mcp_path = plugin_root / ".mcp.json"
    mcp = read_json(mcp_path)
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or "documa" not in servers:
        raise ValidationError("Codex .mcp.json must define mcpServers.documa")
    validate_server_entry(servers["documa"], "codex-documa/.mcp.json mcpServers.documa")
    validate_skill(plugin_root, "codex-documa")
    validate_skill(plugin_root, "codex-documa", "documa-skill-loader")
    openai_yaml = plugin_root / "skills" / "documa-skill-loader" / "agents" / "openai.yaml"
    if not openai_yaml.exists() or "$documa-skill-loader" not in openai_yaml.read_text(encoding="utf-8"):
        raise ValidationError("Codex bootstrap skill must include agents/openai.yaml with its literal $skill prompt")


def validate_claude_plugin() -> None:
    plugin_root = PLUGINS / "claude-code-documa"
    require_documa_install_pin(plugin_root / "README.md")
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    manifest = read_json(manifest_path)
    if require_string(manifest, "name", manifest_path) != "claude-code-documa":
        raise ValidationError("Claude Code plugin name must be 'claude-code-documa'")
    require_documa_version(manifest, "version", manifest_path)
    require_relative_path(plugin_root, manifest.get("skills"), "skills", manifest_path)
    require_relative_path(plugin_root, manifest.get("mcpServers"), "mcpServers", manifest_path)

    mcp_path = plugin_root / ".mcp.json"
    mcp = read_json(mcp_path)
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or "documa" not in servers:
        raise ValidationError("Claude Code .mcp.json must define mcpServers.documa")
    validate_server_entry(servers["documa"], "claude-code-documa/.mcp.json mcpServers.documa")
    validate_skill(plugin_root, "claude-code-documa")


def validate_openclaw_plugin() -> None:
    plugin_root = PLUGINS / "openclaw-documa"
    require_documa_install_pin(plugin_root / "README.md")
    manifest_path = plugin_root / "openclaw.plugin.json"
    manifest = read_json(manifest_path)
    require_string(manifest, "id", manifest_path)
    require_string(manifest, "name", manifest_path)
    require_documa_version(manifest, "version", manifest_path)

    package_path = plugin_root / "package.json"
    package = read_json(package_path)
    require_documa_version(package, "version", package_path)
    if not (plugin_root / "index.js").exists():
        raise ValidationError("openclaw-documa must include index.js")
    validate_skill(plugin_root, "openclaw-documa")


def validate_hermes_plugin() -> None:
    plugin_root = PLUGINS / "hermes-documa"
    require_documa_install_pin(plugin_root / "README.md")

    manifest_path = plugin_root / "plugin.json"
    manifest = read_json(manifest_path)
    unknown = set(manifest) - AGENT_PLUGIN_FIELDS
    if unknown:
        raise ValidationError(
            f"{manifest_path.relative_to(ROOT)} has non-portable top-level fields: {sorted(unknown)!r}"
        )
    if manifest.get("$schema") != AGENT_PLUGIN_SCHEMA:
        raise ValidationError(f"{manifest_path.relative_to(ROOT)} must target Agent Plugins 1.0.0")
    if require_string(manifest, "name", manifest_path) != "documa":
        raise ValidationError("Hermes portable plugin name must be 'documa'")
    require_string(manifest, "description", manifest_path)
    require_documa_version(manifest, "version", manifest_path)

    mcp_path = plugin_root / "mcp.json"
    mcp = read_json(mcp_path)
    if set(mcp) != {"$schema", "mcpServers"}:
        raise ValidationError(f"{mcp_path.relative_to(ROOT)} must only define $schema and mcpServers")
    if mcp.get("$schema") != AGENT_PLUGIN_MCP_SCHEMA:
        raise ValidationError(f"{mcp_path.relative_to(ROOT)} must target Agent Plugins MCP 1.0.0")
    servers = mcp.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {"documa"}:
        raise ValidationError("Hermes mcp.json must define exactly mcpServers.documa")
    validate_server_entry(
        servers["documa"],
        "hermes-documa/mcp.json mcpServers.documa",
        portable=True,
    )

    for skill_name in (
        "documa-codegraph",
        "documa-evidence",
        "documa-maintenance",
        "documa-skill-loader",
    ):
        validate_hermes_skill(plugin_root, skill_name)
    validate_skill(plugin_root, "hermes-documa")
    validate_skill(plugin_root, "hermes-documa", "documa-skill-loader")


def main() -> int:
    try:
        require_documa_install_pin(PLUGINS / "README.md")
        validate_codex_plugin()
        validate_claude_plugin()
        validate_openclaw_plugin()
        validate_hermes_plugin()
    except ValidationError as exc:
        print(f"agent plugin validation failed: {exc}")
        return 1
    print("agent plugin validation ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
