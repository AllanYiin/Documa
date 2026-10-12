"""Remote acquisition contracts, real HTTP streaming and existing parser flow."""

import asyncio
import hashlib
import io
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from documa import sources
from documa.cli import main
from documa.core.errors import DocumaError
from documa.interfaces.mcp_server import create_mcp_server
from documa.interfaces.tool_schemas import documa_tool_schemas
from documa.interfaces.tools import call_documa_tool, parse_document_tool, process_document_tool


@pytest.fixture
def pdf_bytes():
    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 250), "Remote PDF evidence for Documa.\nThe document includes readable source evidence.")
        return document.tobytes()


@pytest.fixture
def http_pdf(monkeypatch, pdf_bytes):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/download?signature=secret")
                self.end_headers()
                return
            if self.path == "/private-redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1/private")
                self.end_headers()
                return
            if self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.end_headers()
                return
            payload = b"<html>Sign in</html>" if self.path == "/html" else pdf_bytes
            self.send_response(403 if self.path == "/denied" else 200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = sources._public_addresses

    def test_addresses(host, port):
        if host == "public.test":
            return socket.getaddrinfo("127.0.0.1", port, type=socket.SOCK_STREAM)
        return original(host, port)

    monkeypatch.setattr(sources, "_public_addresses", test_addresses)
    try:
        yield f"http://public.test:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_parse_without_extension_preserves_source_and_hash(http_pdf, pdf_bytes):
    base, _ = http_pdf
    payload = parse_document_tool(base + "/redirect", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert payload["page_count"] == 1
    record = payload["source_access"]
    assert record["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert record["byte_count"] == len(pdf_bytes)
    assert record["source_url"] == base + "/redirect"
    assert record["resolved_url"] == base + "/download"
    assert payload["document"]["source_name"] == base + "/redirect"
    assert "secret" not in json.dumps(payload)
    assert "Remote PDF evidence" in json.dumps(payload)


def test_real_http_process_exports_and_reads_existing_ir(http_pdf, tmp_path):
    base, _ = http_pdf
    out = tmp_path / "out"
    payload = process_document_tool(base + "/download", out=str(out),
                                    pdf_provider="pymupdf", keyword_provider="ngram",
                                    export_formats=["markdown", "rag-json"])
    assert payload["status"] == "ok"
    assert payload["chunk_count"] > 0
    document = json.loads((out / "documa.ir.json").read_text(encoding="utf-8"))
    assert document["metadata"]["source_access"] == payload["source_access"]
    assert "Remote PDF evidence" in (out / "documa.md").read_text(encoding="utf-8")
    result = call_documa_tool("documa_inspect", {"ir_path": payload["output_path"]})
    assert not result["isError"]
    assert result["structuredContent"]["source_name"] == base + "/download"


@pytest.mark.parametrize("route,code", [
    ("/html", "REMOTE_NOT_PDF"), ("/denied", "REMOTE_AUTH_REQUIRED"),
    ("/private-redirect", "REMOTE_SOURCE_BLOCKED"), ("/loop", "REMOTE_REDIRECT_FAILED"),
])
def test_http_failure_never_writes_ir(http_pdf, tmp_path, route, code):
    base, _ = http_pdf
    out = tmp_path / "failed"
    payload = process_document_tool(base + route, out=str(out))
    assert payload["status"] == "error"
    assert payload["error"]["code"] == code
    assert not (out / "documa.ir.json").exists()


def test_temp_file_lives_through_context_and_is_removed(http_pdf):
    base, _ = http_pdf
    with sources.materialize_source(base + "/download") as resolved:
        path = Path(resolved.path)
        assert path.is_file()
        assert path.suffix == ".pdf"
    assert not path.exists()
    assert not path.parent.exists()


def test_cleanup_on_caller_failure(http_pdf):
    base, _ = http_pdf
    with pytest.raises(RuntimeError):
        with sources.materialize_source(base + "/download") as resolved:
            path = Path(resolved.path)
            raise RuntimeError("parser/pipeline failure")
    assert not path.parent.exists()


def test_output_io_error_is_not_mislabeled_as_download_failure(http_pdf):
    base, _ = http_pdf
    with pytest.raises(OSError, match="output disk full"):
        with sources.materialize_source(base + "/download") as resolved:
            path = Path(resolved.path)
            raise OSError("output disk full")
    assert not path.parent.exists()


def test_connector_download_url_retains_drive_origin_without_using_oauth(fake_transport, pdf_bytes, monkeypatch):
    responses, connections = fake_transport
    monkeypatch.setenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "secret-oauth")
    responses.append(FakeResponse(pdf_bytes))
    payload = parse_document_tool("https://connector.test/download?signature=secret", source_url="https://drive.google.com/file/d/abc/view", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert payload["source_access"]["access_method"] == "connector_download"
    assert payload["document"]["source_name"] == "https://drive.google.com/file/d/abc/view"
    assert connections[0].host == "connector.test"
    assert connections[0].target == "/download?signature=secret"
    assert "Authorization" not in connections[0].headers
    assert "secret" not in json.dumps(payload)


def test_remote_pdf_remains_available_to_ocr_pipeline(http_pdf, monkeypatch):
    from documa.pipeline.base import StageResult
    from documa.pipeline.ocr import OcrStage

    paths = []

    def verify_ocr(self, document, context=None):
        assert context.settings["ocr"] is True
        path = Path(context.settings["source_path"])
        paths.append(path)
        assert path.is_file() and path.suffix == ".pdf"
        assert document.metadata["pdf_provider"]["reason_code"] == "OCR_REQUIRES_PYMUPDF_RENDERER"
        return StageResult(document, stage_name="ocr")

    monkeypatch.setattr(OcrStage, "run", verify_ocr)
    base, _ = http_pdf
    payload = process_document_tool(base + "/download", ocr=True, keyword_provider="ngram")
    assert payload["status"] == "ok"
    assert len(paths) == 1
    assert not paths[0].exists()


def test_materialized_drive_file_without_extension(tmp_path, pdf_bytes):
    file = tmp_path / "connector-output"
    file.write_bytes(pdf_bytes)
    payload = process_document_tool(str(file), source_url="https://drive.google.com/open?id=abc_123&resourcekey=key",
                                    pdf_provider="pymupdf", keyword_provider="ngram")
    assert payload["status"] == "ok"
    record = payload["source_access"]
    assert record["drive_file_id"] == "abc_123"
    assert record["access_method"] == "connector_materialized"
    assert record["source_url"] == "https://drive.google.com/file/d/abc_123/view"
    assert record["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    assert file.read_bytes() == pdf_bytes
    assert "resourcekey" not in json.dumps(payload)


def test_local_sources_keep_existing_contract(tmp_path):
    path = tmp_path / "local.md"
    path.write_text("# Local\nExisting document", encoding="utf-8")
    payload = parse_document_tool(str(path))
    assert payload["status"] == "ok"
    assert payload["document"]["source_name"] == str(path)
    assert "source_access" not in payload
    assert parse_document_tool(path)["status"] == "ok"


@pytest.mark.parametrize("source", ["file://abc", "data:application/pdf;base64,abc", "blob:abc",
                                    "attachment://file-id", "openai://file-id", "sandbox:/mnt/data/a.pdf"])
def test_opaque_connector_references_require_host_resolution(source):
    payload = parse_document_tool(source)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "SOURCE_REFERENCE_UNRESOLVED"
    assert "workspace_path" in payload["error"]["suggested_action"]


def test_unresolved_reference_with_source_url_is_not_local_path():
    payload = parse_document_tool("openai://file-id", source_url="https://drive.google.com/file/d/abc/view")
    assert payload["error"]["code"] == "SOURCE_REFERENCE_UNRESOLVED"


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/a", "http://[::1]/a",
                                 "http://169.254.169.254/a", "http://10.0.0.1/a"])
def test_private_addresses_blocked(url):
    payload = parse_document_tool(url)
    assert payload["error"]["code"] == "REMOTE_SOURCE_BLOCKED"


@pytest.mark.parametrize("source,code", [
    ("https://user:password@public.test/pdf", "REMOTE_SOURCE_INVALID"),
    ("https://public.test:bad/pdf", "REMOTE_SOURCE_INVALID"),
    ("https://[broken/pdf", "REMOTE_SOURCE_INVALID"),
    ("https://drive.google.com/drive/folders/abc", "DRIVE_SOURCE_INVALID"),
    ("gdrive://", "DRIVE_SOURCE_INVALID"),
])
def test_malformed_sources_have_structured_errors(source, code):
    payload = parse_document_tool(source)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == code
    assert "password" not in json.dumps(payload)


class FakeResponse(io.BytesIO):
    def __init__(self, payload=b"", status=200, headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}

    def getheader(self, key, default=None):
        return self.headers.get(key.lower(), default)


@pytest.fixture
def fake_transport(monkeypatch):
    responses = []
    connections = []

    class Connection:
        def __init__(self, host, port, **kwargs):
            self.host = host
            self.port = port
            self.kwargs = kwargs
            self.sock = None
            self.closed = False
            connections.append(self)

        def request(self, method, target, headers):
            self.target = target
            self.headers = headers

        def getresponse(self):
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def close(self):
            self.closed = True

    monkeypatch.setattr(sources, "_public_addresses", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))])
    monkeypatch.setattr(sources.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(sources.http.client, "HTTPSConnection", Connection)
    monkeypatch.delenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", raising=False)
    return responses, connections


def test_drive_public_and_resource_key(fake_transport, pdf_bytes):
    responses, connections = fake_transport
    responses.append(FakeResponse(pdf_bytes))
    payload = parse_document_tool("https://drive.google.com/file/d/abc/view?resourcekey=key", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert connections[0].host == "drive.google.com"
    assert "id=abc" in connections[0].target
    assert "resourcekey=key" in connections[0].target
    assert "Authorization" not in connections[0].headers
    assert "key" not in json.dumps(payload["source_access"])


@pytest.mark.parametrize("host,path", [("drive.usercontent.google.com", "/download"), ("drive.google.com", "/uc")])
def test_drive_direct_download_keeps_confirmation_and_signature(fake_transport, pdf_bytes, monkeypatch, host, path):
    responses, connections = fake_transport
    responses.append(FakeResponse(pdf_bytes))
    monkeypatch.setenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "secret-oauth")
    query = "id=abc&export=download&confirm=t&at=secret-signature"
    payload = parse_document_tool(f"https://{host}{path}?{query}", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert connections[0].host == host
    assert connections[0].target == f"{path}?{query}"
    assert "Authorization" not in connections[0].headers
    assert payload["source_access"]["source_url"] == "https://drive.google.com/file/d/abc/view"
    assert "secret" not in json.dumps(payload)


def test_drive_oauth_metadata_and_media(fake_transport, pdf_bytes, monkeypatch):
    responses, connections = fake_transport
    monkeypatch.setenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "secret-oauth")
    responses.extend([FakeResponse(json.dumps({"name": "研究.pdf", "mimeType": "application/pdf", "capabilities": {"canDownload": True}}).encode()), FakeResponse(pdf_bytes)])
    payload = parse_document_tool("gdrive://abc?resourcekey=key", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert payload["source_access"]["filename"] == "研究.pdf"
    assert len(connections) == 2
    assert all(connection.headers["Authorization"] == "Bearer secret-oauth" for connection in connections)
    assert connections[0].headers["X-Goog-Drive-Resource-Keys"] == "abc/key"
    assert "alt=media" in connections[1].target
    assert all("supportsAllDrives=true" in connection.target for connection in connections)
    assert "secret-oauth" not in json.dumps(payload)


def test_invalid_drive_token_never_echoed(fake_transport, monkeypatch):
    _, connections = fake_transport
    monkeypatch.setenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "secret\r\nInjected: header")
    payload = parse_document_tool("gdrive://abc")
    assert payload["error"]["code"] == "DRIVE_AUTH_INVALID"
    assert "secret" not in json.dumps(payload)
    assert connections == []


def test_unicode_pdf_download_url(fake_transport, pdf_bytes):
    responses, connections = fake_transport
    responses.append(FakeResponse(pdf_bytes))
    payload = parse_document_tool("https://public.test/研究.pdf?title=資料", pdf_provider="pymupdf")
    assert payload["status"] == "ok"
    assert connections[0].target == "/%E7%A0%94%E7%A9%B6.pdf?title=%E8%B3%87%E6%96%99"
    assert payload["document"]["source_name"] == "https://public.test/研究.pdf"


@pytest.mark.parametrize("metadata,code", [
    ({"mimeType": "text/plain", "capabilities": {"canDownload": True}}, "DRIVE_NOT_PDF"),
    ({"mimeType": "application/pdf", "capabilities": {"canDownload": False}}, "DRIVE_DOWNLOAD_FORBIDDEN"),
    ([], "DRIVE_METADATA_INVALID"),
])
def test_drive_metadata_rejects_before_media(fake_transport, monkeypatch, metadata, code):
    responses, connections = fake_transport
    monkeypatch.setenv("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "secret-oauth")
    responses.append(FakeResponse(json.dumps(metadata).encode()))
    payload = parse_document_tool("gdrive://abc")
    assert payload["error"]["code"] == code
    assert len(connections) == 1


def test_cross_origin_redirect_drops_oauth(fake_transport):
    responses, connections = fake_transport
    responses.extend([FakeResponse(status=302, headers={"Location": "https://cdn.test/a.pdf"}), FakeResponse(b"%PDF-1.7\n")])
    with sources._response("https://www.googleapis.com/a", {"Authorization": "Bearer secret", "X-Goog-Drive-Resource-Keys": "abc/key"}, sources.time.monotonic() + 10):
        assert connections[0].headers["Authorization"] == "Bearer secret"
        assert "Authorization" not in connections[1].headers
        assert "X-Goog-Drive-Resource-Keys" not in connections[1].headers
    assert all(connection.closed for connection in connections)


def test_https_downgrade_rejected(fake_transport):
    responses, connections = fake_transport
    responses.append(FakeResponse(status=302, headers={"Location": "http://public.test/a.pdf"}))
    payload = parse_document_tool("https://public.test/a.pdf")
    assert payload["error"]["code"] == "REMOTE_REDIRECT_BLOCKED"
    assert len(connections) == 1


@pytest.mark.parametrize("response,code", [
    (FakeResponse(b"%PDF-1.7\n", headers={"Content-Length": "100"}), "REMOTE_DOWNLOAD_INCOMPLETE"),
    (FakeResponse(b"%PDF-1.7\n", headers={"Content-Length": "bad"}), "REMOTE_RESPONSE_INVALID"),
    (FakeResponse(b"%PDF-1.7\n", headers={"Content-Length": str(sources.MAX_PDF_BYTES + 1)}), "REMOTE_PDF_TOO_LARGE"),
    (FakeResponse(b"%PDF-1.7\n", headers={"Content-Encoding": "gzip"}), "REMOTE_RESPONSE_INVALID"),
    (FakeResponse(b"%PDF-1.7\n", headers={"Content-Range": "bytes 0-8/200"}), "REMOTE_DOWNLOAD_INCOMPLETE"),
    (FakeResponse(status=206), "REMOTE_HTTP_ERROR"),
    (ssl.SSLCertVerificationError("secret url"), "REMOTE_TLS_FAILED"),
    (TimeoutError("secret url"), "REMOTE_DOWNLOAD_TIMEOUT"),
])
def test_download_failure_codes_and_no_secret_echo(fake_transport, response, code, tmp_path):
    responses, _ = fake_transport
    responses.append(response)
    payload = parse_document_tool("https://public.test/download?token=secret", out=str(tmp_path / "out"))
    assert payload["error"]["code"] == code
    assert "secret" not in json.dumps(payload)
    assert not (tmp_path / "out" / "documa.ir.json").exists()


def test_streaming_limit_without_content_length(fake_transport, monkeypatch):
    responses, _ = fake_transport
    monkeypatch.setattr(sources, "MAX_PDF_BYTES", 12)
    responses.append(FakeResponse(b"%PDF-1.7\n" + b"x" * 10))
    payload = parse_document_tool("https://public.test/download")
    assert payload["error"]["code"] == "REMOTE_PDF_TOO_LARGE"


def test_total_download_deadline(fake_transport, monkeypatch):
    responses, _ = fake_transport
    responses.append(FakeResponse(b"%PDF-1.7\n"))
    monkeypatch.setattr(sources, "DOWNLOAD_TIMEOUT", -1)
    payload = parse_document_tool("https://public.test/download")
    assert payload["error"]["code"] == "REMOTE_DOWNLOAD_TIMEOUT"


def test_invalid_pdf_after_download_never_creates_success_ir(fake_transport, tmp_path):
    responses, _ = fake_transport
    responses.append(FakeResponse(b"%PDF-1.7\ninvalid/truncated structure"))
    payload = parse_document_tool("https://public.test/download", out=str(tmp_path / "out"), pdf_provider="pymupdf")
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "PDF_OPEN_FAILED"
    assert not (tmp_path / "out" / "documa.ir.json").exists()


def test_mixed_public_and_private_dns_answers_blocked(monkeypatch):
    monkeypatch.setattr(sources.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(DocumaError) as error:
        sources._public_addresses("rebinding.test", 443)
    assert error.value.detail.code == "REMOTE_SOURCE_BLOCKED"


def test_canonical_source_url_rejects_credentials(tmp_path, pdf_bytes):
    path = tmp_path / "file"
    path.write_bytes(pdf_bytes)
    payload = parse_document_tool(str(path), source_url="https://user:secret@drive.google.com/file/d/abc/view")
    assert payload["error"]["code"] == "REMOTE_SOURCE_INVALID"
    assert "secret" not in json.dumps(payload)


def test_dns_pin_keeps_tls_hostname(fake_transport, monkeypatch):
    responses, connections = fake_transport
    responses.append(FakeResponse(b"%PDF-1.7\n"))
    connected = []

    class Socket:
        def settimeout(self, timeout):
            pass

        def connect(self, address):
            connected.append(address)

    monkeypatch.setattr(sources.socket, "socket", lambda *args: Socket())
    with sources._response("https://public.test/a.pdf", {}, sources.time.monotonic() + 10):
        connections[0]._create_connection(("public.test", 443))
        assert connected == [("93.184.216.34", 443)]
        assert connections[0].host == "public.test"
        context = connections[0].kwargs["context"]
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED


def test_cli_materialized_pdf_and_error_exit(tmp_path, pdf_bytes, capsys):
    file = tmp_path / "download"
    file.write_bytes(pdf_bytes)
    code = main(["parse", str(file), "--source-url", "https://drive.google.com/file/d/abc/view", "--pdf-provider", "pymupdf"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["source_access"]["drive_file_id"] == "abc"
    code = main(["process", "openai://file-id"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_tool_and_mcp_schemas_share_optional_source_url():
    descriptors = {tool["name"]: tool for tool in documa_tool_schemas()}
    tools = asyncio.run(create_mcp_server().list_tools())
    for name in ("documa_parse", "documa_process"):
        assert descriptors[name]["inputSchema"]["required"] == ["source"]
        assert "source_url" in descriptors[name]["inputSchema"]["properties"]
        tool = next(item for item in tools if item.name == name)
        schema = getattr(tool, "inputSchema", None) or tool.input_schema
        assert schema["required"] == ["source"]
        assert "source_url" in schema["properties"]
        if name == "documa_process":
            assert "ocr" in schema["properties"]
            assert schema["properties"]["ocr"]["default"] is False


def test_image_only_remote_pdf_reports_ocr_required(fake_transport, monkeypatch):
    from documa.core.ir import DocumentIR, PageIR
    import documa.interfaces.tools as tool_module

    responses, _ = fake_transport
    responses.append(FakeResponse(b"%PDF-1.7\n"))

    class ImageOnlyAdapter:
        def parse(self, source, options=None):
            return DocumentIR(id="scanned", source_name=source, parser="pymupdf",
                              pages=[PageIR(id="p", page_number=1, width=600, height=800)])

    monkeypatch.setattr(tool_module, "adapter_for_source", lambda *args, **kwargs: ImageOnlyAdapter())
    payload = process_document_tool("https://public.test/scanned.pdf", keyword_provider="ngram")
    assert payload["status"] == "ok"
    assert payload["reading_status"] == "ocr_required"
    assert "ocr=true" in payload["suggested_action"]
    assert any("PDF_NO_READABLE_TEXT" in warning for warning in payload["warnings"])


def test_remote_figure_markdown_links_canonical_pdf_instead_of_temporary_path():
    from documa.core.ir import BlockIR, BlockType, DocumentIR, PageIR, TextContent
    from documa.exporters import MarkdownExporter

    url = "https://drive.google.com/file/d/abc/view"
    caption = BlockIR(id="cap", type=BlockType.TEXT, page_number=1,
                      text=TextContent("Figure 1: Caption"), metadata={"figure_group_id": "cap"})
    document = DocumentIR(id="d", source_name=url, parser="pymupdf",
                          pages=[PageIR(id="p", page_number=1, width=600, height=800, blocks=[caption])],
                          metadata={"source_access": {"source_url": url}})
    markdown = MarkdownExporter().export(document)
    assert f"]({url}#page=1)" in markdown
    assert "https:/drive.google.com" not in markdown
