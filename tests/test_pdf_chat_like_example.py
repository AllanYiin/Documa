import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "pdf_chat_like" / "pdf_chat_example.py"


def load_example_module():
    spec = importlib.util.spec_from_file_location("pdf_chat_example", EXAMPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PdfChatLikeExampleTests(unittest.TestCase):
    def test_keyword_cleanup_filters_low_value_metadata_and_broken_ngrams(self):
        module = load_example_module()

        cases = [
            (
                "the and context in of to answer with",
                {"the", "and", "in", "of", "to", "with"},
            ),
            (
                "Alex L. ZhangMIT CSAILaltzhang@mit.edu",
                {"alex l", "zhang mit", "csailaltzhang"},
            ),
            (
                "arXiv:2512.24601v1  [cs.AI]  31 Dec 2025",
                {"arxiv", "dec"},
            ),
            (
                "RECURSIVE LANGUAGE MODELS recursive language models recursive",
                {"models recursive language", "recursive language models recursive"},
            ),
        ]

        for text, forbidden in cases:
            keywords = module.ngram_keywords(text, top_k=8)
            folded = {item.casefold() for item in keywords}
            self.assertFalse(folded & forbidden, (text, keywords))
            self.assertTrue(all(len(set(item.split())) == len(item.split()) for item in keywords), keywords)

        self.assertIn("context answer", module.ngram_keywords("the and context in of to answer with", top_k=8))
        self.assertEqual(module.clean_keyword_candidate("arXiv:2512.24601v1  [cs.AI]  31 Dec 2025"), None)
        self.assertEqual(module.ngram_keywords("Alex L. ZhangMIT CSAILaltzhang@mit.edu", top_k=8), [])
        self.assertEqual(module.ngram_keywords("arXiv:2512.24601v1  [cs.AI]  31 Dec 2025", top_k=8), [])

    def test_response_api_usage_normalization_keeps_cached_tokens(self):
        module = load_example_module()
        usage = module.normalize_response_usage(
            {
                "input_tokens": 120,
                "input_tokens_details": {"cached_tokens": 80},
                "output_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 12},
                "total_tokens": 150,
            }
        )

        self.assertEqual(usage["source"], "response_api")
        self.assertFalse(usage["estimated"])
        self.assertEqual(usage["cached_tokens"], 80)
        self.assertEqual(usage["reasoning_tokens"], 12)
        self.assertEqual(usage["total_tokens"], 150)

    def test_safe_display_path_segment_removes_local_paths(self):
        module = load_example_module()
        path = r"C:\Users\allan\AppData\Local\Temp\documa_pdf_chat_x\paper.pdf"
        self.assertEqual(module.safe_display_path_segment(path), "paper.pdf")
        self.assertEqual(module.safe_display_path_segment("1 INTRODUCTION"), "1 INTRODUCTION")

    def test_answer_response_usage_is_aggregated_from_response_api(self):
        module = load_example_module()
        recorder = module.TraceRecorder(module.TokenCounter.create())
        recorder.add(
            "answer",
            "llm_response",
            {"ok": True},
            response_usage={
                "input_tokens": 1200,
                "input_tokens_details": {"cached_tokens": 1024},
                "output_tokens": 80,
                "total_tokens": 1280,
            },
        )

        self.assertEqual(recorder.events[0]["token_usage"]["source"], "response_api")
        aggregate = module.aggregate_token_usage(recorder.events)
        self.assertEqual(aggregate["source"], "response_api")
        self.assertEqual(aggregate["total_tokens"], 1280)
        self.assertEqual(aggregate["cached_tokens"], 1024)

    def test_client_create_forwards_previous_response_id(self):
        module = load_example_module()
        calls = []

        class FakeResponse:
            id = "resp_next"
            model = "fake-model"
            output_text = "ok"
            usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(kwargs)
                return FakeResponse()

        client = object.__new__(module.OpenAIResponsesClient)
        client.model = "fake-model"
        client.client = type("FakeClient", (), {"responses": FakeResponses()})()
        output = client.create(
            instructions="inst",
            input_text="input",
            previous_response_id="resp_prev",
            prompt_cache_key="documa:test",
        )

        self.assertEqual(output["response_id"], "resp_next")
        self.assertEqual(calls[0]["previous_response_id"], "resp_prev")
        self.assertTrue(calls[0]["store"])
        self.assertEqual(calls[0]["extra_body"]["prompt_cache_key"], "documa:test")

    def test_pdf_chat_uses_responses_function_tool_calling_when_llm_enabled(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "toolcalling.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=360, height=220)
            page.insert_text((24, 40), "Deployment Risk", fontsize=18)
            page.insert_text((24, 90), "Deployment risk includes rollback planning.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            block_id = next(block.id for block in session.document.document_blocks if block.source_block_ids)
            calls = []
            responses = [
                {
                    "response_id": "resp_search",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "search_blocks",
                            "arguments": json.dumps({"query": "deployment risk", "limit": 3}),
                        }
                    ],
                    "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                },
                {
                    "response_id": "resp_read",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_read",
                            "name": "read_block",
                            "arguments": json.dumps({"block_id": block_id, "max_chars": 1000}),
                        }
                    ],
                    "usage": {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36},
                },
                {
                    "response_id": "resp_final",
                    "model": "fake-model",
                    "text": "回答：Deployment risk 包含 rollback planning。\n\n依據：p.1 / block。",
                    "output": [],
                    "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
                },
            ]

            class FakeOpenAIResponsesClient:
                def __init__(self, *args, **kwargs):
                    pass

                def create(self, **kwargs):
                    calls.append(kwargs)
                    return responses.pop(0)

            original_client = module.OpenAIResponsesClient
            module.OpenAIResponsesClient = FakeOpenAIResponsesClient
            try:
                turn = session.answer("What deployment risk is mentioned?", use_llm=True)
            finally:
                module.OpenAIResponsesClient = original_client

            self.assertTrue(calls[0]["tools"])
            self.assertEqual(calls[0]["tools"][0]["name"], "search_blocks")
            self.assertTrue(any(item.get("type") == "function_call_output" for item in calls[1]["input_text"]))
            self.assertEqual(turn["answer"]["mode"], "llm_responses_api_tool_calling")
            tool_calls = [event for event in turn["events"] if event["type"] == "tool_call"]
            self.assertEqual([event["name"] for event in tool_calls], ["search_blocks", "read_block"])
            self.assertTrue(all(event["payload"]["source"] == "responses_api_function_call" for event in tool_calls))
            self.assertIn("Deployment risk", turn["answer"]["text"])

    def test_pdf_chat_tool_calling_can_batch_read_candidate_blocks(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "batch-read.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=360, height=220)
            page.insert_text((24, 40), "Deployment Risk", fontsize=18)
            page.insert_text((24, 90), "Deployment risk includes rollback planning and monitoring.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            block_id = next(block.id for block in session.document.document_blocks if block.source_block_ids)
            responses = [
                {
                    "response_id": "resp_search",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "search_blocks",
                            "arguments": json.dumps({"query": "deployment risk", "limit": 5, "max_snippets_per_block": 5}),
                        }
                    ],
                    "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                },
                {
                    "response_id": "resp_read",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_read_blocks",
                            "name": "read_blocks",
                            "arguments": json.dumps({"block_ids": [block_id], "max_chars_per_block": 1000}),
                        }
                    ],
                    "usage": {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36},
                },
                {
                    "response_id": "resp_final",
                    "model": "fake-model",
                    "text": "回答：Deployment risk 包含 rollback planning and monitoring。",
                    "output": [],
                    "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
                },
            ]

            class FakeOpenAIResponsesClient:
                def __init__(self, *args, **kwargs):
                    pass

                def create(self, **kwargs):
                    return responses.pop(0)

            original_client = module.OpenAIResponsesClient
            module.OpenAIResponsesClient = FakeOpenAIResponsesClient
            try:
                turn = session.answer("What deployment risk is mentioned?", use_llm=True)
            finally:
                module.OpenAIResponsesClient = original_client

            tool_calls = [event["name"] for event in turn["events"] if event["type"] == "tool_call"]
            self.assertEqual(tool_calls, ["search_blocks", "read_blocks"])
            search_result = next(event for event in turn["events"] if event["type"] == "tool_result" and event["name"] == "search_blocks")
            self.assertEqual(search_result["payload"]["snippet_policy"]["max_snippets_per_block"], 2)
            self.assertEqual(search_result["payload"]["retrieval_policy"]["next_recommended_tool"], "read_blocks")
            self.assertLessEqual(len(search_result["payload"]["retrieval_policy"]["candidate_block_ids"]), 2)
            self.assertEqual(search_result["payload"]["retrieval_policy"]["recommended_args"]["max_blocks"], 4)
            read_result = next(event for event in turn["events"] if event["type"] == "tool_result" and event["name"] == "read_blocks")
            self.assertEqual(read_result["payload"]["status"], "ok")
            self.assertTrue(read_result["payload"]["blocks"])
            self.assertTrue(turn["answer"]["evidence"])

    def test_retrieval_policy_reads_primary_before_secondary_candidates(self):
        module = load_example_module()
        candidates = [
            {
                "id": "p6_para1",
                "type": "paragraph",
                "title": "ABSTRACT",
                "page_refs": [6],
                "matched": ["rlm", "produce"],
                "matched_terms_count": 2,
                "score": 32.25,
                "rank": 1,
            },
            {
                "id": "p24_page",
                "type": "page",
                "title": "Page 24",
                "page_refs": [24],
                "matched": ["rlm"],
                "matched_terms_count": 1,
                "score": 19,
                "rank": 2,
            },
            {
                "id": "p13_para1",
                "type": "paragraph",
                "title": "ABSTRACT",
                "page_refs": [13],
                "matched": ["rlm"],
                "matched_terms_count": 1,
                "score": 18.25,
                "rank": 3,
            },
        ]

        policy = module.read_next_policy(candidates)

        self.assertEqual(policy["candidate_block_ids"], ["p6_para1"])
        self.assertEqual(policy["primary_block_ids"], ["p6_para1"])
        self.assertEqual(policy["secondary_block_ids"], ["p24_page", "p13_para1"])
        self.assertEqual(policy["recommended_args"]["block_ids"], ["p6_para1"])
        self.assertEqual(policy["recommended_args"]["max_blocks"], 4)
        self.assertFalse(policy["low_confidence"])

    def test_retrieval_policy_marks_single_term_matches_as_low_confidence(self):
        module = load_example_module()
        candidates = [
            {
                "id": "p24_page",
                "type": "page",
                "title": "Page 24",
                "page_refs": [24],
                "matched": ["rlm"],
                "matched_terms_count": 1,
                "score": 19,
                "rank": 1,
            },
            {
                "id": "p13_para1",
                "type": "paragraph",
                "title": "ABSTRACT",
                "page_refs": [13],
                "matched": ["rlm"],
                "matched_terms_count": 1,
                "score": 18.25,
                "rank": 2,
            },
            {
                "id": "p5_para1",
                "type": "paragraph",
                "title": "ABSTRACT",
                "page_refs": [5],
                "matched": ["rlm"],
                "matched_terms_count": 1,
                "score": 17.25,
                "rank": 3,
            },
        ]

        policy = module.read_next_policy(candidates)

        self.assertEqual(policy["candidate_block_ids"], ["p13_para1", "p5_para1"])
        self.assertEqual(policy["recommended_args"]["block_ids"], ["p13_para1", "p5_para1"])
        self.assertTrue(policy["low_confidence"])
        self.assertTrue(policy["allow_refined_search_after_primary_read"])

    def test_pdf_chat_tool_calling_blocks_repeated_search_before_reading(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "repeat-search.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=360, height=220)
            page.insert_text((24, 40), "Deployment Risk", fontsize=18)
            page.insert_text((24, 90), "Deployment risk includes rollback planning.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            responses = [
                {
                    "response_id": "resp_search",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_search",
                            "name": "search_blocks",
                            "arguments": json.dumps({"query": "deployment risk", "limit": 5}),
                        }
                    ],
                    "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                },
                {
                    "response_id": "resp_repeat",
                    "model": "fake-model",
                    "text": "",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_repeat",
                            "name": "search_blocks",
                            "arguments": json.dumps({"query": "rollback planning", "limit": 5}),
                        }
                    ],
                    "usage": {"input_tokens": 30, "output_tokens": 6, "total_tokens": 36},
                },
                {
                    "response_id": "resp_final",
                    "model": "fake-model",
                    "text": "回答：Deployment risk 包含 rollback planning。",
                    "output": [],
                    "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
                },
            ]

            class FakeOpenAIResponsesClient:
                def __init__(self, *args, **kwargs):
                    pass

                def create(self, **kwargs):
                    return responses.pop(0)

            original_client = module.OpenAIResponsesClient
            module.OpenAIResponsesClient = FakeOpenAIResponsesClient
            try:
                turn = session.answer("What deployment risk is mentioned?", use_llm=True)
            finally:
                module.OpenAIResponsesClient = original_client

            search_results = [
                event for event in turn["events"] if event["type"] == "tool_result" and event["name"] == "search_blocks"
            ]
            self.assertEqual(len(search_results), 2)
            self.assertEqual(search_results[1]["payload"]["status"], "policy_blocked")
            self.assertEqual(search_results[1]["payload"]["recommended_tool"], "read_blocks")
            self.assertEqual(turn["answer"]["mode"], "llm_responses_api_tool_calling_insufficient_evidence")

    def test_tfidf_keyword_index_filters_common_block_terms(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "tfidf.pdf"
            pdf = pymupdf.open()
            rows_text = [
                "Shared context answer alpha routing planner.",
                "Shared context answer beta memory cache.",
                "Shared context answer gamma synthesis trace.",
            ]
            for text in rows_text:
                page = pdf.new_page(width=520, height=220)
                page.insert_text((24, 40), "Keyword Experiment", fontsize=18)
                page.insert_text((24, 90), text, fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            rows = [row for row in session.list_blocks(depth=None)["blocks"] if row["type"] == "paragraph"]
            all_keywords = {keyword for row in rows for keyword in row["keywords"]}

            self.assertTrue(any(any("alpha routing" in keyword for keyword in row["keywords"]) for row in rows), rows)
            self.assertTrue(any(any("beta memory" in keyword for keyword in row["keywords"]) for row in rows), rows)
            self.assertTrue(any(any("gamma synthesis" in keyword for keyword in row["keywords"]) for row in rows), rows)
            self.assertNotIn("shared context answer", all_keywords)

    def test_pdf_chat_like_example_outputs_tool_trace(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "sample.pdf"
            out_dir = tmp_path / "out"
            pdf = pymupdf.open()
            page = pdf.new_page(width=360, height=220)
            page.insert_text((24, 40), "Cost Monitoring", fontsize=18)
            page.insert_text((24, 90), "Cost monitoring tracks query cost, storage cost, and latency.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            turn = session.answer("What cost does monitoring track?", limit=3)
            trace = {
                "status": "ok",
                "example": "pdf_chat_like",
                "source": str(pdf_path),
                "document_id": session.document.id,
                "page_count": session.document.page_count,
                "document_block_count": len(session.document.document_blocks),
                "turns": [turn],
            }
            paths = session.export_artifacts(out_dir, trace)

            self.assertEqual(trace["status"], "ok")
            self.assertGreaterEqual(trace["document_block_count"], 1)
            tool_calls = [event["name"] for event in turn["events"] if event["type"] == "tool_call"]
            self.assertEqual(tool_calls[0], "search_blocks")
            self.assertNotIn("list_blocks", tool_calls[:2])
            self.assertIn("read_block", tool_calls)
            tool_events = [event for event in turn["events"] if event["type"] in {"tool_call", "tool_result"}]
            self.assertTrue(tool_events)
            self.assertTrue(all(isinstance(event.get("token_count"), int) and event["token_count"] > 0 for event in tool_events))
            self.assertTrue(all("token_usage" in event for event in tool_events))
            self.assertTrue(all("cached_tokens" in event["token_usage"] for event in tool_events))
            self.assertTrue(all(event["token_usage"]["source"] in {"local_estimate", "response_api"} for event in tool_events))
            self.assertIn("cached_tokens", turn["token_usage"])
            self.assertIn("Cost", turn["answer"]["text"])
            self.assertIn("回答：", turn["answer"]["text"])
            self.assertIn("原文證據：", turn["answer"]["text"])
            self.assertIn("繁體中文", turn["system_prompt"])
            self.assertEqual(turn["answer"]["language"], "zh-Hant-with-source-quotes")
            self.assertIn("繁體中文", turn["answer"]["system_prompt"])
            self.assertTrue(Path(paths["trace"]).exists())
            saved = json.loads(Path(paths["trace"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["example"], "pdf_chat_like")

    def test_pdf_chat_like_example_reads_body_after_matched_heading(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "heading.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=420, height=260)
            page.insert_text((24, 40), "Top Takeaways", fontsize=18)
            page.insert_text((24, 90), "Rollback plans must be reviewed before each release.", fontsize=12)
            page.insert_text((24, 120), "Monitoring should be enabled before traffic is shifted.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            turn = session.answer("What are the top takeaways?", limit=3)
            rows = session.list_blocks(depth=None)["blocks"]
            body_rows = [row for row in rows if row["type"] == "paragraph"]

            self.assertIn("回答：", turn["answer"]["text"])
            self.assertIn("原文證據：", turn["answer"]["text"])
            self.assertIn("Rollback plans", turn["answer"]["text"])
            self.assertNotIn("Top Takeaways\n\n依據：\n- ", turn["answer"]["text"])
            self.assertTrue(body_rows)
            self.assertTrue(all(row["keywords"] for row in rows))
            self.assertTrue(any(row["title"] == "Top Takeaways" for row in body_rows))

    def test_chinese_overview_query_uses_deterministic_bilingual_terms(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_example_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "overview.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=420, height=260)
            page.insert_text((24, 40), "1 INTRODUCTION", fontsize=18)
            page.insert_text((24, 90), "This paper studies efficient routing for tool-based document reading.", fontsize=12)
            page.insert_text((24, 120), "The method reduces unnecessary context loading before synthesis.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            session = module.PdfBlockChatExample.load(pdf_path)
            result = session.search_blocks("這篇論文主要在講甚麼?", limit=5, verbosity="debug")

            self.assertLessEqual(len(result["terms"]), 12)
            self.assertEqual(result["query_plan"]["intent"], "overview")
            self.assertFalse(result["query_plan"]["requires_llm_terms"])
            self.assertEqual(result["query_plan"]["terms_source"], "deterministic_bilingual")
            self.assertIn("paper", result["terms"])
            self.assertIn("overview", result["terms"])
            self.assertTrue(result["results"])
            self.assertTrue(all(row["matches"] for row in result["results"]))
            self.assertTrue(any(row["snippets"] for row in result["results"]))
            self.assertTrue(any("structural_hints" in row["matches"] for row in result["results"]))

            turn = session.answer(
                "這篇論文主要在講甚麼?",
                limit=3,
                query_terms_override=["efficient routing", "document reading", "context loading"],
            )
            search_call = next(event for event in turn["events"] if event["type"] == "tool_call" and event["name"] == "search_blocks")
            self.assertEqual(search_call["payload"]["query_plan"]["terms_source"], "user_supplied")
            self.assertEqual(search_call["payload"]["query_plan"]["terms"][0], "efficient routing")


if __name__ == "__main__":
    unittest.main()
