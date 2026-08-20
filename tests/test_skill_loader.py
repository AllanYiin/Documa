from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from filelock import FileLock

from documa.interfaces import token_counting
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.skills import (
    SkillRoot,
    inspect_skill_graph,
    load_skill_bundle,
    read_skill_resource,
    sync_skill_roots,
)
from documa.skills.index import query_skill_candidates
from documa.skills.store import active_skill_entries, load_skill_ir, load_skill_registry


class _CharCounter:
    name = "test:characters"

    def count(self, text: str) -> int:
        return len(text)

    def truncate(self, text: str, max_tokens: int) -> tuple[str, bool]:
        return text[:max_tokens], len(text) > max_tokens


@pytest.fixture(autouse=True)
def _token_counter():
    token_counting.set_token_counter(_CharCounter())
    yield
    token_counting.reset_token_counter()


def _write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    description: str,
    body: str,
    dependency: str | None = None,
) -> Path:
    skill = root / directory
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body.rstrip()}\n",
        encoding="utf-8",
    )
    if dependency:
        (skill / "skill_lifecycle.yaml").write_text(
            f"dependencies:\n  skills:\n    - {dependency}\n", encoding="utf-8"
        )
    return skill


def test_parser_preserves_source_and_structural_roles(tmp_path: Path):
    roots = tmp_path / "roots"
    skill = _write_skill(
        roots,
        "deploy",
        name="safe-deploy",
        description="Deploy services safely 發布服務",
        body="""# Scope
Only production deployment tasks.

# Guardrails
- Never print credentials.
- 不得跳過驗證。

# Workflow
<steps>
1. Step 1 validate the release.
2. Step 2 deploy it.
</steps>

```powershell
Write-Output 'dry-run'
```

Read [the runbook](references/runbook.md) first.
""",
    )
    references = skill / "references"
    references.mkdir()
    (references / "runbook.md").write_text("# Guardrails\r\n\r\n回復流程。\r\n", encoding="utf-8")
    scripts = skill / "scripts"
    scripts.mkdir()
    (scripts / "deploy.ps1").write_text("throw 'must not execute'\n", encoding="utf-8")

    result = sync_skill_roots([SkillRoot("local", str(roots))], store_dir=tmp_path / "store")
    assert result.compiled == 1
    ir = load_skill_ir(active_skill_entries(tmp_path / "store")[0], tmp_path / "store")
    assert ir.frontmatter["description"] == "Deploy services safely 發布服務"
    assert any(block.kind == "xml_tag" for block in ir.blocks)
    assert any(block.kind == "code_fence" for block in ir.blocks)
    guardrails = [block for block in ir.blocks if block.role.value == "guardrail"]
    assert any("Never print credentials" in block.text.raw_text for block in guardrails)
    assert all(block.metadata["required"] for block in guardrails if block.resource_path == "SKILL.md")
    assert any(resource.path == "references/runbook.md" and resource.text_indexed for resource in ir.resources)
    assert not any(
        block.metadata["required"]
        for block in ir.blocks
        if block.resource_path == "references/runbook.md" and block.role.value == "guardrail"
    )
    assert any(resource.path == "scripts/deploy.ps1" and not resource.text_indexed for resource in ir.resources)
    assert "throw 'must not execute'" not in json.dumps(ir.metadata, ensure_ascii=False)
    assert any(edge.type.value == "requires_block" for edge in ir.edges)


def test_incremental_generations_and_missing_tombstone(tmp_path: Path):
    roots = tmp_path / "roots"
    skill = _write_skill(roots, "alpha", name="alpha", description="Alpha workflow", body="# Workflow\nDo alpha.")
    store = tmp_path / "store"
    first = sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)
    assert (first.compiled, first.unchanged) == (1, 0)
    second = sync_skill_roots(store_dir=store)
    assert (second.compiled, second.unchanged, second.index_rebuilt) == (0, 1, False)

    path = skill / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")
    updated = sync_skill_roots(store_dir=store)
    assert updated.compiled == 1
    statuses = [item["status"] for item in load_skill_registry(store)["skills"]]
    assert statuses.count("active") == 1 and "superseded" in statuses

    path.unlink()
    missing = sync_skill_roots(store_dir=store)
    assert missing.missing == 1
    assert active_skill_entries(store) == []


def test_optional_enrichment_is_cached_and_negative_triggers_demote(tmp_path: Path):
    class Provider:
        name = "fixture"
        version = "1"

        def __init__(self):
            self.calls = 0

        def enrich(self, catalog):
            self.calls += 1
            if catalog["name"] == "finance-review":
                return {"synonyms": ["財報"], "negative_triggers": ["圖片"]}
            return {"positive_triggers": ["圖片"]}

    roots = tmp_path / "roots"
    _write_skill(roots, "finance", name="finance-review", description="Review company numbers", body="# Workflow\nReview.")
    _write_skill(roots, "image", name="image-review", description="Review visual media", body="# Workflow\nInspect.")
    provider = Provider()
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store, enrichment_provider=provider)
    assert provider.calls == 2
    sync_skill_roots(store_dir=store, enrichment_provider=provider)
    assert provider.calls == 2
    result = query_skill_candidates("請檢查這張圖片", store_dir=store)
    assert result["status"] == "ok"
    assert result["candidates"][0]["name"] == "image-review"


def test_exact_name_ambiguity_and_dependency_cycle(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_skill(root_a, "same", name="same", description="First same skill", body="# Workflow\nFirst.")
    _write_skill(root_b, "same", name="same", description="Second same skill", body="# Workflow\nSecond.")
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("a", str(root_a)), SkillRoot("b", str(root_b))], store_dir=store)
    ambiguous = load_skill_bundle("same", ["same"], store_dir=store)
    assert ambiguous.status == "needs_narrowing"
    assert ambiguous.code == "SKILL_AMBIGUOUS"
    assert len(ambiguous.candidates) == 2

    cycles = tmp_path / "cycles"
    _write_skill(cycles, "one", name="one", description="Run one", body="# Workflow\nOne.", dependency="two")
    _write_skill(cycles, "two", name="two", description="Run two", body="# Workflow\nTwo.", dependency="one")
    cycle_store = tmp_path / "cycle-store"
    sync_skill_roots([SkillRoot("cycles", str(cycles))], store_dir=cycle_store)
    cycle = load_skill_bundle("run one", ["one"], store_dir=cycle_store)
    assert cycle.status == "needs_narrowing"
    assert cycle.code == "SKILL_DEPENDENCY_CYCLE"

    missing_root = tmp_path / "missing-dependency"
    _write_skill(
        missing_root,
        "consumer",
        name="consumer",
        description="Consume a missing dependency",
        body="# Workflow\nConsume.",
        dependency="not-installed",
    )
    missing_store = tmp_path / "missing-store"
    sync_skill_roots([SkillRoot("missing", str(missing_root))], store_dir=missing_store)
    missing = load_skill_bundle("consumer", ["consumer"], store_dir=missing_store)
    assert missing.status == "needs_narrowing"
    assert missing.code == "SKILL_DEPENDENCY_MISSING"


def test_bundle_keeps_guardrails_verbatim_and_reports_small_budget(tmp_path: Path):
    roots = tmp_path / "roots"
    guardrail = "NEVER-DROP-GUARDRAIL " + ("x" * 240)
    _write_skill(
        roots,
        "release",
        name="release",
        description="Release the application",
        body=f"# Scope\nRelease only.\n\n# Guardrails\n{guardrail}\n\n# Workflow\nDeploy the application.\n",
    )
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)
    too_small = load_skill_bundle("release", ["release"], max_tokens=256, store_dir=store)
    assert too_small.status == "needs_narrowing"
    assert too_small.code == "SKILL_BUDGET_TOO_SMALL"
    assert too_small.budget["minimum_required_tokens"] > 256

    bundle = load_skill_bundle("release", ["release"], max_tokens=2000, store_dir=store)
    assert bundle.status == "ok"
    assert guardrail in bundle.rendered_skill_md
    assert "DOCUMA SYNTHETIC WRAPPER" in bundle.rendered_skill_md
    assert bundle.rendered_skill_md == load_skill_bundle(
        "release", ["release"], max_tokens=2000, store_dir=store
    ).rendered_skill_md
    assert bundle.budget["mode"] == "explicit"
    assert bundle.budget["requested_max_tokens"] == 2000


def test_automatic_bundle_budget_is_not_fixed_at_3000_tokens(tmp_path: Path):
    roots = tmp_path / "roots"
    guardrail = "AUTOMATIC-BUDGET-GUARDRAIL " + ("x" * 3600)
    _write_skill(
        roots,
        "large-safe-skill",
        name="large-safe-skill",
        description="Load a large mandatory safety policy",
        body=f"# Scope\nSafety policy only.\n\n# Guardrails\n{guardrail}\n",
    )
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)

    fixed = load_skill_bundle(
        "large mandatory safety policy",
        ["large-safe-skill"],
        max_tokens=3000,
        store_dir=store,
    )
    assert fixed.status == "needs_narrowing"
    assert fixed.code == "SKILL_BUDGET_TOO_SMALL"

    automatic = load_skill_bundle("large mandatory safety policy", ["large-safe-skill"], store_dir=store)
    assert automatic.status == "ok"
    assert automatic.budget["mode"] == "automatic"
    assert automatic.budget["requested_max_tokens"] is None
    assert automatic.budget["max_tokens"] == 8000
    assert automatic.budget["spent_tokens"] > 3000
    assert guardrail in automatic.rendered_skill_md


def test_resource_pagination_and_script_refusal(tmp_path: Path):
    roots = tmp_path / "roots"
    skill = _write_skill(
        roots,
        "reader",
        name="reader",
        description="Read operational runbooks",
        body="# Workflow\nRead [runbook](references/runbook.md).\n",
    )
    (skill / "references").mkdir()
    (skill / "references" / "runbook.md").write_text("0123456789" * 80, encoding="utf-8")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.py").write_text("raise SystemExit\n", encoding="utf-8")
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)
    skill_id = active_skill_entries(store)[0]["skill_id"]
    page = read_skill_resource(skill_id, "references/runbook.md", max_tokens=256, store_dir=store)
    assert page["status"] == "ok" and len(page["content"]) == 256
    assert page["continuation"]["start"] == 256
    denied = read_skill_resource(skill_id, "scripts/run.py", store_dir=store)
    assert denied["code"] == "SKILL_RESOURCE_NOT_READABLE"
    escaped = read_skill_resource(skill_id, "../references/runbook.md", store_dir=store)
    assert escaped["code"] == "SKILL_RESOURCE_OUTSIDE_ROOT"
    (skill / "references" / "runbook.md").write_text("changed after sync", encoding="utf-8")
    changed = read_skill_resource(skill_id, "references/runbook.md", store_dir=store)
    assert changed["code"] == "SKILL_RESOURCE_CHANGED"
    graph = inspect_skill_graph(skill_id, store_dir=store)
    assert graph["status"] == "ok" and graph["resources"]


def test_required_resource_actions_and_summary_distinguish_resource_states(tmp_path: Path):
    roots = tmp_path / "roots"
    skill = _write_skill(
        roots,
        "research",
        name="research",
        description="Research official sources",
        body="""# Scope
Research only.

# Instructions
先以 `references/playbook.md` 作為查詢設計基準。

# Notes
先讀使用者提供的內容，再依 `references/optional.md` 補充背景。

# Resources
- `scripts/check.py`
""",
    )
    (skill / "references").mkdir()
    (skill / "references" / "playbook.md").write_text(
        "# Official source playbook\n\n"
        + "\n\n".join(f"## Rule {index}\nUse authoritative official sources. " + ("x" * 120) for index in range(20)),
        encoding="utf-8",
    )
    (skill / "references" / "optional.md").write_text("# Optional\n\nUnrelated background.\n", encoding="utf-8")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "check.py").write_text("raise SystemExit\n", encoding="utf-8")
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)

    ir = load_skill_ir(active_skill_entries(store)[0], store)
    required_paths = {
        edge.metadata["resource_path"]
        for edge in ir.edges
        if edge.type.value == "requires_block" and edge.metadata.get("reason") == "required_resource"
    }
    assert required_paths == {"references/playbook.md"}

    bundle = load_skill_bundle(
        "research official authoritative sources",
        ["research"],
        max_tokens=2000,
        store_dir=store,
    )
    assert bundle.status == "ok"
    recommended_paths = {
        action["arguments"]["resource_path"]
        for action in bundle.next_actions
        if action["tool"] == "documa_read_skill_resource"
    }
    assert recommended_paths == {"references/playbook.md"}
    assert bundle.resource_summary["available_text_resources"] == 2
    assert bundle.resource_summary["materialized_text_resources"] == 1
    assert bundle.resource_summary["partially_materialized_text_resources"] == 1
    assert bundle.resource_summary["fully_materialized_text_resources"] == 0
    assert bundle.resource_summary["recommended_resource_reads"] == 1
    assert bundle.selected_skills[0]["resource_summary"] == bundle.resource_summary["by_skill"][0]


def test_fully_materialized_resource_is_reported_without_recommending_a_duplicate_read(tmp_path: Path):
    roots = tmp_path / "roots"
    skill = _write_skill(
        roots,
        "reader",
        name="reader",
        description="Read a short note",
        body="# Scope\nRead notes only.\n\n# Instructions\nRead [the note](references/note.md) first.\n",
    )
    (skill / "references").mkdir()
    (skill / "references" / "note.md").write_text("Short note.\n", encoding="utf-8")
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)

    bundle = load_skill_bundle("Short note", ["reader"], max_tokens=4000, store_dir=store)
    assert bundle.status == "ok"
    assert bundle.next_actions == []
    assert bundle.resource_summary["available_text_resources"] == 1
    assert bundle.resource_summary["materialized_text_resources"] == 1
    assert bundle.resource_summary["partially_materialized_text_resources"] == 0
    assert bundle.resource_summary["fully_materialized_text_resources"] == 1
    assert bundle.resource_summary["recommended_resource_reads"] == 0


def test_token_counter_is_required(tmp_path: Path):
    token_counting.set_token_counter(None)
    bundle = load_skill_bundle("anything", store_dir=tmp_path / "store")
    assert bundle.code == "TOKEN_COUNTER_REQUIRED"


def test_mcp_profile_contract_keeps_agent_surface_compact():
    agent = {item["name"] for item in documa_tool_schemas(profile="agent")}
    admin = {item["name"] for item in documa_tool_schemas(profile="admin")}
    skill_tools = {name for name in admin if "skill" in name}
    assert skill_tools.intersection(agent) == {"documa_load_skill", "documa_read_skill_resource"}
    assert skill_tools == {
        "documa_load_skill",
        "documa_read_skill_resource",
        "documa_sync_skills",
        "documa_skill_status",
        "documa_inspect_skill_graph",
    }
    sync_schema = next(item for item in documa_tool_schemas(profile="admin") if item["name"] == "documa_sync_skills")
    root_properties = sync_schema["inputSchema"]["properties"]["roots"]["items"]["properties"]
    assert root_properties["allow_native_scan_overlap"]["default"] is False
    load_schema = next(item for item in documa_tool_schemas(profile="agent") if item["name"] == "documa_load_skill")
    max_tokens_schema = load_schema["inputSchema"]["properties"]["max_tokens"]
    assert max_tokens_schema["default"] is None
    assert max_tokens_schema["type"] == ["integer", "null"]


def test_malicious_yaml_untrusted_roots_and_lock_contention_are_isolated(tmp_path: Path, monkeypatch):
    malicious = tmp_path / "malicious" / "bad"
    malicious.mkdir(parents=True)
    (malicious / "SKILL.md").write_text(
        "---\nname: bad\ndescription: !python/object/apply:os.system ['echo unsafe']\n---\nBody\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    bad = sync_skill_roots([SkillRoot("bad", str(malicious.parent))], store_dir=store)
    assert bad.quarantined == 1
    assert bad.warnings[0]["code"] == "SKILL_FRONTMATTER_INVALID"
    assert active_skill_entries(store) == []
    assert load_skill_registry(store)["skills"][0]["status"] == "quarantined"

    escaping = tmp_path / "escaping"
    _write_skill(
        escaping,
        "escape",
        name="escape",
        description="Attempt an escaping reference",
        body="# Workflow\nRead [outside](../secret.txt).",
    )
    escaped_sync = sync_skill_roots([SkillRoot("escape", str(escaping))], store_dir=tmp_path / "escape-store")
    assert escaped_sync.quarantined == 1
    assert escaped_sync.warnings[0]["code"] == "SKILL_RESOURCE_OUTSIDE_ROOT"

    native_root = tmp_path / "codex-home" / "skills" / "managed"
    _write_skill(
        native_root,
        "native-opt-in",
        name="native-opt-in",
        description="Native overlap opt-in fixture",
        body="# Workflow\nUse explicit authorization.",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    with pytest.raises(ValueError, match="native skill scan paths"):
        sync_skill_roots([SkillRoot("native", str(native_root))], store_dir=tmp_path / "native-store")
    opted_in = sync_skill_roots(
        [SkillRoot("native", str(native_root), allow_native_scan_overlap=True)],
        store_dir=tmp_path / "native-opt-in-store",
    )
    assert opted_in.compiled == 1
    assert active_skill_entries(tmp_path / "native-opt-in-store")[0]["name"] == "native-opt-in"

    trusted = tmp_path / "trusted"
    _write_skill(trusted, "okay", name="okay", description="Okay workflow", body="# Workflow\nOkay.")
    ignored = sync_skill_roots([SkillRoot("ignored", str(trusted), trusted=False)], store_dir=tmp_path / "ignored-store")
    assert ignored.quarantined == 1
    assert active_skill_entries(tmp_path / "ignored-store") == []

    locked_store = tmp_path / "locked-store"
    lock_path = locked_store / "skills" / "registry.lock"
    lock_path.parent.mkdir(parents=True)
    with FileLock(str(lock_path)):
        locked = sync_skill_roots([], store_dir=locked_store, lock_timeout=0.01)
    assert locked.status == "error"
    assert locked.warnings[0]["code"] == "LOCK_TIMEOUT"


def test_held_out_intents_and_context_reduction(tmp_path: Path):
    roots = tmp_path / "roots"
    cases = [
        ("invoice-audit", "Audit invoices and accounting totals 稽核發票會計總額", "檢查發票會計總額"),
        ("photo-resize", "Resize photographs for social media", "resize photographs for a post"),
        ("release-check", "Validate application deployment releases", "validate deployment release"),
        ("pdf-citation", "Find PDF evidence and citations", "find PDF citation evidence"),
    ]
    for name, description, _ in cases:
        examples = "\n\n".join(f"Example {index}: {name} " + ("detail " * 60) for index in range(12))
        _write_skill(
            roots,
            name,
            name=name,
            description=description,
            body=f"# Guardrails\nPreserve source truth.\n\n# Workflow\nRun {name}.\n\n# Examples\n{examples}",
        )
    store = tmp_path / "store"
    sync_skill_roots([SkillRoot("local", str(roots))], store_dir=store)
    for expected, _, query in cases:
        result = query_skill_candidates(query, max_skills=3, store_dir=store)
        assert result["status"] == "ok"
        assert result["candidates"][0]["name"] == expected

    bundle = load_skill_bundle("validate deployment release", ["release-check"], max_tokens=1000, store_dir=store)
    source = (roots / "release-check" / "SKILL.md").read_text(encoding="utf-8")
    assert bundle.status == "ok"
    assert bundle.budget["spent_tokens"] <= 1000
    assert bundle.budget["spent_tokens"] < len(source) * 0.5


def test_1000_skill_warm_load_p95_is_bounded(tmp_path: Path):
    roots = tmp_path / "roots"
    for index in range(1000):
        name = f"fixture-skill-{index:04d}"
        _write_skill(
            roots,
            name,
            name=name,
            description=f"Handle deterministic fixture topic {index:04d}",
            body="# Guardrails\nPreserve fixture truth.\n\n# Workflow\nRun the requested fixture workflow.\n",
        )
    store = tmp_path / "store"
    synced = sync_skill_roots([SkillRoot("scale", str(roots))], store_dir=store)
    assert synced.compiled == 1000

    samples = []
    for index in range(40):
        name = f"fixture-skill-{index * 23:04d}"
        started = time.perf_counter()
        bundle = load_skill_bundle(name, [f"scale:{name}"], max_tokens=1000, store_dir=store)
        samples.append((time.perf_counter() - started) * 1000.0)
        assert bundle.status == "ok"
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    assert p95 <= 250.0
