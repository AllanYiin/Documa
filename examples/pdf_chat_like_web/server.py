"""Local web server for the Documa PDF chat example."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
EXAMPLE_ROOT = Path(__file__).resolve().parent
PUBLIC_ROOT = EXAMPLE_ROOT / "public"
PDF_CHAT_EXAMPLE_ROOT = REPO_ROOT / "examples" / "pdf_chat_like"
if SRC_ROOT.exists() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if PDF_CHAT_EXAMPLE_ROOT.exists() and str(PDF_CHAT_EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(PDF_CHAT_EXAMPLE_ROOT))

from pdf_chat_example import OpenAIResponsesClient, PdfBlockChatExample, SYSTEM_PROMPT_ZH_HANT  # noqa: E402


class AppState:
    def __init__(self, work_dir: Path | None = None):
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="documa_pdf_chat_"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, PdfBlockChatExample] = {}
        self.session_files: dict[str, Path] = {}
        self.llm_available = OpenAIResponsesClient.available()

    def load_pdf(self, data: bytes, filename: str, *, include_blocks: bool = False) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        safe_name = Path(unquote(filename or "document.pdf")).name or "document.pdf"
        pdf_path = self.work_dir / f"{session_id}_{safe_name}"
        pdf_path.write_bytes(data)
        session = PdfBlockChatExample.load(pdf_path, asset_dir=self.work_dir / f"{session_id}_assets")
        self.sessions[session_id] = session
        self.session_files[session_id] = pdf_path
        payload = {
            "status": "ok",
            "session_id": session_id,
            "filename": safe_name,
            "document_id": session.document.id,
            "page_count": session.document.page_count,
            "document_block_count": len(session.document.document_blocks),
            "document_token_usage": session.document_token_usage(),
            "system_prompt": SYSTEM_PROMPT_ZH_HANT,
            "keyword_groups": session.keyword_groups(limit=24),
        }
        if include_blocks:
            payload["blocks"] = session.list_blocks(depth=2)
        return payload

    def ask(
        self,
        session_id: str,
        question: str,
        query_terms: list[str] | None = None,
        use_llm: bool | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            return {"status": "error", "message": "Unknown session_id"}
        if not question.strip():
            return {"status": "error", "message": "Question is required"}
        turn = session.answer(
            question.strip(),
            limit=5,
            max_chars_per_block=2200,
            query_terms_override=query_terms,
            use_llm=self.llm_available if use_llm is None else bool(use_llm),
        )
        return {"status": "ok", "session_id": session_id, "turn": turn}


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def create_handler(state: AppState):
    class PdfChatHandler(BaseHTTPRequestHandler):
        server_version = "DocumaPdfChatExample/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                self.send_json(
                    {
                        "status": "ok",
                        "service": "documa_pdf_chat_web",
                        "sessions": len(state.sessions),
                        "llm_available": state.llm_available,
                    }
                )
                return
            if parsed.path == "/":
                self.serve_static(PUBLIC_ROOT / "index.html")
                return
            target = (PUBLIC_ROOT / parsed.path.lstrip("/")).resolve()
            if not str(target).startswith(str(PUBLIC_ROOT.resolve())):
                self.send_json({"status": "error", "message": "Invalid path"}, status=403)
                return
            self.serve_static(target)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/load":
                length = int(self.headers.get("Content-Length", "0") or "0")
                data = self.rfile.read(length)
                query = parse_qs(parsed.query)
                filename = query.get("name", ["document.pdf"])[0]
                include_blocks = query.get("include_blocks", ["0"])[0].lower() in {"1", "true", "yes"}
                try:
                    payload = state.load_pdf(data, filename, include_blocks=include_blocks)
                except Exception as exc:  # noqa: BLE001
                    self.send_json({"status": "error", "message": str(exc)}, status=500)
                    return
                self.send_json(payload)
                return
            if parsed.path == "/api/ask":
                payload = self.read_json()
                try:
                    raw_terms = payload.get("query_terms")
                    query_terms = [str(term) for term in raw_terms] if isinstance(raw_terms, list) else None
                    raw_use_llm = payload.get("use_llm")
                    use_llm = bool(raw_use_llm) if isinstance(raw_use_llm, bool) else None
                    result = state.ask(str(payload.get("session_id", "")), str(payload.get("question", "")), query_terms, use_llm)
                except Exception as exc:  # noqa: BLE001
                    result = {"status": "error", "message": str(exc)}
                self.send_json(result, status=200 if result.get("status") == "ok" else 400)
                return
            self.send_json({"status": "error", "message": "Not found"}, status=404)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def serve_static(self, path: Path) -> None:
            if not path.exists() or not path.is_file():
                self.send_json({"status": "error", "message": "Not found"}, status=404)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, payload: Any, *, status: int = 200) -> None:
            data = json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    return PdfChatHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Documa PDF chat web example.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = AppState()
    server = ThreadingHTTPServer((args.host, args.port), create_handler(state))
    print(f"Documa PDF chat example running at http://{args.host}:{server.server_port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
