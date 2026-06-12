"""Parser adapter interfaces and implementations."""

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.adapters.docx_adapter import DocxAdapter
from documa.adapters.html_adapter import HtmlAdapter
from documa.adapters.markdown_adapter import MarkdownAdapter
from documa.adapters.pptx_adapter import PptxAdapter
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter
from documa.adapters.registry import adapter_for_source

__all__ = [
    "DocxAdapter",
    "HtmlAdapter",
    "MarkdownAdapter",
    "ParseOptions",
    "ParserAdapter",
    "PptxAdapter",
    "PyMuPDFAdapter",
    "adapter_for_source",
]
