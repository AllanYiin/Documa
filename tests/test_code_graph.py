from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from documa.codegraph import (
    CodeGraphError,
    RustCodeAdapter,
    ScipIndexAdapter,
    code_context,
    code_graph_status,
    query_code_graph,
    read_code_evidence,
    sync_code_graph,
    watch_code_graph,
)
from documa.codegraph.store import open_code_graph
from documa.cli import main
from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool
from documa.interfaces.token_counting import TiktokenCounter
from documa.interfaces.tool_schemas import documa_tool_schemas


class WordCounter:
    name = "word-counter"

    def count(self, text: str) -> int:
        return len(text.split())


class SummaryProvider:
    name = "fixture-summary"
    version = "1"

    def summarize(self, node: dict) -> str:
        return f"Derived summary for {node['qualifiedName']}"


def _write_repository(root: Path) -> None:
    package = root / "src" / "pkg"
    tests = root / "tests"
    package.mkdir(parents=True)
    tests.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='graph-fixture'\n", encoding="utf-8")
    (package / "__init__.py").write_text("__all__ = ['endpoint']\n", encoding="utf-8")
    (package / "repository.py").write_text(
        "class Repository:\n"
        "    def get(self):\n"
        "        return 'value'\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from .repository import Repository\n\n"
        "class Service:\n"
        "    def __init__(self):\n"
        "        self.repository = Repository()\n\n"
        "    def run(self):\n"
        "        return self.repository.get()\n",
        encoding="utf-8",
    )
    (package / "api.py").write_text(
        "from .service import Service\n\n"
        "def endpoint():\n"
        "    service = Service()\n"
        "    return service.run()\n",
        encoding="utf-8",
    )
    (tests / "test_api.py").write_text(
        "from pkg.api import endpoint\n\n"
        "def test_endpoint():\n"
        "    assert endpoint() == 'value'\n",
        encoding="utf-8",
    )


def test_unicode_sqlite_uri_and_source_root_reindex(tmp_path: Path):
    root = tmp_path / "專案 # % space"
    src = root / "src"
    src.mkdir(parents=True)
    (src / "rules.py").write_text("def pick():\n    return 1\n", encoding="utf-8")
    store = root / ".documa"
    first = sync_code_graph(root, source_roots=["."], store_dir=store)
    workspace = first["workspace_id"]
    assert query_code_graph(workspace, symbols=["pick"], store_dir=store)["nodes"][0]["qualifiedName"] == "src.rules.pick"
    assert code_graph_status(workspace, store)["freshness"] == {"state": "fresh"}

    second = sync_code_graph(root, source_roots=["src"], store_dir=store)
    assert second["generation"] != first["generation"]
    assert second["parsed"] == 1
    result = code_context(workspace, "pick", symbols=["pick"], store_dir=store)
    assert result["nodes"][0]["qualifiedName"] == "rules.pick"
    assert result["evidence"]["blocks"][0]["body"] == "def pick():\n    return 1"
    assert sync_code_graph(root, source_roots=["src"], store_dir=store)["parsed"] == 0


def test_scoped_resolution_matches_clean_rebuild_on_source_changes(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    initial = sync_code_graph(tmp_path, store_dir=store)
    workspace = initial["workspace_id"]

    def graph_rows(location: Path):
        connection = open_code_graph(workspace, location)
        try:
            return {
                table: {tuple(row) for row in connection.execute(f"SELECT * FROM {table}")}
                for table in ("files", "nodes", "edges", "occurrences", "blindspots", "metrics")
            }
        finally:
            connection.close()

    source = tmp_path / "src" / "pkg" / "repository.py"
    modified = source.read_text(encoding="utf-8").replace("return 'value'", "return str(5)")
    source.write_text(modified, encoding="utf-8")
    assert code_graph_status(workspace, store)["freshness"] == {
        "state": "stale", "reason": "source_manifest_changed", "added": 0, "removed": 0, "modified": 1,
    }
    edited = sync_code_graph(tmp_path, store_dir=store)
    assert edited["parsed"] == 1
    assert edited["resolution"]["mode"] == "scoped"
    assert 1 < edited["resolution"]["files"] < edited["file_count"]
    clean = tmp_path / ".documa" / "clean"
    assert sync_code_graph(tmp_path, store_dir=clean)["generation"] == edited["generation"]
    assert graph_rows(store) == graph_rows(clean)

    source.rename(source.with_name("other_repository.py"))
    stale = code_graph_status(workspace, store)["freshness"]
    assert (stale["added"], stale["removed"]) == (1, 1)
    renamed = sync_code_graph(tmp_path, store_dir=store)
    assert renamed["resolution"]["reason"] == "source_membership_changed"
    clean = tmp_path / ".documa" / "renamed"
    assert sync_code_graph(tmp_path, store_dir=clean)["generation"] == renamed["generation"]
    assert graph_rows(store) == graph_rows(clean)


def test_watch_coalesces_changes_and_clear_requires_confirmation(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    store = tmp_path / "shared # store"
    source = root / "sample.py"
    source.write_text("def original():\n    return 1\n", encoding="utf-8")
    watch = watch_code_graph(root, store_dir=store)
    first = next(watch)
    workspace = first["workspace_id"]
    assert _run_cli(["code-graph-status", workspace, "--store-dir", str(store)])[1]["freshness"]["state"] == "fresh"
    source.write_text("def original():\n    return 2\n", encoding="utf-8")
    (root / "new.py").write_text("def added():\n    return 3\n", encoding="utf-8")
    update = next(watch)
    assert update["parsed"] == 2 and update["generation"] != first["generation"]
    watch.close()

    other = tmp_path / "other"
    other.mkdir()
    selected = sync_code_graph(other, store_dir=store)["workspace_id"]
    args = ["code-graph-clear", workspace, "--store-dir", str(store)]
    assert _run_cli(args)[1]["code"] == "CODE_GRAPH_CONFIRMATION_REQUIRED"
    assert _run_cli([*args, "--confirm"]) == (0, {"status": "ok", "workspace_id": workspace, "cleared": True})
    assert _run_cli([*args, "--confirm"])[1]["cleared"] is False
    with pytest.raises(CodeGraphError, match="index is empty"):
        code_graph_status(workspace, store)
    assert code_graph_status(selected, store)["freshness"]["state"] == "fresh"
    assert "return 2" in source.read_text(encoding="utf-8")
    assert sync_code_graph(root, store_dir=store)["generation"] == update["generation"]


def test_branch_switch_and_interrupted_sync_keep_committed_generation(tmp_path: Path):
    source = tmp_path / "example.py"
    store = tmp_path / ".documa"

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.email=fixture@example.test", "-c", "user.name=Fixture",
             "-c", f"core.hooksPath={tmp_path / '.git' / 'fixture-hooks'}", "-c", "commit.gpgsign=false", *args],
            capture_output=True,
            check=True,
        )

    git("init", "-b", "base")
    source.write_text("def value():\n    return 'base'\n", encoding="utf-8")
    git("add", "example.py")
    git("commit", "-m", "base fixture")
    git("switch", "-c", "other")
    source.write_text("def value():\n    return 'other'\n", encoding="utf-8")
    git("commit", "-am", "other fixture")
    first = sync_code_graph(tmp_path, store_dir=store)
    workspace = first["workspace_id"]
    git("switch", "base")
    assert code_graph_status(workspace, store)["freshness"]["state"] == "stale"

    class InterruptedProvider(SummaryProvider):
        def summarize(self, node: dict) -> str:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        sync_code_graph(tmp_path, store_dir=store, enrichment_provider=InterruptedProvider())
    assert code_graph_status(workspace, store)["active_generation"] == first["generation"]
    second = sync_code_graph(tmp_path, store_dir=store)
    assert second["generation"] != first["generation"]
    assert code_graph_status(workspace, store)["freshness"]["state"] == "fresh"
    assert "return 'base'" in code_context(workspace, "value", symbols=["value"], store_dir=store)["evidence"]["blocks"][0]["body"]


def test_explore_reads_verified_callsites_and_caps_the_complete_response(tmp_path: Path, monkeypatch):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    indexed = sync_code_graph(tmp_path, store_dir=store)
    workspace = indexed["workspace_id"]
    counter = TiktokenCounter()
    symbol = "pkg.service.Service.run"
    result = code_context(
        workspace, "How do services read the repository?", intent="explore", symbols=[symbol],
        max_response_tokens=1500, token_counter=counter, store_dir=store,
    )
    assert result["nodes"][0]["qualifiedName"] == symbol
    assert result["freshness"] == {"state": "fresh"}
    assert result["budgetReceipt"]["used"] == counter.count(
        json.dumps({"status": "ok", **result}, ensure_ascii=False, separators=(",", ":"))
    ) <= 1500
    site = next(block for block in result["evidence"]["blocks"] if block.get("evidenceKind") == "callsite")
    assert site["sourceHash"].startswith("sha256:")
    assert read_code_evidence(workspace, [site["blockId"]], store_dir=store)["blocks"][0]["body"] == site["body"]

    args = ["code-graph-query", workspace, "--intent", "explore", "--symbol", symbol, "--store-dir", str(store)]
    monkeypatch.setenv("DOCUMA_TOKEN_COUNTER", "anthropic")
    args.extend(["--max-response-tokens", "1500"])
    exit_code, cli = _run_cli(args)
    assert exit_code == 0
    assert cli["budgetReceipt"]["tokenizer"].startswith("tiktoken:")
    assert cli["evidence"]["blocks"]
    schema = next(tool for tool in documa_tool_schemas("agent") if tool["name"] == "documa_code_context")
    assert "explore" in schema["inputSchema"]["properties"]["intent"]["enum"]
    assert "max_response_tokens" in schema["inputSchema"]["properties"]

    source = tmp_path / site["sourceLocator"]
    source.write_text(source.read_text(encoding="utf-8") + "\n# moved\n", encoding="utf-8")
    with pytest.raises(CodeGraphError, match="changed after indexing"):
        read_code_evidence(workspace, [site["blockId"]], store_dir=store)
    stale = code_context(workspace, symbol, intent="explore", token_counter=counter, store_dir=store)
    assert stale["code"] == "CODE_GRAPH_STALE" and stale["nextAction"]["tool"] == "documa_sync_code_graph"


def test_explore_preview_can_be_read_in_full_and_missing_tokenizer_fails(tmp_path: Path):
    source = tmp_path / "long_body.py"
    source.write_text("def render():\n" + "    value = '<|endoftext|>'\n" * 40 + "    return value\n", encoding="utf-8")
    store = tmp_path / ".documa"
    workspace = sync_code_graph(tmp_path, store_dir=store)["workspace_id"]
    with pytest.raises(CodeGraphError) as missing_counter:
        code_context(workspace, "render", intent="explore", store_dir=store)
    assert missing_counter.value.code == "TOKEN_COUNTER_UNAVAILABLE"
    result = code_context(workspace, "render", intent="explore", symbols=["render"], token_counter=TiktokenCounter(), store_dir=store)
    preview = result["evidence"]["blocks"][0]
    assert preview["completeBlock"] is False
    assert preview["sourceSpan"] == {"startLine": 1, "endLine": 24}
    assert result["nextAction"]["arguments"]["block_ids"][0] == preview["blockId"]
    assert "return value" in read_code_evidence(workspace, [preview["blockId"]], store_dir=store)["blocks"][0]["body"]


def test_repository_document_and_test_links_keep_verifiable_sources(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_repository(root)
    store = tmp_path / ".documa"
    readme = root / "README.md"
    readme.write_text(
        "# Verhalten 規格\n\nThe rule is [the service](src/pkg/service.py#run).\n\n"
        "A loose name is `run`. Invalid [anchor](src/pkg/service.py#missing).\n\n"
        "```text\n[pretend](src/pkg/service.py#run)\n```\n\n"
        "[Outside](../outside.py) and [network](https://example.invalid/a.py).\n",
        encoding="utf-8",
    )
    (tmp_path / "outside.py").write_text("def rejected():\n    pass\n", encoding="utf-8")
    (root / "src" / "pkg" / "other.py").write_text("def run():\n    return 'other'\n", encoding="utf-8")
    (root / "tests" / "test_service.py").write_text(
        "from pkg.service import Service\n"
        "def test_service_run():\n"
        "    service = Service()\n"
        "    assert service.run() == 'value'\n",
        encoding="utf-8",
    )
    archived = DocumentIR(id="archived", source_name="detached", parser="fixture", pages=[PageIR(
        id="pg1", page_number=1, width=10, height=10,
        blocks=[BlockIR(id="source_b1", type=BlockType.PARAGRAPH, page_number=1,
                        text=TextContent("Archived notes for `pkg.service.Service.run`."))],
    )])
    archive_path = root / "archive.ir.json"
    archive_path.write_text(json.dumps(to_plain_data(archived), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CodeGraphError) as outside:
        sync_code_graph(root, document_paths=["../outside.py"], store_dir=store)
    assert outside.value.code == "CODE_DOCUMENT_OUTSIDE_SCOPE"

    code, synced = _run_cli(["code-graph-sync", str(root), "--document-path", "archive.ir.json", "--store-dir", str(store)])
    assert code == 0
    workspace = synced["workspace_id"]
    impact = query_code_graph(workspace, intent="impact", symbols=["pkg.service.Service.run"], max_nodes=40, store_dir=store)
    docs_by_locator = {
        node["sourceLocator"]: node for node in impact["nodes"] if node["kind"] == "document_block"
    }
    assert {"README.md", "archive.ir.json"} == docs_by_locator.keys()
    assert any(edge["type"] == "tests" for edge in impact["edges"])
    assert impact["impactReceipt"]["candidateTestEvidence"]

    ids = [docs_by_locator[locator]["nodeId"] for locator in ("README.md", "archive.ir.json")]
    read = read_code_evidence(workspace, ids, expected_generation=synced["generation"], store_dir=store)["blocks"]
    assert read[0]["body"] == "The rule is [the service](src/pkg/service.py#run)."
    assert read[0]["sourceSpan"]["startLine"] == 3
    assert read[1]["body"] == "Archived notes for `pkg.service.Service.run`."
    assert read[1]["documentBlockId"] == "source_b1" and read[1]["sourceHash"].startswith("sha256:")
    connection = open_code_graph(workspace, store)
    try:
        rows = list(connection.execute("SELECT resolution,metadata_json FROM edges WHERE type = 'documents'"))
        unresolved = [row["expression"] for row in connection.execute(
            "SELECT expression FROM blindspots WHERE code = 'UNRESOLVED_DOCUMENT_REFERENCE'"
        )]
    finally:
        connection.close()
    assert [(row["resolution"], json.loads(row["metadata_json"])["reference"]) for row in rows].count(("EXACT", "src/pkg/service.py#run")) == 1
    assert sum(json.loads(row["metadata_json"])["reference"] == "run" and row["resolution"] == "POSSIBLE" for row in rows) == 2
    assert unresolved == ["src/pkg/service.py#missing"]

    readme.write_text("# Updated\n\nThe reference was removed.\n", encoding="utf-8")
    with pytest.raises(CodeGraphError, match="changed after indexing"):
        read_code_evidence(workspace, [ids[0]], store_dir=store)
    archive_path.unlink()
    sync_code_graph(root, document_paths=["archive.ir.json"], store_dir=store)
    result = query_code_graph(workspace, intent="impact", symbols=["pkg.service.Service.run"], max_nodes=40, store_dir=store)
    assert not any(node["kind"] == "document_block" for node in result["nodes"])


def test_cargo_and_pyo3_index_at_normal_entry_points(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "alpha" / "src").mkdir(parents=True)
    (root / "helper" / "src").mkdir(parents=True)
    store = tmp_path / ".documa"
    (root / "Cargo.toml").write_text('[workspace]\nmembers = ["alpha", "helper"]\n', encoding="utf-8")
    (root / "alpha" / "Cargo.toml").write_text(
        '[package]\nname="alpha"\n[lib]\nname="_native"\n[dependencies]\nhelper={path="../helper"}\n', encoding="utf-8",
    )
    (root / "helper" / "Cargo.toml").write_text('[package]\nname="helper"\n', encoding="utf-8")
    (root / "setup.py").write_text(
        'from setuptools_rust import RustExtension\next = RustExtension("pkg._native", path="alpha/Cargo.toml")\n',
        encoding="utf-8",
    )
    (root / "src" / "pkg" / "__init__.py").write_text(
        'from ._native import extracted\ndef route():\n    return extracted()\n', encoding="utf-8",
    )
    (root / "alpha" / "src" / "lib.rs").write_text(
        '#[pyfunction(name = "extracted")]\nfn take() { helper::open(); }\n'
        '#[pymodule]\nfn _native(m: &Bound<PyModule>) { m.add_function(wrap_pyfunction!(take, m))?; }\n',
        encoding="utf-8",
    )
    (root / "helper" / "src" / "lib.rs").write_text('pub fn open() {}\n', encoding="utf-8")
    code, result = _run_cli([
        "code-graph-sync", str(root), "--store-dir", str(store), "--language", "python", "--language", "rust",
    ])
    assert code == 0
    workspace = result["workspace_id"]
    deps = query_code_graph(workspace, intent="dependencies", symbols=["alpha"], store_dir=store)
    assert any(edge["type"] == "depends_on_crate" for edge in deps["edges"])
    coverage = result["coverage"]["rust"]
    assert coverage["discovered"] == 2
    if RustCodeAdapter.capability_version().endswith("grammar-unavailable"):
        assert coverage["parsed"] == 0 and coverage["skipped"] == 2
        assert any(warning["code"] == "CODE_RUST_GRAMMAR_UNAVAILABLE" for warning in result["warnings"])
        return
    assert coverage["parsed"] == 2 and coverage["skipped"] == 0
    traced = query_code_graph(workspace, intent="trace", symbols=["pkg.route"], targets=["helper::open"], store_dir=store)
    binding = next(edge for edge in traced["edges"] if edge["type"] == "binds")
    assert binding["metadata"]["pythonName"] == "pkg._native.extracted"
    assert any(node["qualifiedName"] == "helper::open" for node in traced["nodes"])
    evidence = read_code_evidence(
        workspace, [binding["evidence"]["occurrenceId"], binding["metadata"]["registrationId"], binding["targetNodeId"]],
        store_dir=store,
    )["blocks"]
    assert [block["sourceLocator"] for block in evidence] == ["src/pkg/__init__.py", "alpha/src/lib.rs", "alpha/src/lib.rs"]
    assert evidence[1]["evidenceKind"] == "registration" and "wrap_pyfunction!(take" in evidence[1]["body"]


def test_git_diff_maps_commits_and_worktree_and_rereads_deleted_symbols(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    store = tmp_path / ".documa"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=fixture@example.test", "-c", "user.name=Fixture",
             "-c", f"core.hooksPath={root / '.git' / 'fixture-hooks'}", "-c", "commit.gpgsign=false", *args],
            capture_output=True, check=True, encoding="utf-8",
        ).stdout.strip()

    git("init", "-b", "base")
    (root / ".gitignore").write_text("excluded.py\n", encoding="utf-8")
    (root / "old.py").write_text("def removed():\n    return 'before'\n", encoding="utf-8")
    (root / "move.py").write_text("def move_here():\n    pass\n", encoding="utf-8")
    (root / "service.py").write_text("def service():\n    return 0\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base fixture")
    base = git("rev-parse", "HEAD")

    git("rm", "old.py")
    git("mv", "move.py", "renamed # file.py")
    (root / "service.py").write_text("def service():\n    return 1\n", encoding="utf-8")
    git("add", "service.py")
    git("commit", "-m", "change fixture")
    head = git("rev-parse", "HEAD")
    (root / "new.py").write_text("def added():\n    return 2\n", encoding="utf-8")
    (root / "excluded.py").write_text("def excluded():\n    pass\n", encoding="utf-8")
    (root / "service.py").write_text("def service():\n    return 3\n", encoding="utf-8")
    first = sync_code_graph(root, store_dir=store)
    workspace = first["workspace_id"]
    before_status = git("status", "--short")
    exit_code, report = _run_cli([
        "code-graph-query", workspace, "--intent", "git_diff", "--base-ref", base, "--head-ref", head,
        "--store-dir", str(store),
    ])
    assert exit_code == 0, report
    assert {entry["changeType"] for entry in report["changes"]} == {"modified", "renamed", "deleted"}
    removed = next(entry for entry in report["changes"] if entry["changeType"] == "deleted")
    assert removed["beforeSymbols"][0]["qualifiedName"] == "old.removed"
    cite = removed["beforeEvidence"][0]
    assert cite["revision"] == base and "return 'before'" in cite["body"]
    reread = read_code_evidence(workspace, [cite["blockId"]], store_dir=store)["blocks"][0]
    assert reread["body"] == cite["body"] and reread["sourceHash"] == cite["sourceHash"]

    working = query_code_graph(workspace, intent="git_diff", store_dir=store)
    assert working["base"] == head and working["head"] == "worktree"
    assert {(entry["changeType"], entry["afterPath"]) for entry in working["changes"]} == {("added", "new.py"), ("modified", "service.py")}
    assert git("status", "--short") == before_status
    with pytest.raises(CodeGraphError) as invalid:
        query_code_graph(workspace, intent="git_diff", base_ref="--unknown", store_dir=store)
    assert invalid.value.code == "CODE_GRAPH_GIT_REF_INVALID"


def test_tour_is_deterministic_and_cli_renders_source_links_without_a_counter(tmp_path: Path, monkeypatch):
    (tmp_path / "provider.py").write_text(
        "class Fast:\n    pass\n\nclass Portable:\n    pass\n\n"
        "def select_provider(fast=True):\n    if fast:\n        return Fast()\n    return Portable()\n",
        encoding="utf-8",
    )
    store = tmp_path / ".documa"
    workspace = sync_code_graph(tmp_path, store_dir=store)["workspace_id"]
    monkeypatch.setenv("DOCUMA_TOKEN_COUNTER", "none")
    command = ["code-graph-query", workspace, "--store-dir", str(store), "--intent", "tour", "--symbol", "select_provider"]
    code, result = _run_cli(command)
    assert code == 0
    assert _run_cli(command) == (0, result)
    assert result["tour"][0]["title"] == "provider.select_provider"
    assert result["tour"][0]["role"] == "entry"
    assert {step["title"] for step in result["tour"] if step["role"] == "branch"} == {"provider.Fast", "provider.Portable"}
    assert all(step["locator"] == "provider.py" and step["sourceHash"].startswith("sha256:") for step in result["tour"])
    buffer = StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    assert main([*command, "--format", "markdown"]) == 0
    assert "[provider.py:7](provider.py#L7-L10)" in buffer.getvalue()
    assert "**分支**" in buffer.getvalue()


def test_explore_and_tour_do_not_fetch_a_tokenizer_or_use_model_keys(tmp_path: Path, monkeypatch):
    import socket

    from tiktoken import load, registry

    counter = TiktokenCounter()

    def no_network(*args, **kwargs):
        raise AssertionError("Codegraph attempted to connect outside the repository")

    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DOCUMA_TOKEN_COUNTER", "tiktoken:o200k_base")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "empty-tokenizer-cache"))
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(load, "read_file", no_network)
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    workspace = sync_code_graph(tmp_path, store_dir=store)["workspace_id"]
    args = ["code-graph-query", workspace, "--store-dir", str(store), "--symbol", "pkg.api.endpoint", "--intent"]
    assert _run_cli([*args, "explore"])[1]["evidence"]["blocks"]
    assert _run_cli([*args, "tour"])[1]["tour"]
    with monkeypatch.context() as isolated:
        isolated.setattr(registry, "ENCODINGS", {})
        assert _run_cli([*args, "explore"])[1]["code"] == "TOKEN_COUNTER_UNAVAILABLE"
    assert counter.count("local tokenizer") > 0


def test_sync_builds_code_dependency_call_and_impact_graph(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"

    synced = sync_code_graph(tmp_path, store_dir=store)

    assert synced["status"] == "ok"
    assert synced["file_count"] == 5
    assert synced["node_count"] > 10
    assert synced["edge_count"] > 10
    workspace_id = synced["workspace_id"]

    impact = query_code_graph(
        workspace_id,
        intent="impact",
        symbols=["pkg.repository.Repository.get"],
        max_hops=5,
        max_nodes=30,
        store_dir=store,
    )
    names = {node["qualifiedName"] for node in impact["nodes"]}
    assert "pkg.service.Service.run" in names
    assert "pkg.api.endpoint" in names
    assert "tests.test_api.test_endpoint" in names
    assert impact["impactReceipt"]["candidateTests"]
    assert impact["proofPaths"]
    assert all(edge["resolution"] in {"EXACT", "RESOLVED"} for edge in impact["edges"])

    dependencies = query_code_graph(
        workspace_id,
        intent="dependencies",
        symbols=["pkg.api"],
        store_dir=store,
    )
    assert any(edge["type"] == "imports_module" for edge in dependencies["edges"])


def test_evidence_read_is_generation_and_hash_bound(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    synced = sync_code_graph(tmp_path, store_dir=store)
    workspace_id = synced["workspace_id"]
    lookup = query_code_graph(
        workspace_id,
        query="Repository get",
        intent="lookup",
        symbols=["pkg.repository.Repository.get"],
        store_dir=store,
    )
    block_id = lookup["nodes"][0]["nodeId"]

    read = read_code_evidence(
        workspace_id,
        [block_id],
        expected_generation=synced["generation"],
        total_max_tokens=100,
        token_counter=WordCounter(),
        store_dir=store,
    )
    assert "def get" in read["blocks"][0]["body"]
    assert read["blocks"][0]["contentHash"] == lookup["nodes"][0]["contentHash"]

    repository = tmp_path / "src" / "pkg" / "repository.py"
    repository.write_text(repository.read_text(encoding="utf-8").replace("'value'", "'changed'"), encoding="utf-8")
    with pytest.raises(CodeGraphError, match="changed after indexing"):
        read_code_evidence(workspace_id, [block_id], store_dir=store)


def test_incremental_sync_noop_diff_and_unavailable_file(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    first = sync_code_graph(tmp_path, store_dir=store)
    second = sync_code_graph(tmp_path, store_dir=store)
    assert second["changed"] is False
    assert second["parsed"] == 0

    repository = tmp_path / "src" / "pkg" / "repository.py"
    repository.write_text(repository.read_text(encoding="utf-8").replace("'value'", "'v2'"), encoding="utf-8")
    third = sync_code_graph(tmp_path, store_dir=store)
    assert third["parsed"] == 1
    assert third["previous_generation"] == first["generation"]
    diff = query_code_graph(third["workspace_id"], intent="diff", store_dir=store, max_nodes=100)
    assert any(change["entityType"] == "node" and change["changeType"] == "changed" for change in diff["changes"])

    broken = tmp_path / "src" / "pkg" / "broken.py"
    broken.write_text("def broken(:\n", encoding="utf-8")
    warning = sync_code_graph(tmp_path, store_dir=store)
    assert warning["status"] == "warning"
    assert any(item["code"] == "CODE_SYNTAX_ERROR" for item in warning["warnings"])


def test_cycles_uncertainty_and_one_call_context(tmp_path: Path):
    _write_repository(tmp_path)
    package = tmp_path / "src" / "pkg"
    (package / "a.py").write_text("from . import b\n\ndef run():\n    return getattr(b, 'go')()\n", encoding="utf-8")
    (package / "b.py").write_text("from . import a\n\ndef go():\n    return a.run\n", encoding="utf-8")
    store = tmp_path / ".documa"
    synced = sync_code_graph(tmp_path, store_dir=store)

    cycles = query_code_graph(synced["workspace_id"], intent="cycles", store_dir=store)
    assert cycles["nodes"]
    lookup = query_code_graph(
        synced["workspace_id"],
        intent="lookup",
        symbols=["pkg.a.run"],
        store_dir=store,
    )
    assert lookup["uncertaintyReceipt"]["count"] > 0

    context = code_context(
        synced["workspace_id"],
        "endpoint",
        symbols=["pkg.api.endpoint"],
        total_max_tokens=200,
        token_counter=WordCounter(),
        store_dir=store,
    )
    assert context["evidence"]["blocks"]
    assert context["recommendedNext"] == []


def _run_cli(arguments: list[str]) -> tuple[int, dict]:
    previous = sys.stdout
    sys.stdout = StringIO()
    try:
        exit_code = main(arguments)
        payload = json.loads(sys.stdout.getvalue())
    finally:
        sys.stdout = previous
    return exit_code, payload


def test_cli_tool_registry_and_profile_contract(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    exit_code, synced = _run_cli(["code-graph-sync", str(tmp_path), "--store-dir", str(store)])
    assert exit_code == 0
    assert synced["workspace_id"]

    exit_code, queried = _run_cli(
        [
            "code-graph-query",
            synced["workspace_id"],
            "endpoint",
            "--intent",
            "lookup",
            "--symbol",
            "pkg.api.endpoint",
            "--store-dir",
            str(store),
        ]
    )
    assert exit_code == 0
    assert queried["nodes"][0]["qualifiedName"] == "pkg.api.endpoint"

    direct = call_documa_tool(
        "documa_query_code_graph",
        {
            "workspace_id": synced["workspace_id"],
            "intent": "lookup",
            "symbols": ["pkg.api.endpoint"],
            "store_dir": str(store),
        },
    )
    assert direct["structuredContent"]["status"] == "ok"

    agent = {item["name"] for item in documa_tool_schemas("agent")}
    advanced = {item["name"] for item in documa_tool_schemas("advanced")}
    admin = {item["name"] for item in documa_tool_schemas("admin")}
    assert "documa_code_context" in agent
    assert "documa_query_code_graph" not in agent
    assert {"documa_query_code_graph", "documa_read_code_evidence"} <= advanced
    assert "documa_sync_code_graph" not in advanced
    assert "documa_sync_code_graph" in admin


def test_optional_summary_enrichment_is_derived_and_cached(tmp_path: Path):
    _write_repository(tmp_path)
    store = tmp_path / ".documa"
    first = sync_code_graph(tmp_path, store_dir=store)
    enriched = sync_code_graph(tmp_path, store_dir=store, enrichment_provider=SummaryProvider())
    assert enriched["changed"] is False
    assert enriched["enriched"] > 0
    second = sync_code_graph(tmp_path, store_dir=store, enrichment_provider=SummaryProvider())
    assert second["enriched"] == 0

    queried = query_code_graph(
        first["workspace_id"],
        intent="lookup",
        symbols=["pkg.api.endpoint"],
        store_dir=store,
    )
    assert queried["nodes"][0]["enrichedSummary"].startswith("Derived summary")
    assert queried["nodes"][0]["summaryEnrichment"]["authoritative"] is False


def test_decoded_scip_adapter_preserves_symbol_identity_and_ranges():
    adapter = ScipIndexAdapter()
    payload = {
        "documents": [
            {
                "relative_path": "src/example.ts",
                "symbols": [
                    {
                        "symbol": "scip-typescript npm demo 1.0.0 src/example.ts/Foo#run().",
                        "kind": "Method",
                        "documentation": ["Run the example."],
                    }
                ],
            }
        ]
    }
    nodes = adapter.symbols(payload, "workspace")
    assert nodes[0].kind.value == "method"
    assert nodes[0].docstring == "Run the example."
    span = adapter.occurrence_span({"range": [3, 4, 8]})
    assert span is not None and span.start_line == 4 and span.start_column == 4


def test_python_resolver_gold_precision_recall_and_import_classification(tmp_path: Path):
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "base.py").write_text(
        "class Base:\n"
        "    def run(self):\n"
        "        return 'base'\n\n"
        "class Repo:\n"
        "    async def fetch(self):\n"
        "        return 'value'\n\n"
        "class TypeOnly:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "hooks.py").write_text(
        "def register(callback):\n"
        "    return callback\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "from . import base as model\n"
        "from .base import Base, Repo as Repository\n"
        "from .hooks import register\n\n"
        "if TYPE_CHECKING:\n"
        "    from .base import TypeOnly\n"
        "if False:\n"
        "    import conditional_dep\n"
        "try:\n"
        "    import optional_dep\n"
        "except ImportError:\n"
        "    optional_dep = None\n\n"
        "class Service(Base):\n"
        "    def __init__(self):\n"
        "        self.repo: Repository = Repository()\n\n"
        "    async def run(self):\n"
        "        await self.repo.fetch()\n"
        "        return super().run()\n\n"
        "def handler():\n"
        "    return None\n\n"
        "def wire():\n"
        "    model.Repo()\n"
        "    register(handler)\n\n"
        "def outer():\n"
        "    def nested():\n"
        "        return Service()\n"
        "    return 1\n",
        encoding="utf-8",
    )
    store = tmp_path / ".documa"
    synced = sync_code_graph(tmp_path, source_roots=["src"], store_dir=store)

    actual: set[tuple[str, str, str]] = set()
    for symbol in (
        "pkg.service.Service.__init__",
        "pkg.service.Service.run",
        "pkg.service.wire",
        "pkg.service.outer",
    ):
        call_slice = query_code_graph(
            synced["workspace_id"],
            intent="callees",
            symbols=[symbol],
            max_hops=1,
            max_nodes=100,
            store_dir=store,
        )
        names = {node["nodeId"]: node["qualifiedName"] for node in call_slice["nodes"]}
        actual.update(
            (names[edge["sourceNodeId"]], names[edge["targetNodeId"]], edge["type"])
            for edge in call_slice["edges"]
            if edge["resolution"] in {"EXACT", "RESOLVED"}
        )
    expected = {
        ("pkg.service.Service.__init__", "pkg.base.Repo", "constructs"),
        ("pkg.service.Service.run", "pkg.base.Repo.fetch", "calls"),
        ("pkg.service.Service.run", "pkg.base.Base.run", "calls"),
        ("pkg.service.wire", "pkg.base.Repo", "constructs"),
        ("pkg.service.wire", "pkg.hooks.register", "calls"),
        ("pkg.service.wire", "pkg.service.handler", "registers_callback"),
    }
    precision = len(actual & expected) / len(actual)
    recall = len(actual & expected) / len(expected)
    assert precision >= 0.98, (actual, expected)
    assert recall >= 0.90, (actual, expected)
    assert not any(source == "pkg.service.outer" for source, _, _ in actual)

    dependencies = query_code_graph(
        synced["workspace_id"],
        intent="dependencies",
        symbols=["pkg.service"],
        max_hops=1,
        max_nodes=100,
        store_dir=store,
    )
    classifications = {
        edge["metadata"]["classification"]
        for edge in dependencies["edges"]
        if edge["type"] in {"imports_module", "imports_symbol"}
    }
    assert {"runtime", "type_checking", "conditional", "optional"} <= classifications
