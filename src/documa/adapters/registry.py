"""Source-format adapter registry."""

from __future__ import annotations

from pathlib import Path

from documa.adapters.base import ParserAdapter
from documa.adapters.docx_adapter import DocxAdapter
from documa.adapters.html_adapter import HtmlAdapter
from documa.adapters.markdown_adapter import MarkdownAdapter
from documa.adapters.pptx_adapter import PptxAdapter
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdp", ".txt"}
_PDF_SUFFIXES = {".pdf"}
_DOCX_SUFFIXES = {".docx"}
_PPTX_SUFFIXES = {".pptx"}
_HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


def adapter_for_source(source: str | Path) -> ParserAdapter:
    source_path = Path(source)
    suffix = source_path.suffix.lower()
    suffixes = [item.lower() for item in source_path.suffixes]

    if suffix in _MARKDOWN_SUFFIXES or suffixes[-2:] == [".mdp", ".md"]:
        return MarkdownAdapter()
    if suffix in _PDF_SUFFIXES:
        return PyMuPDFAdapter()
    if suffix in _DOCX_SUFFIXES:
        return DocxAdapter()
    if suffix in _PPTX_SUFFIXES:
        return PptxAdapter()
    if suffix in _HTML_SUFFIXES:
        return HtmlAdapter()

    raise DocumaError(
        DocumaErrorDetail(
            code="UNSUPPORTED_DOCUMENT_FORMAT",
            message=f"Unsupported document format: {source_path.suffix or '(no suffix)'}",
            recoverable=True,
            suggested_action="Use one of: .pdf, .md, .markdown, .mdp, .txt, .docx, .pptx, .html, .htm, .xhtml.",
            context={"source": str(source_path), "suffix": source_path.suffix},
        )
    )
