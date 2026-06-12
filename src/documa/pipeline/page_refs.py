"""Page reference helpers for source citations."""

from __future__ import annotations

import re
from typing import Any

from documa.core.ir import BlockType, DocumentIR
from documa.pipeline.relations import block_text

PAGE_REF_KIND = "physical_page_number_1_based"
_PRINTED_PAGE_LABEL_RE = re.compile(r"^(?:\d{1,6}|[ivxlcdmIVXLCDM]{1,12})$")
_LABEL_EDGE_CHARS = " \t\r\n-\u2013\u2014_:\uff1a.\u3002"


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    output = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        output.append(value)
    return output


def printed_page_label_from_footer(text: str) -> str | None:
    """Extract a conservative printed page label from footer text."""

    label = " ".join(str(text or "").split()).strip(_LABEL_EDGE_CHARS)
    if not label:
        return None
    if _PRINTED_PAGE_LABEL_RE.match(label):
        return label
    return None


def build_page_citation_map(document: DocumentIR) -> dict[str, dict[str, Any]]:
    page_citations: dict[str, dict[str, Any]] = {}
    for page in sorted(document.pages, key=lambda item: item.page_number):
        printed_labels = []
        for block in page.blocks:
            if block.type != BlockType.PAGE_FOOTER:
                continue
            label = printed_page_label_from_footer(block_text(block))
            if label:
                printed_labels.append(label)
        printed_label = _unique(printed_labels)[0] if printed_labels else None
        pdf_label = str(page.metadata.get("label") or "").strip() or None
        page_ref = page.page_number
        if printed_label:
            citation_label = f"PDF p.{page_ref} (printed p.{printed_label})"
        elif pdf_label and pdf_label != str(page_ref):
            citation_label = f"PDF p.{page_ref} (label {pdf_label})"
        else:
            citation_label = f"PDF p.{page_ref}"
        page_citations[str(page_ref)] = {
            "page_ref": page_ref,
            "page_ref_kind": PAGE_REF_KIND,
            "printed_page_label": printed_label,
            "pdf_page_label": pdf_label,
            "citation_label": citation_label,
        }
    return page_citations


def ensure_page_citation_map(document: DocumentIR) -> dict[str, dict[str, Any]]:
    page_citations = document.metadata.get("page_citations")
    if not isinstance(page_citations, dict):
        page_citations = build_page_citation_map(document)
        document.metadata["page_citations"] = page_citations
    document.metadata["page_ref_kind"] = PAGE_REF_KIND
    return page_citations


def page_citation_metadata(
    page_refs: list[int],
    page_citations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs = _unique([int(page_ref) for page_ref in page_refs])
    citation_items = []
    printed_labels = []
    pdf_labels = []
    for page_ref in refs:
        item = page_citations.get(str(page_ref)) or {
            "page_ref": page_ref,
            "page_ref_kind": PAGE_REF_KIND,
            "printed_page_label": None,
            "pdf_page_label": None,
            "citation_label": f"PDF p.{page_ref}",
        }
        citation_items.append(str(item.get("citation_label") or f"PDF p.{page_ref}"))
        printed_label = item.get("printed_page_label")
        if printed_label:
            printed_labels.append(str(printed_label))
        pdf_label = item.get("pdf_page_label")
        if pdf_label:
            pdf_labels.append(str(pdf_label))
    return {
        "page_ref_kind": PAGE_REF_KIND,
        "printed_page_labels": _unique(printed_labels),
        "pdf_page_labels": _unique(pdf_labels),
        "citation_label": ", ".join(citation_items),
    }
