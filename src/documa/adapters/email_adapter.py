"""Email parser adapter for EML and Outlook MSG files."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import re
from pathlib import Path
from typing import Any

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, PageIR, TextContent
from documa.storage.assets import AssetStore, safe_asset_name


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass(slots=True)
class _EmailPayload:
    subject: str | None
    sender: str | None
    receivers: list[str]
    cc: list[str]
    bcc: list[str]
    date: str | None
    body: str
    body_content_type: str | None
    attachments: list[dict[str, Any]]


def _load_extract_msg():
    try:
        import extract_msg  # type: ignore

        return extract_msg
    except ImportError as exc:
        raise DocumaError(
            DocumaErrorDetail(
                code="MSG_DEPENDENCY_NOT_INSTALLED",
                message="extract-msg is required for Outlook MSG parsing.",
                recoverable=True,
                suggested_action="Install or repair the standard runtime: pip install --upgrade documa",
            )
        ) from exc


def _document_id(source_path: Path, size: int, format_name: str) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}\n{size}".encode("utf-8")).hexdigest()[:16]
    return f"doc_{format_name}_{digest}"


def _header_value(message: Any, name: str) -> str | None:
    value = message.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _address_values(message: Any, name: str) -> list[str]:
    values = [str(item) for item in message.get_all(name, [])]
    addresses = []
    for display_name, address in getaddresses(values):
        display_name = display_name.strip()
        address = address.strip()
        if display_name and address:
            addresses.append(f"{display_name} <{address}>")
        elif address:
            addresses.append(address)
        elif display_name:
            addresses.append(display_name)
    return addresses


def _date_iso(raw_date: str | None) -> str | None:
    if not raw_date:
        return None
    try:
        parsed = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return parsed.isoformat()


def _html_to_text(value: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        return "\n".join(line.strip() for line in BeautifulSoup(value, "html.parser").get_text("\n").splitlines() if line.strip())
    except ImportError:
        from html import unescape

        text = _HTML_TAG_RE.sub(" ", value)
        text = unescape(text)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _clean_text(value: str) -> str:
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _WHITESPACE_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _first_attr(obj: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    text = text.strip()
    return text or None


def _split_recipients(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        values = [str(item) for item in value]
    else:
        values = [str(value).replace(";", ",")]
    parsed = []
    for display_name, address in getaddresses(values):
        display_name = display_name.strip()
        address = address.strip()
        if display_name and address:
            parsed.append(f"{display_name} <{address}>")
        elif address:
            parsed.append(address)
        elif display_name:
            parsed.append(display_name)
    return parsed or [item.strip() for item in values[0].split(";") if item.strip()]


def _attachment_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return bytes(value)
    except TypeError:
        return str(value).encode("utf-8")


def _write_attachment(
    asset_store: AssetStore | None,
    *,
    prefix: str,
    index: int,
    filename: str,
    data: bytes,
) -> str | None:
    if asset_store is None:
        return None
    safe_name = safe_asset_name(filename)
    return asset_store.write_bytes(f"attachments/{prefix}_{index:04d}_{safe_name}", data)


class EmailAdapter(ParserAdapter):
    """Parse EML and Outlook MSG files into logical Documa IR."""

    name = "email"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        suffix = source_path.suffix.lower()
        asset_store = AssetStore(options.asset_dir) if options.asset_dir else None

        if suffix == ".eml":
            payload = self._parse_eml(source_path, asset_store)
            format_name = "eml"
        elif suffix == ".msg":
            payload = self._parse_msg(source_path, asset_store)
            format_name = "msg"
        else:
            raise DocumaError(
                DocumaErrorDetail(
                    code="UNSUPPORTED_EMAIL_FORMAT",
                    message=f"Unsupported email format: {source_path.suffix or '(no suffix)'}",
                    recoverable=True,
                    suggested_action="Use .eml or .msg.",
                    context={"source": str(source_path), "suffix": source_path.suffix},
                )
            )

        return self._document_from_payload(source_path, format_name, payload, options)

    def _parse_eml(self, source_path: Path, asset_store: AssetStore | None) -> _EmailPayload:
        try:
            message = BytesParser(policy=policy.default).parsebytes(source_path.read_bytes())
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="EML_OPEN_FAILED",
                    message=f"Unable to open EML: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists and is a valid .eml file.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc

        body_part = message.get_body(preferencelist=("plain", "html")) if hasattr(message, "get_body") else None
        if body_part is None:
            for part in message.walk():
                content_type = part.get_content_type()
                if content_type in {"text/plain", "text/html"} and not part.get_filename():
                    body_part = part
                    break

        body = ""
        body_content_type = None
        if body_part is not None:
            body_content_type = body_part.get_content_type()
            content = body_part.get_content()
            body = str(content)
            if body_content_type == "text/html":
                body = _html_to_text(body)
            body = _clean_text(body)

        attachments = []
        for index, part in enumerate(message.iter_attachments(), start=1):
            filename = part.get_filename() or f"attachment_{index}"
            data = part.get_payload(decode=True)
            if data is None:
                content = part.get_content()
                data = _attachment_bytes(content)
            asset_ref = _write_attachment(asset_store, prefix="email", index=index, filename=filename, data=data)
            attachments.append(
                {
                    "index": index,
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "content_id": _header_value(part, "Content-ID"),
                    "disposition": part.get_content_disposition(),
                    "size": len(data),
                    "asset_ref": asset_ref,
                }
            )

        return _EmailPayload(
            subject=_header_value(message, "Subject"),
            sender=_header_value(message, "From"),
            receivers=_address_values(message, "To"),
            cc=_address_values(message, "Cc"),
            bcc=_address_values(message, "Bcc"),
            date=_header_value(message, "Date"),
            body=body,
            body_content_type=body_content_type,
            attachments=attachments,
        )

    def _parse_msg(self, source_path: Path, asset_store: AssetStore | None) -> _EmailPayload:
        extract_msg = _load_extract_msg()
        message = None
        try:
            if hasattr(extract_msg, "openMsg"):
                message = extract_msg.openMsg(str(source_path))
            else:
                message = extract_msg.Message(str(source_path))
            return self._payload_from_msg(message, asset_store)
        except DocumaError:
            raise
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="MSG_OPEN_FAILED",
                    message=f"Unable to open Outlook MSG: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists and is a valid .msg file.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc
        finally:
            close = getattr(message, "close", None)
            if callable(close):
                close()

    def _payload_from_msg(self, message: Any, asset_store: AssetStore | None) -> _EmailPayload:
        body = _string_value(_first_attr(message, ("body", "bodyText", "text"))) or ""
        body_content_type = "text/plain" if body else None
        if not body:
            html_body = _first_attr(message, ("htmlBody", "html", "html_body"))
            if html_body is not None:
                body = _html_to_text(_string_value(html_body) or "")
                body_content_type = "text/html"

        attachments = []
        for index, attachment in enumerate(getattr(message, "attachments", []) or [], start=1):
            filename = (
                _string_value(_first_attr(attachment, ("longFilename", "shortFilename", "filename", "name")))
                or f"attachment_{index}"
            )
            data = _attachment_bytes(_first_attr(attachment, ("data", "content", "binary")))
            asset_ref = _write_attachment(asset_store, prefix="msg", index=index, filename=filename, data=data)
            attachments.append(
                {
                    "index": index,
                    "filename": filename,
                    "content_type": _string_value(_first_attr(attachment, ("mimetype", "mimeType", "contentType"))),
                    "content_id": _string_value(_first_attr(attachment, ("cid", "contentId", "content_id"))),
                    "disposition": "attachment",
                    "size": len(data),
                    "asset_ref": asset_ref,
                }
            )

        return _EmailPayload(
            subject=_string_value(_first_attr(message, ("subject", "Subject"))),
            sender=_string_value(_first_attr(message, ("sender", "senderEmail", "sender_email", "from_"))),
            receivers=_split_recipients(_first_attr(message, ("to", "recipients", "recipient"))),
            cc=_split_recipients(_first_attr(message, ("cc", "carbonCopy"))),
            bcc=_split_recipients(_first_attr(message, ("bcc", "blindCarbonCopy"))),
            date=_string_value(_first_attr(message, ("date", "sent_date", "deliveryTime", "messageDate"))),
            body=_clean_text(body),
            body_content_type=body_content_type,
            attachments=attachments,
        )

    def _document_from_payload(
        self,
        source_path: Path,
        format_name: str,
        payload: _EmailPayload,
        options: ParseOptions,
    ) -> DocumentIR:
        size = source_path.stat().st_size if source_path.exists() else 0
        metadata = {
            "adapter": self.name,
            "format": format_name,
            "languages": list(options.languages),
            "page_model": "logical_email",
            "email": {
                "subject": payload.subject,
                "sender": payload.sender,
                "receiver": payload.receivers,
                "receivers": payload.receivers,
                "cc": payload.cc,
                "bcc": payload.bcc,
                "date": payload.date,
                "date_iso": _date_iso(payload.date),
                "body_content_type": payload.body_content_type,
                "attachments": payload.attachments,
            },
        }
        document = DocumentIR(
            id=_document_id(source_path, size, format_name),
            source_name=str(source_path),
            parser=format_name,
            metadata=metadata,
        )
        page = PageIR(
            id="page_1",
            page_number=1,
            width=1.0,
            height=0.0,
            metadata={"source": "email_message", "page_model": "logical_email"},
        )
        document.pages.append(page)

        order = 0
        if payload.subject:
            order += 1
            page.blocks.append(
                BlockIR(
                    id="email_subject",
                    type=BlockType.HEADING,
                    page_number=1,
                    text=TextContent(payload.subject),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"{format_name}:header:subject"],
                    metadata={"source_type": "email_subject", "heading_level": 1},
                )
            )

        header_lines = [
            ("Sender", payload.sender),
            ("Receiver", ", ".join(payload.receivers) if payload.receivers else None),
            ("Cc", ", ".join(payload.cc) if payload.cc else None),
            ("Bcc", ", ".join(payload.bcc) if payload.bcc else None),
            ("Date", payload.date),
        ]
        header_text = "\n".join(f"{label}: {value}" for label, value in header_lines if value)
        if header_text:
            order += 1
            page.blocks.append(
                BlockIR(
                    id="email_headers",
                    type=BlockType.PARAGRAPH,
                    page_number=1,
                    text=TextContent(header_text),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"{format_name}:headers"],
                    metadata={"source_type": "email_headers"},
                )
            )

        if payload.body:
            order += 1
            page.blocks.append(
                BlockIR(
                    id="email_body",
                    type=BlockType.PARAGRAPH,
                    page_number=1,
                    text=TextContent(payload.body),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"{format_name}:body"],
                    metadata={"source_type": "email_body", "content_type": payload.body_content_type},
                )
            )

        if payload.attachments:
            rows = [
                f"{item['filename']} ({item.get('content_type') or 'unknown'}, {item['size']} bytes)"
                for item in payload.attachments
            ]
            order += 1
            page.blocks.append(
                BlockIR(
                    id="email_attachments",
                    type=BlockType.PARAGRAPH,
                    page_number=1,
                    text=TextContent("Attachments:\n" + "\n".join(rows)),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"{format_name}:attachments"],
                    metadata={"source_type": "email_attachments", "attachments": payload.attachments},
                )
            )

        page.height = float(max(1, order))
        return document
