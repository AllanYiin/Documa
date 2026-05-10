"""Parser adapter interfaces and implementations."""

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.adapters.pymupdf_adapter import PyMuPDFAdapter

__all__ = ["ParseOptions", "ParserAdapter", "PyMuPDFAdapter"]
