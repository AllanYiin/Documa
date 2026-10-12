"""Materialize remote PDFs before handing them to the existing local adapters.

OAuth belongs to the caller/connector. OAuth headers and download query strings
are never written to IR; downloads live only for the duration of processing.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

from documa.core.errors import DocumaError, DocumaErrorDetail

MAX_PDF_BYTES = 100 * 1024 * 1024
REQUEST_TIMEOUT = 30.0
DOWNLOAD_TIMEOUT = 120.0
MAX_REDIRECTS = 5
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _error(code: str, message: str, action: str | None = None, **context: Any) -> DocumaError:
    return DocumaError(DocumaErrorDetail(
        code=code, message=message, recoverable=True,
        suggested_action=action, context=context or None,
    ))


def _url_parts(url: str):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password or any(ord(c) < 32 for c in url)):
            raise ValueError
        _ = parts.port
        return parts
    except ValueError:
        raise _error("REMOTE_SOURCE_INVALID", "Expected an HTTP(S) PDF URL without embedded credentials.") from None


def _safe_url(url: str) -> str:
    parts = _url_parts(url)
    # Query strings may contain bearer/signature credentials. Drive provenance
    # is canonicalized separately so its file id is retained.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _drive_ref(source: str) -> tuple[str, str | None] | None:
    try:
        parts = urlsplit(source)
    except ValueError:
        raise _error("REMOTE_SOURCE_INVALID", "Malformed source URL.") from None
    file_id = None
    query = parse_qs(parts.query)
    if parts.scheme in {"gdrive", "google-drive"}:
        file_id = parts.netloc or parts.path.strip("/")
    elif parts.hostname in {"drive.google.com", "drive.usercontent.google.com"}:
        _url_parts(source)
        match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]+)(?:/[^/]*)?/?", parts.path)
        if match:
            file_id = match.group(1)
        elif parts.path in {"/open", "/uc", "/download"}:
            file_id = query.get("id", [None])[0]
        if not file_id:
            raise _error("DRIVE_SOURCE_INVALID", "Expected a Google Drive file link, not a folder or Drive home URL.")
    if file_id is None:
        return None
    if not _DRIVE_ID.fullmatch(file_id):
        raise _error("DRIVE_SOURCE_INVALID", "Invalid Google Drive file id.")
    resource_key = query.get("resourcekey", [None])[0]
    if resource_key and not _DRIVE_ID.fullmatch(resource_key):
        raise _error("DRIVE_SOURCE_INVALID", "Invalid Google Drive resource key.")
    return file_id, resource_key


def _origin(url: str) -> tuple[str, str | None, int]:
    parts = _url_parts(url)
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def _public_addresses(host: str, port: int) -> list[tuple]:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise _error("REMOTE_SOURCE_BLOCKED", "Remote PDF URLs must resolve to public network addresses.")
    return addresses


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _error("REMOTE_DOWNLOAD_TIMEOUT", "PDF download exceeded its time limit.")
    return min(REQUEST_TIMEOUT, remaining)


@contextmanager
def _response(url: str, headers: dict[str, str], deadline: float):
    """Validate every redirect and pin the socket to the validated DNS answers."""
    initial_origin = _origin(url)
    for redirect in range(MAX_REDIRECTS + 1):
        parts = _url_parts(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = _public_addresses(parts.hostname, port)
        timeout = _remaining(deadline)
        conn = (http.client.HTTPSConnection(parts.hostname, port, timeout=timeout, context=ssl.create_default_context())
                if parts.scheme == "https" else http.client.HTTPConnection(parts.hostname, port, timeout=timeout))

        def connect(address, timeout=timeout, source_address=None):
            # HTTPConnection/HTTPSConnection use this hook; TLS still checks the
            # original hostname, but DNS is not consulted a second time.
            last_error = None
            for family, socktype, proto, _, sockaddr in addresses:
                sock = socket.socket(family, socktype, proto)
                try:
                    sock.settimeout(_remaining(deadline))
                    sock.connect(sockaddr)
                    return sock
                except OSError as exc:
                    last_error = exc
                    sock.close()
            raise last_error or OSError("No usable address")

        conn._create_connection = connect
        response = None
        try:
            target = urlunsplit(("", "", quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                                quote(parts.query, safe="=&%/:?@!$'()*+,;~-._"), ""))
            request_headers = {"User-Agent": "Documa/remote-pdf", "Accept-Encoding": "identity"}
            if _origin(url) == initial_origin:
                request_headers.update(headers)
            conn.request("GET", target, headers=request_headers)
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect == MAX_REDIRECTS:
                    raise _error("REMOTE_REDIRECT_FAILED", "PDF download has an invalid or excessive redirect chain.")
                next_url = urljoin(url, location)
                if parts.scheme == "https" and _url_parts(next_url).scheme != "https":
                    raise _error("REMOTE_REDIRECT_BLOCKED", "HTTPS PDF download cannot redirect to HTTP.")
                url = next_url
                continue
            if response.status != 200:
                code = "REMOTE_AUTH_REQUIRED" if response.status in {401, 403} else "REMOTE_HTTP_ERROR"
                raise _error(code, "PDF server rejected the download.",
                             "For private Drive PDFs, use an authorized connector workspace_path or configure DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN.",
                             http_status=response.status)
            if response.getheader("Content-Range"):
                raise _error("REMOTE_DOWNLOAD_INCOMPLETE", "PDF server returned only a partial byte range.")
            yield response, _safe_url(url), conn
            return
        finally:
            if response is not None:
                response.close()
            conn.close()


def _copy_pdf(reader, destination: Path, *, connection=None, deadline: float | None = None,
              content_length: str | None = None) -> tuple[int, str]:
    expected = None
    if content_length is not None:
        try:
            expected = int(content_length)
            if expected < 0:
                raise ValueError
        except ValueError:
            raise _error("REMOTE_RESPONSE_INVALID", "Invalid PDF Content-Length.") from None
        if expected > MAX_PDF_BYTES:
            raise _error("REMOTE_PDF_TOO_LARGE", "PDF exceeds the 100 MiB download limit.")
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    with destination.open("wb") as output:
        while True:
            if deadline is not None:
                _set_read_timeout(reader, connection, deadline)
            read = getattr(reader, "read1", reader.read)
            chunk = read(min(64 * 1024, MAX_PDF_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                raise _error("REMOTE_PDF_TOO_LARGE", "PDF exceeds the 100 MiB download limit.")
            if len(prefix) < 1024:
                prefix = (prefix + chunk)[:1024]
            digest.update(chunk)
            output.write(chunk)
    if expected is not None and size != expected:
        raise _error("REMOTE_DOWNLOAD_INCOMPLETE", "PDF download is incomplete.")
    # MIME types and filename extensions alone cannot distinguish login/consent
    # HTML from a PDF. The parser performs the full structural validation.
    if not re.search(rb"%PDF-\d\.\d", prefix):
        raise _error("REMOTE_NOT_PDF", "Downloaded content is not a PDF (it may be a login or confirmation page).",
                     "Use the raw PDF download or an authenticated connector workspace_path; do not disable TLS verification.")
    return size, digest.hexdigest()


def _set_read_timeout(response, connection, deadline: float) -> None:
    timeout = _remaining(deadline)
    sock = connection.sock
    if sock is None:
        # HTTPConnection detaches a Connection: close socket after headers;
        # the response's socket-backed file still owns the live descriptor.
        raw = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(timeout)


def _drive_download(ref: tuple[str, str | None], deadline: float) -> tuple[str, dict[str, str], str | None]:
    file_id, resource_key = ref
    token = os.environ.get("DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN", "").strip()
    if not token:
        query = {"id": file_id, "export": "download"}
        if resource_key:
            query["resourcekey"] = resource_key
        return "https://drive.google.com/uc?" + urlencode(query), {}, None
    if any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise _error("DRIVE_AUTH_INVALID", "Configured Drive OAuth token contains invalid characters.",
                     "Configure a valid DOCUMA_GOOGLE_DRIVE_ACCESS_TOKEN in the local process environment.")
    headers = {"Authorization": f"Bearer {token}"}
    if resource_key:
        headers["X-Goog-Drive-Resource-Keys"] = f"{file_id}/{resource_key}"
    endpoint = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    query = urlencode({"fields": "name,mimeType,capabilities(canDownload)", "supportsAllDrives": "true"})
    with _response(endpoint + "?" + query, headers, deadline) as (response, _, connection):
        chunks = []
        size = 0
        while size <= 64 * 1024:
            _set_read_timeout(response, connection, deadline)
            chunk = response.read1(64 * 1024 + 1 - size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b"".join(chunks)
    if len(raw) > 64 * 1024:
        raise _error("DRIVE_METADATA_INVALID", "Drive metadata exceeds its size limit.")
    try:
        metadata = json.loads(raw)
        if not isinstance(metadata, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        raise _error("DRIVE_METADATA_INVALID", "Drive returned invalid file metadata.") from None
    if metadata.get("mimeType") != "application/pdf":
        raise _error("DRIVE_NOT_PDF", "The selected Drive file is not a stored PDF.")
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities.get("canDownload", False):
        raise _error("DRIVE_DOWNLOAD_FORBIDDEN", "Drive permissions do not allow downloading this PDF.")
    return endpoint + "?alt=media&supportsAllDrives=true", headers, metadata.get("name")


@dataclass
class MaterializedSource:
    path: str
    provenance: dict[str, Any] | None = None

    def stamp(self, document) -> None:
        if self.provenance is not None:
            document.source_name = self.provenance["source_url"]
            document.metadata["source_access"] = dict(self.provenance)


@contextmanager
def materialize_source(source: str, source_url: str | None = None) -> Iterator[MaterializedSource]:
    """Accept local files, HTTP(S) PDF downloads, Drive links and connector paths.

    ``source_url`` attaches canonical provenance to a connector-materialized PDF
    or a temporary HTTP(S) download; opaque references need host resolution.
    """
    if isinstance(source, os.PathLike):
        source = os.fspath(source)
    if not isinstance(source, str) or not source:
        raise _error("REMOTE_SOURCE_INVALID", "source must be a non-empty path or PDF URL.")
    if source_url is not None and (not isinstance(source_url, str) or not source_url):
        raise _error("REMOTE_SOURCE_INVALID", "source_url must be a non-empty canonical URL or null.")
    try:
        scheme = urlsplit(source).scheme.lower()
    except ValueError:
        raise _error("REMOTE_SOURCE_INVALID", "Malformed source URL.") from None
    remote = scheme in {"http", "https", "gdrive", "google-drive"}
    if source_url and scheme in {"gdrive", "google-drive"}:
        raise _error("REMOTE_SOURCE_INVALID", "source_url requires a local PDF or an HTTP(S) download URL.")
    if not remote and not source_url:
        if "://" in source or scheme in {"file", "data", "blob", "attachment", "sandbox"}:
            raise _error("SOURCE_REFERENCE_UNRESOLVED", "This file reference is not a local path or HTTP(S) download.",
                         "Ask the owning connector/host to materialize its file_uri, then pass workspace_path as source and the canonical Drive URL as source_url.")
        yield MaterializedSource(source)
        return
    if source_url and not remote and ("://" in source or scheme in {"file", "data", "blob", "attachment", "sandbox"}):
        raise _error("SOURCE_REFERENCE_UNRESOLVED", "source_url requires a materialized local path as source.",
                     "Resolve file_uri through its owning connector/host and pass workspace_path as source.")
    origin = source_url or source
    ref = _drive_ref(origin)
    canonical = f"https://drive.google.com/file/d/{ref[0]}/view" if ref else _safe_url(origin)
    provenance: dict[str, Any] = {
        "kind": "google_drive" if ref else "http_pdf",
        "source_url": canonical,
        "access_method": ("connector_download" if remote else "connector_materialized") if source_url else "download",
    }
    if ref:
        provenance["drive_file_id"] = ref[0]
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    with tempfile.TemporaryDirectory(prefix="documa-pdf-") as temporary:
        try:
            path = Path(temporary) / "source.pdf"
            if source_url and not remote:
                with Path(source).open("rb") as reader:
                    size, digest = _copy_pdf(reader, path)
                provenance["filename"] = Path(source).name
            else:
                source_parts = urlsplit(source)
                direct_drive_download = source_parts.hostname == "drive.usercontent.google.com" or (
                    source_parts.hostname == "drive.google.com" and source_parts.path == "/uc"
                )
                # A connector/browser download may already carry a confirmation
                # and short-lived signature. Preserve it verbatim on the wire;
                # converting it back to a share-link download loses authorization.
                use_drive_api = ref and not source_url and not direct_drive_download
                url, headers, filename = _drive_download(ref, deadline) if use_drive_api else (source, {}, None)
                with _response(url, headers, deadline) as (response, final_url, connection):
                    if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
                        raise _error("REMOTE_RESPONSE_INVALID", "PDF server returned unsupported content encoding.")
                    size, digest = _copy_pdf(response, path, connection=connection, deadline=deadline,
                                             content_length=response.getheader("Content-Length"))
                    if not ref:
                        provenance["resolved_url"] = final_url
                if filename:
                    provenance["filename"] = filename
            provenance.update({"byte_count": size, "sha256": digest})
        except DocumaError:
            raise
        except ssl.SSLError:
            raise _error("REMOTE_TLS_FAILED", "TLS verification or negotiation failed while downloading the PDF.",
                         "Check the server certificate and local CA trust; TLS verification remains enabled.") from None
        except (TimeoutError, socket.timeout):
            raise _error("REMOTE_DOWNLOAD_TIMEOUT", "PDF download timed out.") from None
        except http.client.IncompleteRead:
            raise _error("REMOTE_DOWNLOAD_INCOMPLETE", "PDF download ended before the full response arrived.") from None
        except (OSError, http.client.HTTPException):
            raise _error("REMOTE_DOWNLOAD_FAILED", "Unable to retrieve the PDF bytes.",
                         "Check connectivity and access permissions, or pass a connector-materialized workspace_path.") from None
        # Parser, pipeline and output errors belong to their existing layers.
        yield MaterializedSource(str(path), provenance)
