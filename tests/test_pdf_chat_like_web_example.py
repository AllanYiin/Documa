import http.client
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "examples" / "pdf_chat_like_web" / "server.py"


def load_server_module():
    spec = importlib.util.spec_from_file_location("pdf_chat_web_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PdfChatLikeWebExampleTests(unittest.TestCase):
    def test_web_server_loads_pdf_and_answers(self):
        try:
            import pymupdf  # type: ignore
        except ImportError:
            try:
                import fitz as pymupdf  # type: ignore
            except ImportError:
                self.skipTest("PyMuPDF is required")

        module = load_server_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "web.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page(width=360, height=220)
            page.insert_text((24, 40), "Deployment Risk", fontsize=18)
            page.insert_text((24, 90), "Deployment risk includes rollback planning and monitoring.", fontsize=12)
            pdf.save(pdf_path)
            pdf.close()

            state = module.AppState(tmp_path / "state")
            server = ThreadingHTTPServer(("127.0.0.1", 0), module.create_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                conn.request("GET", "/")
                page_response = conn.getresponse()
                page_html = page_response.read().decode("utf-8")
                self.assertEqual(page_response.status, 200)
                self.assertIn('id="loadPdf"', page_html)
                self.assertIn('class="file-input"', page_html)
                self.assertIn('id="keywordStrip"', page_html)
                self.assertIn('id="dropzone"', page_html)

                conn.request(
                    "POST",
                    "/api/load?name=web.pdf",
                    body=pdf_path.read_bytes(),
                    headers={"Content-Type": "application/pdf"},
                )
                load_response = conn.getresponse()
                load_payload = json.loads(load_response.read().decode("utf-8"))
                self.assertEqual(load_response.status, 200)
                self.assertEqual(load_payload["status"], "ok")
                self.assertGreater(load_payload["document_token_usage"]["total_tokens"], 0)
                self.assertIn("繁體中文", load_payload["system_prompt"])
                self.assertIsInstance(load_payload["keyword_groups"], list)
                self.assertGreater(len(load_payload["keyword_groups"]), 0)
                self.assertNotIn("blocks", load_payload)

                conn.request(
                    "POST",
                    "/api/load?name=web.pdf&include_blocks=1",
                    body=pdf_path.read_bytes(),
                    headers={"Content-Type": "application/pdf"},
                )
                compat_response = conn.getresponse()
                compat_payload = json.loads(compat_response.read().decode("utf-8"))
                self.assertEqual(compat_response.status, 200)
                self.assertTrue(all(row["keywords"] for row in compat_payload["blocks"]["blocks"]))

                conn.request(
                    "POST",
                    "/api/ask",
                    body=json.dumps(
                        {
                            "session_id": load_payload["session_id"],
                            "question": "What deployment risk is mentioned?",
                            "query_terms": ["deployment risk", "rollback planning"],
                            "use_llm": False,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                ask_response = conn.getresponse()
                ask_payload = json.loads(ask_response.read().decode("utf-8"))
                self.assertEqual(ask_response.status, 200)
                self.assertEqual(ask_payload["status"], "ok")
                tool_calls = [event["name"] for event in ask_payload["turn"]["events"] if event["type"] == "tool_call"]
                self.assertEqual(tool_calls[0], "search_blocks")
                self.assertNotIn("list_blocks", tool_calls[:2])
                self.assertIn("read_block", tool_calls)
                tool_events = [event for event in ask_payload["turn"]["events"] if event["type"] in {"tool_call", "tool_result"}]
                self.assertTrue(tool_events)
                self.assertTrue(all(isinstance(event.get("token_count"), int) and event["token_count"] > 0 for event in tool_events))
                self.assertTrue(all("cached_tokens" in event["token_usage"] for event in tool_events))
                self.assertIn("cached_tokens", ask_payload["turn"]["token_usage"])
                search_call = next(event for event in ask_payload["turn"]["events"] if event["type"] == "tool_call" and event["name"] == "search_blocks")
                self.assertEqual(search_call["payload"]["query_plan"]["terms_source"], "user_supplied")
                self.assertIn("繁體中文", ask_payload["turn"]["system_prompt"])
                self.assertEqual(ask_payload["turn"]["answer"]["language"], "zh-Hant-with-source-quotes")
                self.assertIn("Deployment", ask_payload["turn"]["answer"]["text"])
                self.assertIn("回答：", ask_payload["turn"]["answer"]["text"])
                self.assertIn("原文證據：", ask_payload["turn"]["answer"]["text"])
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
