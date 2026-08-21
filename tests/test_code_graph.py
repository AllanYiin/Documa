from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from documa.codegraph import (
    CodeGraphError,
    ScipIndexAdapter,
    code_context,
    query_code_graph,
    read_code_evidence,
    sync_code_graph,
)
from documa.cli import main
from documa.interfaces import call_documa_tool
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
