import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from documa.context import (
    ContextAuthority,
    ContextBlock,
    ContextContractError,
    ContextIR,
    ContextRelation,
    ContextService,
    ContextSourceKind,
    RelationOrigin,
    context_from_code,
    context_from_document,
    context_from_skill,
    load_context_ir,
    save_context_ir,
    sha256_text,
)
from documa.core.ir import (
    BlockIR,
    BlockType,
    DocumentBlockIR,
    DocumentBlockType,
    DocumentIR,
    PageIR,
    TextContent,
)
from documa.interfaces import context_read_blocks_tool, context_search_tool
from documa.skills.models import SkillBlockIR, SkillBlockRole, SkillEdgeIR, SkillEdgeType, SkillIR


class WordCounter:
    name = "word-counter"

    def count(self, text: str) -> int:
        return len(text.split())


def make_context() -> ContextIR:
    def block(block_id: str, title: str, body: str, *, pinned: bool = False) -> ContextBlock:
        return ContextBlock(
            block_id=block_id,
            source_id="ctx",
            source_kind=ContextSourceKind.CODE,
            title=title,
            body=body,
            source_locator="src/example.py",
            content_hash=sha256_text(body),
            authority=ContextAuthority.DEVELOPER,
            pinned=pinned,
        )

    return ContextIR(
        context_id="ctx",
        source_kind=ContextSourceKind.CODE,
        source_digest="sha256:source",
        blocks=[
            block("a", "入口", "入口會呼叫處理器"),
            block("b", "處理器", "處理器依賴安全規則"),
            block("c", "安全規則", "安全規則必須保留", pinned=True),
            block("d", "相似候選", "只由語意邊連接"),
        ],
        relations=[
            ContextRelation("a", "b", "calls"),
            ContextRelation("b", "c", "requires"),
            ContextRelation("a", "d", "similar_to", origin=RelationOrigin.INFERRED),
        ],
    )


class ContextServiceTests(unittest.TestCase):
    def test_explore_uses_only_hard_edges_by_default(self):
        result = ContextService(make_context()).search(
            "入口關係",
            intent="explore",
            seed_block_ids=["a"],
            max_hops=2,
        )

        self.assertTrue(result["graphUsed"])
        self.assertEqual([item["blockId"] for item in result["candidates"]], ["a", "b", "c"])
        self.assertNotIn("d", {item["blockId"] for item in result["candidates"]})
        self.assertEqual(result["requiredBlockIds"], ["c"])

    def test_semantic_edges_require_explicit_opt_in(self):
        result = ContextService(make_context()).search(
            "關係",
            intent="explore",
            seed_block_ids=["a"],
            allow_semantic_edges=True,
            max_hops=1,
        )

        by_id = {item["blockId"]: item for item in result["candidates"]}
        self.assertIn("d", by_id)
        self.assertIn("GRAPH_SOFT_CANDIDATE", by_id["d"]["reasons"])

    def test_digest_mismatch_falls_back_to_lexical_only(self):
        result = ContextService(make_context()).search(
            "入口",
            intent="explore",
            seed_block_ids=["a"],
            expected_source_digest="sha256:changed",
        )

        self.assertEqual(result["graphFreshness"], "stale")
        self.assertFalse(result["graphUsed"])
        self.assertEqual([item["blockId"] for item in result["candidates"]], ["a"])
        self.assertTrue(result["warnings"])

    def test_trace_returns_a_hash_bound_path(self):
        result = ContextService(make_context()).search(
            "依賴路徑",
            intent="trace",
            seed_block_ids=["a"],
            target_block_ids=["c"],
            max_hops=2,
        )

        target = next(item for item in result["candidates"] if item["blockId"] == "c")
        self.assertEqual([step["type"] for step in target["graphPath"]], ["calls", "requires"])

    def test_read_adds_required_blocks_and_enforces_real_token_counter(self):
        service = ContextService(make_context(), token_counter=WordCounter())
        result = service.read_blocks(
            ["a"],
            required_block_ids=["c"],
            expected_source_digest="sha256:source",
            total_max_tokens=10,
        )

        self.assertEqual([item["blockId"] for item in result["blocks"]], ["c", "a"])
        self.assertEqual(result["sourceTreeHash"], "sha256:source")
        self.assertTrue(all(item["contentHash"] == sha256_text(item["body"]) for item in result["blocks"]))

        with self.assertRaisesRegex(ContextContractError, "real token counter"):
            ContextService(make_context()).read_blocks(["a"], total_max_tokens=10)

    def test_constructor_rejects_tampered_body(self):
        context = make_context()
        context.blocks[0].body = "tampered"

        with self.assertRaisesRegex(ContextContractError, "Body hash mismatch"):
            ContextService(context)

    def test_navigation_token_cap_requires_a_real_counter(self):
        with self.assertRaisesRegex(ContextContractError, "real token counter"):
            ContextService(make_context()).search("入口", max_navigation_tokens=100)


class ContextAdapterTests(unittest.TestCase):
    def test_document_projection_keeps_block_text_and_parent_relation(self):
        source = BlockIR(
            id="raw-1",
            type=BlockType.PARAGRAPH,
            page_number=1,
            text=TextContent("原始文件內容"),
        )
        document = DocumentIR(
            id="doc-1",
            source_name="report.pdf",
            pages=[PageIR(id="page-1", page_number=1, width=100, height=100, blocks=[source])],
            document_blocks=[
                DocumentBlockIR(id="root", type=DocumentBlockType.DOCUMENT, child_ids=["paragraph"]),
                DocumentBlockIR(
                    id="paragraph",
                    type=DocumentBlockType.PARAGRAPH,
                    parent_id="root",
                    source_block_ids=["raw-1"],
                    page_refs=[1],
                    order_index=1,
                ),
            ],
        )

        context = context_from_document(document)

        paragraph = next(item for item in context.blocks if item.block_id == "paragraph")
        self.assertEqual(paragraph.body, "原始文件內容")
        self.assertEqual(paragraph.content_hash, sha256_text("原始文件內容"))
        self.assertTrue(any(edge.type == "parent" for edge in context.relations))

        source.text = TextContent("更新後文件內容")
        changed = context_from_document(document)
        self.assertNotEqual(context.source_digest, changed.source_digest)

    def test_skill_projection_preserves_required_dependencies(self):
        skill = SkillIR(
            skill_id="skill-1",
            qualified_name="demo:skill",
            name="skill",
            description="demo",
            generation="g1",
            source_digest="abc",
            source_root_id="root",
            source_path="SKILL.md",
            blocks=[
                SkillBlockIR(
                    id="workflow",
                    resource_path="SKILL.md",
                    kind="markdown",
                    role=SkillBlockRole.WORKFLOW,
                    text=TextContent("執行流程"),
                ),
                SkillBlockIR(
                    id="guard",
                    resource_path="SKILL.md",
                    kind="markdown",
                    role=SkillBlockRole.GUARDRAIL,
                    text=TextContent("不可略過"),
                    metadata={"required": True},
                ),
            ],
            edges=[SkillEdgeIR(SkillEdgeType.REQUIRES_BLOCK, "workflow", "guard")],
        )

        context = context_from_skill(skill)

        workflow = next(item for item in context.blocks if item.block_id == "workflow")
        guard = next(item for item in context.blocks if item.block_id == "guard")
        self.assertEqual(workflow.depends_on, ["guard"])
        self.assertTrue(guard.pinned)
        self.assertRegex(context.source_digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(context.metadata["sourceDigest"], "abc")

    def test_python_projection_extracts_symbols_containment_and_calls(self):
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.py"
            source.write_text(
                "def helper():\n    return 1\n\nclass Runner:\n    def run(self):\n        return helper()\n",
                encoding="utf-8",
            )

            context = context_from_code([source], context_id="code")

        by_symbol = {item.metadata["symbol"]: item for item in context.blocks}
        self.assertEqual(by_symbol["run"].parent_id, by_symbol["Runner"].block_id)
        self.assertTrue(
            any(
                edge.type == "calls"
                and edge.source_block_id == by_symbol["run"].block_id
                and edge.target_block_id == by_symbol["helper"].block_id
                for edge in context.relations
            )
        )


class ContextToolTests(unittest.TestCase):
    def test_context_file_round_trip_and_tools(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "context.json"
            save_context_ir(make_context(), path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            loaded = load_context_ir(path)

            self.assertEqual(raw["schemaVersion"], "1.0")
            self.assertEqual(loaded.context_id, "ctx")

            search = context_search_tool(str(path), "入口", expected_source_digest="sha256:source")
            read = context_read_blocks_tool(
                str(path),
                [search["candidates"][0]["blockId"]],
                expected_source_digest="sha256:source",
            )

        self.assertEqual(search["status"], "ok")
        self.assertEqual(read["status"], "ok")
        self.assertEqual(read["blocks"][0]["body"], "入口會呼叫處理器")


if __name__ == "__main__":
    unittest.main()
