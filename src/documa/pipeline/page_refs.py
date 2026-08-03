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
    coordinate_space = str(document.metadata.get("coordinate_space") or "")
    for page in sorted(document.pages, key=lambda item: item.page_number):
        printed_labels = []
        for block in page.blocks:
            if block.type != BlockType.PAGE_FOOTER:
                continue
            label = printed_page_label_from_footer(block_text(block))
            if label:
                printed_labels.append(label)
        printed_label = _unique(printed_labels)[0] if printed_labels else None
        source_label = str(page.metadata.get("label") or "").strip() or None
        page_ref = page.page_number
        citation_geometry = str(page.metadata.get("citation_geometry") or "")
        if citation_geometry == "structural":
            page_ref_kind = "structural"
            if str(page.metadata.get("source") or "") == "worksheet":
                citation_label = f'Worksheet "{source_label or page_ref}"'
            else:
                citation_label = source_label or "Document structure"
        elif coordinate_space == "slide_points":
            page_ref_kind = "slide_number_1_based"
            citation_label = f"Slide {page_ref}"
            if source_label and source_label != f"Slide {page_ref}":
                citation_label += f" ({source_label})"
        else:
            page_ref_kind = PAGE_REF_KIND
            if printed_label:
                citation_label = f"PDF p.{page_ref} (printed p.{printed_label})"
            elif source_label and source_label != str(page_ref):
                citation_label = f"PDF p.{page_ref} (label {source_label})"
            else:
                citation_label = f"PDF p.{page_ref}"
        page_citations[str(page_ref)] = {
            "page_ref": page_ref,
            "page_ref_kind": page_ref_kind,
            "printed_page_label": printed_label,
            "pdf_page_label": source_label if page_ref_kind == PAGE_REF_KIND else None,
            "citation_label": citation_label,
        }
    return page_citations


def ensure_page_citation_map(document: DocumentIR) -> dict[str, dict[str, Any]]:
    page_citations = document.metadata.get("page_citations")
    if not isinstance(page_citations, dict):
        page_citations = build_page_citation_map(document)
        document.metadata["page_citations"] = page_citations
    kinds = {
        str(item.get("page_ref_kind") or PAGE_REF_KIND)
        for item in page_citations.values()
        if isinstance(item, dict)
    }
    document.metadata["page_ref_kind"] = (
        next(iter(kinds)) if len(kinds) == 1 else "mixed"
    )
    return page_citations


def document_page_ref_kind(document: DocumentIR) -> str:
    ensure_page_citation_map(document)
    return str(document.metadata.get("page_ref_kind") or PAGE_REF_KIND)


def page_citation_metadata(
    page_refs: list[int],
    page_citations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    refs = _unique([int(page_ref) for page_ref in page_refs])
    citation_items = []
    printed_labels = []
    pdf_labels = []
    page_ref_kinds = []
    for page_ref in refs:
        item = page_citations.get(str(page_ref)) or {
            "page_ref": page_ref,
            "page_ref_kind": PAGE_REF_KIND,
            "printed_page_label": None,
            "pdf_page_label": None,
            "citation_label": f"PDF p.{page_ref}",
        }
        citation_items.append(str(item.get("citation_label") or f"PDF p.{page_ref}"))
        page_ref_kinds.append(str(item.get("page_ref_kind") or PAGE_REF_KIND))
        printed_label = item.get("printed_page_label")
        if printed_label:
            printed_labels.append(str(printed_label))
        pdf_label = item.get("pdf_page_label")
        if pdf_label:
            pdf_labels.append(str(pdf_label))
    unique_kinds = _unique(page_ref_kinds)
    return {
        "page_ref_kind": unique_kinds[0]
        if len(unique_kinds) == 1
        else ("mixed" if unique_kinds else PAGE_REF_KIND),
        "printed_page_labels": _unique(printed_labels),
        "pdf_page_labels": _unique(pdf_labels),
        "citation_label": ", ".join(citation_items),
    }
