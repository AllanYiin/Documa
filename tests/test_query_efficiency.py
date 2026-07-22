"""Regression tests for query-efficiency mechanisms: document cache, budgets, response size."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent, to_plain_data
from documa.interfaces import call_documa_tool
from documa.interfaces import token_counting
from documa.interfaces import tools as tools_module


class _CharCounter:
    """Deterministic test counter: one token per character."""

    name = "test:chars"

    def count(self, text):
        return len(text)

    def truncate(self, text, max_tokens):
        if len(text) <= max_tokens:
            return text, False
        return text[:max_tokens], True


def _paragraph(block_id: str, text: str, order_index: int = 1) -> BlockIR:
    return BlockIR(
        id=block_id,
        type=BlockType.PARAGRAPH,
        page_number=1,
        text=TextContent(text),
        bbox=(40, 40 + order_index * 20, 260, 58 + order_index * 20),
        order_index=order_index,
    )


def _write_ir(directory: str, document: DocumentIR, name: str = "documa.ir.json") -> Path:
    ir_path = Path(directory) / name
    ir_path.write_text(json.dumps(to_plain_data(document), ensure_ascii=False), encoding="utf-8")
    return ir_path


def _fixture_document(text: str = "快取測試內容 cache-probe") -> DocumentIR:
    return DocumentIR(
        id="d1",
        source_name="cache.pdf",
        pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=[_paragraph("b1", text)])],
    )


class DocumentCacheTests(unittest.TestCase):
    def setUp(self):
        tools_module.clear_document_cache()

    def tearDown(self):
        tools_module.clear_document_cache()

    def test_cache_hit_skips_reparse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            with mock.patch.object(
                tools_module,
                "document_from_plain_data",
                wraps=tools_module.document_from_plain_data,
            ) as parse_spy:
                first = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
                second = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})

            self.assertFalse(first["isError"])
            self.assertFalse(second["isError"])
            self.assertEqual(parse_spy.call_count, 1)

    def test_cache_invalidates_when_ir_file_is_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document("第一版內容 first-version"))
            before = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "first-version"})
            self.assertEqual(len(before["structuredContent"]["results"]), 1)

            # Rewrite with different content (and different size, so the
            # mtime/size cache key is guaranteed to change even on coarse clocks).
            _write_ir(tmp, _fixture_document("第二版內容 second-version-longer-body"))
            stale = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "first-version"})
            fresh = call_documa_tool("documa_search_blocks", {"ir_path": str(ir_path), "query": "second-version-longer-body"})

            self.assertEqual(stale["structuredContent"]["results"], [])
            self.assertEqual(len(fresh["structuredContent"]["results"]), 1)

    def test_cached_and_uncached_payloads_are_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            params = {"ir_path": str(ir_path), "query": "cache-probe"}

            warm_first = call_documa_tool("documa_search_blocks", params)
            warm_second = call_documa_tool("documa_search_blocks", params)
            tools_module.clear_document_cache()
            cold = call_documa_tool("documa_search_blocks", params)

            self.assertEqual(warm_first["structuredContent"], warm_second["structuredContent"])
            self.assertEqual(warm_first["structuredContent"], cold["structuredContent"])

    def test_cache_eviction_keeps_recent_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                _write_ir(tmp, _fixture_document(f"doc-{index} 內容"), name=f"doc{index}.ir.json")
                for index in range(tools_module._DOCUMENT_CACHE_MAX_ENTRIES + 2)
            ]
            for path in paths:
                call_documa_tool("documa_inspect", {"ir_path": str(path)})
            self.assertLessEqual(len(tools_module._DOCUMENT_CACHE), tools_module._DOCUMENT_CACHE_MAX_ENTRIES)

    def test_rag_export_chunking_does_not_pollute_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document("導出測試 " + "長內容片段。" * 40))
            # Warm the cache, then export rag-json (which chunks with max_chars).
            call_documa_tool("documa_inspect", {"ir_path": str(ir_path)})
            call_documa_tool("documa_export", {"ir_path": str(ir_path), "format": "rag-json", "max_chars": 60})
            cached = next(iter(tools_module._DOCUMENT_CACHE.values()))
            self.assertEqual(cached.chunks, [])


class TokenBudgetTests(unittest.TestCase):
    def setUp(self):
        tools_module.clear_document_cache()
        token_counting.set_token_counter(_CharCounter())

    def tearDown(self):
        tools_module.clear_document_cache()
        token_counting.reset_token_counter()

    def test_tiktoken_counter_counts_real_tokens(self):
        try:
            counter = token_counting.TiktokenCounter()
        except Exception as exc:  # pragma: no cover - env without tiktoken
            self.skipTest(f"tiktoken unavailable: {exc}")
        cjk = "資本緩衝要求說明文件內容" * 10
        count = counter.count(cjk)
        # Real BPE counts; a chars/4 guess would report 30 for 120 CJK chars.
        self.assertGreater(count, len(cjk) // 4)
        truncated_text, was_truncated = counter.truncate(cjk, count // 2)
        self.assertTrue(was_truncated)
        self.assertLessEqual(counter.count(truncated_text), count // 2)

    def test_tiktoken_budget_counts_final_serialized_payload(self):
        try:
            counter = token_counting.TiktokenCounter()
        except Exception as exc:  # pragma: no cover - env without tiktoken
            self.skipTest(f"tiktoken unavailable: {exc}")
        token_counting.set_token_counter(counter)

        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            bounded = call_documa_tool(
                "documa_search_blocks",
                {
                    "ir_path": str(ir_path),
                    "query": "budget-needle",
                    "limit": 10,
                    "max_response_tokens": 180,
                },
            )["structuredContent"]

            serialized = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(bounded["status"], "ok")
            self.assertLessEqual(counter.count(serialized), 180)
            self.assertEqual(bounded["budget"]["spent_tokens"], counter.count(serialized))


    def test_anthropic_counter_caches_and_truncates_without_extra_calls(self):
        from types import SimpleNamespace

        class _FakeMessages:
            def __init__(self):
                self.calls = 0

            def count_tokens(self, model, messages):
                self.calls += 1
                return SimpleNamespace(input_tokens=len(messages[0]["content"]))

        fake_client = SimpleNamespace(messages=_FakeMessages())
        counter = token_counting.AnthropicTokenCounter(model="claude-sonnet-5", client=fake_client)

        first = counter.count("資本緩衝要求")
        second = counter.count("資本緩衝要求")

        self.assertEqual(first, 6)
        self.assertEqual(second, 6)
        # The second count must come from the content-hash cache, not the API.
        self.assertEqual(fake_client.messages.calls, 1)

        truncated_text, was_truncated = counter.truncate("資本緩衝要求的完整說明", 5)
        self.assertTrue(was_truncated)
        self.assertEqual(truncated_text, "資本緩衝要")

    def test_token_fields_are_null_without_a_counter(self):
        token_counting.set_token_counter(None)
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            block_id = listed["structuredContent"]["blocks"][-1]["id"]

            read = call_documa_tool("documa_read_block", {"ir_path": str(ir_path), "block_id": block_id})
            searched = call_documa_tool(
                "documa_search_blocks", {"ir_path": str(ir_path), "query": "cache-probe", "response_profile": "evidence"}
            )

            self.assertIsNone(read["structuredContent"]["token_estimate"])
            self.assertIsNone(read["structuredContent"]["token_counter"])
            self.assertIsNone(searched["structuredContent"]["results"][0]["token_estimate"])

    def test_token_budget_params_error_without_a_counter(self):
        token_counting.set_token_counter(None)
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            block_id = listed["structuredContent"]["blocks"][-1]["id"]

            read = call_documa_tool(
                "documa_read_block",
                {"ir_path": str(ir_path), "block_id": block_id, "max_tokens": 40},
            )
            searched = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "cache-probe", "max_response_tokens": 300},
            )

            self.assertEqual(read["structuredContent"]["code"], "TOKEN_COUNTER_UNAVAILABLE")
            self.assertEqual(searched["structuredContent"]["code"], "TOKEN_COUNTER_UNAVAILABLE")

    def test_read_block_max_tokens_truncates_content(self):
        text = "資本緩衝要求的完整說明。" * 50
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document(text))
            listed = call_documa_tool("documa_list_blocks", {"ir_path": str(ir_path)})
            block_id = listed["structuredContent"]["blocks"][-1]["id"]

            full = call_documa_tool("documa_read_block", {"ir_path": str(ir_path), "block_id": block_id})
            budgeted = call_documa_tool(
                "documa_read_block",
                {"ir_path": str(ir_path), "block_id": block_id, "max_tokens": 40},
            )

            self.assertFalse(full["structuredContent"]["truncated"])
            self.assertIn("token_estimate", full["structuredContent"])
            self.assertTrue(budgeted["structuredContent"]["truncated"])
            self.assertLessEqual(budgeted["structuredContent"]["token_estimate"], 40)
            self.assertLess(
                len(budgeted["structuredContent"]["content"]),
                len(full["structuredContent"]["content"]),
            )

    def _multi_block_document(self, count: int = 6) -> DocumentIR:
        blocks = [
            _paragraph(f"b{index}", f"預算測試段落 {index} budget-needle 內容說明。", order_index=index)
            for index in range(1, count + 1)
        ]
        return DocumentIR(
            id="d1",
            source_name="budget.pdf",
            pages=[PageIR(id="p1", page_number=1, width=400, height=500, blocks=blocks)],
        )

    def test_default_nav_profile_is_navigation_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, _fixture_document())
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "cache-probe"},
            )["structuredContent"]

            self.assertEqual(payload["response_profile"], "nav")
            self.assertEqual(
                set(payload["results"][0]),
                {"block_id", "kind", "path", "page", "score", "coverage", "snippet", "read_chars"},
            )
            self.assertNotIn("searched_fields", payload)
            self.assertNotIn("snippet_policy", payload)

    def test_single_document_quoted_phrase_search(self):
        document = DocumentIR(
            id="d1",
            source_name="phrases.pdf",
            pages=[
                PageIR(
                    id="p1",
                    page_number=1,
                    width=400,
                    height=500,
                    blocks=[
                        _paragraph("b1", "The capital buffer requirement applies.", order_index=1),
                        _paragraph("b2", "Capital planning mentions a separate buffer.", order_index=2),
                    ],
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, document)
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": '"capital buffer"', "response_profile": "evidence"},
            )["structuredContent"]

            self.assertEqual(payload["terms"], ["capital buffer"])
            self.assertGreaterEqual(len(payload["results"]), 1)
            self.assertTrue(all(row["matched_terms"] == ["capital buffer"] for row in payload["results"]))
            snippets = [snippet["snippet"].casefold() for row in payload["results"] for snippet in row["snippets"]]
            self.assertTrue(all("capital buffer" in snippet for snippet in snippets))

    def test_exact_token_counts_only_run_after_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            with mock.patch.object(tools_module, "_count_tokens", wraps=tools_module._count_tokens) as count_spy:
                call_documa_tool(
                    "documa_search_blocks",
                    {
                        "ir_path": str(ir_path),
                        "query": "budget-needle",
                        "limit": 2,
                        "response_profile": "evidence",
                    },
                )
            self.assertEqual(count_spy.call_count, 2)

            with mock.patch.object(tools_module, "_count_tokens", wraps=tools_module._count_tokens) as nav_spy:
                call_documa_tool(
                    "documa_search_blocks",
                    {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2},
                )
            self.assertEqual(nav_spy.call_count, 0)


    def test_search_blocks_offset_pages_through_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            first = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2},
            )["structuredContent"]
            second = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2, "offset": 2},
            )["structuredContent"]

            self.assertGreaterEqual(first["total_matches"], 4)
            self.assertEqual(len(first["results"]), 2)
            self.assertEqual(second["offset"], 2)
            first_ids = {row["block_id"] for row in first["results"]}
            second_ids = {row["block_id"] for row in second["results"]}
            self.assertFalse(first_ids & second_ids)

    def test_search_blocks_max_response_tokens_drops_lowest_ranked_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            unbounded = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 10},
            )["structuredContent"]
            bounded = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 10, "max_response_tokens": 300},
            )["structuredContent"]

            self.assertGreater(len(unbounded["results"]), 1)
            self.assertLess(len(bounded["results"]), len(unbounded["results"]))
            # At least the top-ranked row always survives the budget.
            self.assertGreaterEqual(len(bounded["results"]), 1)
            self.assertEqual(bounded["results"][0]["block_id"], unbounded["results"][0]["block_id"])
            budget = bounded["budget"]
            self.assertEqual(budget["max_response_tokens"], 300)
            self.assertEqual(budget["dropped_results"], len(unbounded["results"]) - len(bounded["results"]))
            serialized = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
            self.assertLessEqual(len(serialized), 300)

    def test_list_blocks_supports_limit_and_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            page = call_documa_tool(
                "documa_list_blocks",
                {"ir_path": str(ir_path), "limit": 3, "offset": 1},
            )["structuredContent"]

            self.assertEqual(page["block_count"], 3)
            self.assertEqual(page["offset"], 1)
            self.assertGreater(page["total_blocks"], 4)
            self.assertTrue(page["has_more"])

    def test_search_blocks_recommends_next_read_and_paging_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 2},
            )["structuredContent"]

            recommended = payload["recommended_next"]
            first_action = recommended["actions"][0]
            self.assertIn(first_action["tool"], {"documa_read_block", "documa_source_window"})
            self.assertEqual(first_action["arguments"]["ir_path"], str(ir_path))
            self.assertEqual(first_action["arguments"]["block_id"], payload["results"][0]["block_id"])
            self.assertNotIn("block_ids", first_action["arguments"])
            self.assertTrue(any("offset=2" in hint for hint in payload["hints"]))

    def test_search_blocks_zero_results_hint_suggests_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "totally-absent-term"},
            )["structuredContent"]

            self.assertEqual(payload["results"], [])
            self.assertNotIn("recommended_next", payload)
            self.assertTrue(any("any_of" in hint for hint in payload["hints"]))

    def test_block_tree_supports_depth_and_node_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document())

            full = call_documa_tool("documa_block_tree", {"ir_path": str(ir_path)})["structuredContent"]
            bounded = call_documa_tool(
                "documa_block_tree",
                {"ir_path": str(ir_path), "max_depth": 1, "include_citations": False},
            )["structuredContent"]
            capped = call_documa_tool(
                "documa_block_tree",
                {"ir_path": str(ir_path), "max_nodes": 2},
            )["structuredContent"]

            self.assertFalse(full["truncated"])
            root = full["tree"][0]
            self.assertIn("citation_label", root)
            self.assertTrue(root["children"])

            bounded_root = bounded["tree"][0]
            self.assertNotIn("citation_label", bounded_root)
            # Depth-1 children collapse their own subtrees into counts.
            page_node = bounded_root["children"][0]
            self.assertNotIn("children", page_node)
            self.assertGreater(page_node["children_count"], 0)
            self.assertTrue(bounded["truncated"])
            self.assertLess(
                len(json.dumps(bounded, ensure_ascii=False)),
                len(json.dumps(full, ensure_ascii=False)),
            )

            self.assertTrue(capped["truncated"])

    def test_compact_search_response_size_stays_bounded(self):
        # Regression guardrail: the serialized compact response must stay small
        # enough that a default search costs well under ~1k estimated tokens.
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = _write_ir(tmp, self._multi_block_document(count=10))
            payload = call_documa_tool(
                "documa_search_blocks",
                {"ir_path": str(ir_path), "query": "budget-needle", "limit": 5},
            )["structuredContent"]

            serialized = json.dumps(payload, ensure_ascii=False)
            per_result = len(serialized) / max(1, len(payload["results"]))
            # Calibrated against the current compact row (~950 chars incl. legacy
            # aliases); a breach means response fat regressed, not a tuning goal.
            self.assertLessEqual(per_result, 1000, f"compact search row grew too large: {per_result:.0f} chars/result")
            self.assertLessEqual(len(serialized), 6000)


class RegistryMapCacheTests(unittest.TestCase):
    def setUp(self):
        from documa.collections import sqlite_index

        sqlite_index.clear_registry_entry_cache()

    def tearDown(self):
        from documa.collections import sqlite_index

        sqlite_index.clear_registry_entry_cache()

    def test_registry_map_is_cached_until_registry_file_changes(self):
        from documa.collections import registry as registry_store
        from documa.collections import sqlite_index
        from documa.collections.sqlite_index import build_collection_index, search_collection

        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / ".documa"
            source = Path(tmp) / "a.md"
            source.write_text("# A\n\ncached registry needle.\n", encoding="utf-8")
            result = registry_store.ingest_document(str(source), store_dir=store)
            build_collection_index(store)

            with mock.patch.object(
                sqlite_index.registry_store, "load_registry", wraps=registry_store.load_registry
            ) as load_spy:
                search_collection(store, query="needle")
                search_collection(store, query="needle")
                self.assertEqual(load_spy.call_count, 1)

                # Rewriting registry.json invalidates the mtime/size cache key.
                registry_path = store / "registry.json"
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                registry["documents"][0]["status"] = "superseded"
                registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

                stale = search_collection(store, query="needle")
                self.assertEqual(load_spy.call_count, 2)
                self.assertEqual(stale["result_count"], 0)
            self.assertEqual(result["status"], "ok")

    def test_document_cache_size_env_parsing(self):
        self.assertEqual(tools_module._env_cache_size("DOCUMA_TEST_ABSENT_VAR", default=16), 16)
        with mock.patch.dict("os.environ", {"DOCUMA_TEST_CACHE_VAR": "4"}):
            self.assertEqual(tools_module._env_cache_size("DOCUMA_TEST_CACHE_VAR", default=16), 4)
        with mock.patch.dict("os.environ", {"DOCUMA_TEST_CACHE_VAR": "0"}):
            self.assertEqual(tools_module._env_cache_size("DOCUMA_TEST_CACHE_VAR", default=16), 1)
        with mock.patch.dict("os.environ", {"DOCUMA_TEST_CACHE_VAR": "not-a-number"}):
            self.assertEqual(tools_module._env_cache_size("DOCUMA_TEST_CACHE_VAR", default=16), 16)


class CollectionSearchGuidanceTests(unittest.TestCase):
    """Budget, hints, and recommended_next for cross-document search."""

    def setUp(self):
        tools_module.clear_document_cache()
        token_counting.set_token_counter(_CharCounter())
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / ".documa"
        self._build_store()

    def tearDown(self):
        token_counting.reset_token_counter()
        self._tmp.cleanup()
        tools_module.clear_document_cache()

    def _build_store(self):
        from documa.collections import registry as registry_store
        from documa.collections.sqlite_index import build_collection_index

        for name in ("a", "b", "c", "d", "e"):
            source = Path(self._tmp.name) / f"{name}.md"
            source.write_text(f"# {name}\n\nspread needle appears in document {name}.\n", encoding="utf-8")
            registry_store.ingest_document(str(source), store_dir=self.store)
        build_collection_index(self.store)

    def _search(self, **params):
        payload = {"store_dir": str(self.store), "query": "needle", **params}
        return call_documa_tool("documa_search_collection", payload)["structuredContent"]

    def test_flat_search_recommends_read_and_group_hint(self):
        result = self._search(limit=10)

        recommended = result["recommended_next"]
        action = recommended["actions"][0]
        self.assertEqual(action["tool"], "documa_read_block")
        self.assertTrue(action["arguments"]["ir_path"].startswith("doc-"))
        self.assertEqual(action["arguments"]["block_id"], result["results"][0]["read_ref"]["block_id"])
        # Hits spread across >=4 documents without grouping -> group-mode hint.
        self.assertTrue(any("group_by_document" in hint for hint in result["hints"]))

    def test_grouped_search_recommends_top_block_of_top_document(self):
        result = self._search(limit=10, group_by_document=True)

        recommended = result["recommended_next"]
        top_ref = result["results"][0]["top_blocks"][0]["read_ref"]
        action = recommended["actions"][0]
        self.assertEqual(action["arguments"]["ir_path"], top_ref["ir_path"])
        self.assertEqual(action["arguments"]["block_id"], top_ref["block_id"])
        self.assertFalse(any("group_by_document" in hint for hint in result.get("hints", [])))

    def test_zero_results_hint_mentions_doctor(self):
        result = self._search(query="absent-token-entirely")
        self.assertEqual(result["results"], [])
        self.assertTrue(any("documa_doctor" in hint for hint in result["hints"]))
        self.assertNotIn("recommended_next", result)

    def test_offset_hint_appears_when_more_matches_exist(self):
        result = self._search(limit=2)
        self.assertTrue(result["has_more"])
        self.assertTrue(any("offset=2" in hint for hint in result["hints"]))

    def test_budget_requires_counter_and_drops_lowest_ranked(self):
        token_counting.set_token_counter(None)
        unavailable = self._search(max_response_tokens=500)
        self.assertEqual(unavailable["code"], "TOKEN_COUNTER_UNAVAILABLE")

        token_counting.set_token_counter(_CharCounter())
        unbounded = self._search(limit=10)
        bounded = self._search(limit=10, max_response_tokens=1200)

        self.assertGreater(len(unbounded["results"]), len(bounded["results"]))
        self.assertGreaterEqual(len(bounded["results"]), 1)
        self.assertEqual(bounded["budget"]["dropped_results"], len(unbounded["results"]) - len(bounded["results"]))
        self.assertEqual(bounded["result_count"], len(bounded["results"]))


if __name__ == "__main__":
    unittest.main()
