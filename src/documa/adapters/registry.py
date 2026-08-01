"""Source-format adapter registry."""

from __future__ import annotations

from pathlib import Path

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.adapters.docx_adapter import DocxAdapter
from documa.adapters.email_adapter import EmailAdapter
from documa.adapters.html_adapter import HtmlAdapter
from documa.adapters.ipynb_adapter import IpynbAdapter
from documa.adapters.markdown_adapter import MarkdownAdapter
from documa.adapters.pptx_adapter import PptxAdapter
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.adapters.rust_pdf_adapter import RustPdfAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdp", ".txt"}
_PDF_SUFFIXES = {".pdf"}
_DOCX_SUFFIXES = {".docx"}
_PPTX_SUFFIXES = {".pptx"}
_HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
_EMAIL_SUFFIXES = {".eml", ".msg"}
_IPYNB_SUFFIXES = {".ipynb"}


class RustFirstPdfAdapter(ParserAdapter):
    """Prefer Rust extraction and make a recoverable PyMuPDF fallback visible."""

    name = "rust_pdf_first"

    def parse(self, source: str | Path, options: ParseOptions | None = None):
        try:
            document = RustPdfAdapter().parse(source, options)
        except DocumaError as exc:
            if not exc.detail.recoverable:
                raise
            document = PyMuPDFAdapter().parse(source, options)
            document.metadata["pdf_provider"] = {
                "requested": "rust",
                "actual": "pymupdf",
                "fallback": True,
                "reason_code": exc.detail.code,
                "reason": exc.detail.message,
            }
            return document
        document.metadata["pdf_provider"] = {
            "requested": "rust",
            "actual": "rust",
            "fallback": False,
        }
        return document


def adapter_for_source(source: str | Path, *, pdf_provider: str | None = None) -> ParserAdapter:
    source_path = Path(source)
    suffix = source_path.suffix.lower()
    suffixes = [item.lower() for item in source_path.suffixes]

    if suffix in _MARKDOWN_SUFFIXES or suffixes[-2:] == [".mdp", ".md"]:
        return MarkdownAdapter()
    if suffix in _PDF_SUFFIXES:
        provider = (pdf_provider or "auto").casefold()
        if provider in {"auto", "rust-first", "rust_first"}:
            return RustFirstPdfAdapter()
        if provider == "pymupdf":
            return PyMuPDFAdapter()
        if provider in {"rust", "rust_pdf"}:
            return RustPdfAdapter()
        raise DocumaError(
            DocumaErrorDetail(
                code="INVALID_PDF_PROVIDER",
                message=f"Unsupported PDF provider: {provider}",
                recoverable=True,
                suggested_action="Use 'auto', 'rust', or 'pymupdf'.",
                context={"provider": provider, "source": str(source_path)},
            )
        )
    if suffix in _DOCX_SUFFIXES:
        return DocxAdapter()
    if suffix in _PPTX_SUFFIXES:
        return PptxAdapter()
    if suffix in _HTML_SUFFIXES:
        return HtmlAdapter()
    if suffix in _EMAIL_SUFFIXES:
        return EmailAdapter()
    if suffix in _IPYNB_SUFFIXES:
        return IpynbAdapter()

    raise DocumaError(
        DocumaErrorDetail(
            code="UNSUPPORTED_DOCUMENT_FORMAT",
            message=f"Unsupported document format: {source_path.suffix or '(no suffix)'}",
            recoverable=True,
            suggested_action=(
                "Use one of: .pdf, .md, .markdown, .mdp, .txt, .docx, .pptx, "
                ".html, .htm, .xhtml, .eml, .msg, .ipynb."
            ),
            context={"source": str(source_path), "suffix": source_path.suffix},
        )
    )
