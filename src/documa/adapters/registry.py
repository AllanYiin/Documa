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
from documa.adapters.rust_office_adapter import (
    RustOfficeAdapter,
    _FALLBACK_CODES as _OFFICE_FALLBACK_CODES,
)
from documa.adapters.rust_pdf_adapter import RustPdfAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail

_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdp", ".txt"}
_PDF_SUFFIXES = {".pdf"}
_DOCX_SUFFIXES = {".docx"}
_PPTX_SUFFIXES = {".pptx"}
_RUST_OFFICE_SUFFIXES = {".docx", ".xls", ".xlsx", ".pptx"}
_LEGACY_OFFICE_SUFFIXES = {".doc", ".ppt"}
_MACRO_OFFICE_SUFFIXES = {".docm", ".xlsm", ".pptm"}
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


class PythonOfficeAdapter(ParserAdapter):
    """Run only the existing Python DOCX/PPTX adapter."""

    name = "python_office"

    def __init__(self, requested: str = "python"):
        self.requested = requested

    def parse(self, source: str | Path, options: ParseOptions | None = None):
        suffix = Path(source).suffix.casefold()
        if suffix == ".docx":
            document = DocxAdapter().parse(source, options)
            actual = "python_docx"
        elif suffix == ".pptx":
            document = PptxAdapter().parse(source, options)
            actual = "python_pptx"
        else:
            raise DocumaError(
                DocumaErrorDetail(
                    code="OFFICE_PROVIDER_CAPABILITY_UNAVAILABLE",
                    message=f"The Python Office provider does not support {suffix or '(no suffix)'}.",
                    recoverable=False,
                    suggested_action="Use office_provider='rust' for .xls/.xlsx.",
                    context={"source": str(source), "suffix": suffix},
                )
            )
        document.metadata["office_provider"] = {
            "requested": self.requested,
            "actual": actual,
            "fallback": False,
        }
        return document


class RustFirstOfficeAdapter(ParserAdapter):
    """Prefer Rust and allow only capability/binding fallback for DOCX/PPTX."""

    name = "rust_office_first"

    def __init__(self, requested: str = "auto"):
        self.requested = requested

    def parse(self, source: str | Path, options: ParseOptions | None = None):
        suffix = Path(source).suffix.casefold()
        try:
            document = RustOfficeAdapter().parse(source, options)
        except DocumaError as exc:
            if (
                suffix not in {".docx", ".pptx"}
                or exc.detail.code not in _OFFICE_FALLBACK_CODES
            ):
                raise
            document = PythonOfficeAdapter(requested=self.requested).parse(
                source, options
            )
            document.metadata["office_provider"] = {
                "requested": self.requested,
                "actual": "python_docx" if suffix == ".docx" else "python_pptx",
                "fallback": True,
                "reason_code": exc.detail.code,
                "reason": exc.detail.message,
            }
            return document
        document.metadata["office_provider"] = {
            "requested": self.requested,
            "actual": "rust",
            "fallback": False,
        }
        return document


class UnsupportedLegacyOfficeAdapter(ParserAdapter):
    name = "legacy_office_unsupported"

    def parse(self, source: str | Path, options: ParseOptions | None = None):
        raise DocumaError(
            DocumaErrorDetail(
                code="LEGACY_OFFICE_NOT_SUPPORTED",
                message=f"Legacy Word/PowerPoint format is not supported: {source}",
                recoverable=False,
                suggested_action="Convert the file to .docx or .pptx.",
                context={
                    "source": str(source),
                    "suffix": Path(source).suffix.casefold(),
                },
            )
        )


class UnsupportedMacroOfficeAdapter(ParserAdapter):
    name = "macro_office_unsupported"

    def parse(self, source: str | Path, options: ParseOptions | None = None):
        raise DocumaError(
            DocumaErrorDetail(
                code="MACRO_ENABLED_OFFICE_NOT_SUPPORTED",
                message=f"Macro-enabled Office format is not supported: {source}",
                recoverable=False,
                suggested_action="Save a macro-free .docx, .xlsx, or .pptx copy.",
                context={
                    "source": str(source),
                    "suffix": Path(source).suffix.casefold(),
                },
            )
        )


def adapter_for_source(
    source: str | Path,
    *,
    pdf_provider: str | None = None,
    office_provider: str | None = None,
) -> ParserAdapter:
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
    if suffix in _LEGACY_OFFICE_SUFFIXES:
        return UnsupportedLegacyOfficeAdapter()
    if suffix in _MACRO_OFFICE_SUFFIXES:
        return UnsupportedMacroOfficeAdapter()
    if suffix in _RUST_OFFICE_SUFFIXES:
        provider = (office_provider or "auto").casefold()
        if provider == "auto":
            return RustFirstOfficeAdapter(requested="auto")
        if provider == "rust":
            return RustOfficeAdapter()
        if provider == "python":
            if suffix in _DOCX_SUFFIXES | _PPTX_SUFFIXES:
                return PythonOfficeAdapter(requested="python")
            raise DocumaError(
                DocumaErrorDetail(
                    code="OFFICE_PROVIDER_CAPABILITY_UNAVAILABLE",
                    message=f"The Python Office provider does not support {suffix}.",
                    recoverable=False,
                    suggested_action="Use office_provider='rust' for .xls/.xlsx.",
                    context={"provider": provider, "source": str(source_path)},
                )
            )
        raise DocumaError(
            DocumaErrorDetail(
                code="INVALID_OFFICE_PROVIDER",
                message=f"Unsupported Office provider: {provider}",
                recoverable=True,
                suggested_action="Use 'auto', 'rust', or 'python'.",
                context={"provider": provider, "source": str(source_path)},
            )
        )
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
                "Use one of: .pdf, .md, .markdown, .mdp, .txt, .doc, .docx, .xls, "
                ".xlsx, .ppt, .pptx, .html, .htm, .xhtml, .eml, .msg, .ipynb."
            ),
            context={"source": str(source_path), "suffix": source_path.suffix},
        )
    )
