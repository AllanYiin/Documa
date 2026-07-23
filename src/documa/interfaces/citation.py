"""Read-only citation layer: resolve blocks/chunks to page + bbox + excerpt payloads.

Consumes existing provenance fields (page_refs, bbox_refs, source_block_ids)
without computing or writing anything back to the IR. Missing provenance is a
pipeline bug, not something to patch here.

Block references resolve against document blocks (``db_...``) first and fall
back to page-level blocks (``p1_b1``), since chunk ``source_block_ids`` point
at page-level blocks.
"""

from __future__ import annotations

from typing import Any

from documa.core.ir import BlockIR, ChunkIR, DocumentBlockIR, DocumentIR, PageIR
from documa.pipeline.block_tree import document_block_text
from documa.pipeline.page_refs import build_page_citation_map, page_citation_metadata
from documa.pipeline.relations import block_text

ToolPayload = dict[str, Any]

EXCERPT_MAX_CHARS = 400
CITATION_STYLES = ("page-bbox", "markdown", "inline")


def _error(code: str, message: str, **extra: Any) -> ToolPayload:
    return {"status": "error", "code": code, "message": message, **extra}


def _truncate(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= EXCERPT_MAX_CHARS:
        return text
    return text[: EXCERPT_MAX_CHARS - 1] + "…"


def _page_citations(document: DocumentIR) -> dict[str, dict[str, Any]]:
    """Read-only variant of ensure_page_citation_map: never mutates the IR."""
    existing = document.metadata.get("page_citations")
    if isinstance(existing, dict):
        return existing
    return build_page_citation_map(document)


def _resolve_block(document: DocumentIR, block_id: str):
    """Return ("document_block", DocumentBlockIR) or ("page_block", (PageIR, BlockIR)) or None.

    Accepts canonical document-block ids and the short (document-prefix
    stripped) form that block-reading tool responses emit.
    """
    candidates = {block_id, f"db_{document.id}_{block_id}"}
    for block in document.document_blocks:
        if block.id in candidates:
            return ("document_block", block)
    for page in document.pages:
        for block in page.blocks:
            if block.id == block_id:
                return ("page_block", (page, block))
    return None


def _find_chunk(document: DocumentIR, chunk_id: str) -> ChunkIR | None:
    for chunk in document.chunks:
        if chunk.id == chunk_id:
            return chunk
    return None


def _block_facts(document: DocumentIR, kind: str, resolved) -> dict[str, Any]:
    """Extract page refs, paired bboxes, and excerpt for a resolved block."""
    if kind == "document_block":
        block: DocumentBlockIR = resolved
        page_refs = [int(ref) for ref in block.page_refs]
        single_page = page_refs[0] if len(page_refs) == 1 else None
        bboxes = [
            {"page": single_page, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
            for b in block.bbox_refs
        ]
        excerpt = _truncate(block.text_preview or document_block_text(document, block))
        title = block.title
        order_index = block.order_index
    else:
        page: PageIR
        block_ir: BlockIR
        page, block_ir = resolved
        page_refs = [block_ir.page_number]
        bboxes = (
            [{"page": block_ir.page_number, "x0": block_ir.bbox[0], "y0": block_ir.bbox[1], "x1": block_ir.bbox[2], "y1": block_ir.bbox[3]}]
            if block_ir.bbox is not None
            else []
        )
        excerpt = _truncate(block_text(block_ir))
        title = None
        order_index = block_ir.order_index
    return {
        "page_refs": page_refs,
        "bboxes": bboxes,
        "excerpt": excerpt,
        "title": title,
        "order_index": order_index,
    }


def _citation_string(style: str, label: str, facts: dict[str, Any], ref_id: str) -> str:
    if style == "inline":
        return f"({label})"
    if style == "markdown":
        return f"[{label}](#{ref_id})"
    # page-bbox (default)
    if facts["bboxes"]:
        b = facts["bboxes"][0]
        return f"[{label}, bbox({b['x0']:.0f},{b['y0']:.0f},{b['x1']:.0f},{b['y1']:.0f})]"
    if facts["title"]:
        return f"[{label}, {facts['title']}]"
    return f"[{label}]"


def _validate_style(style: str) -> ToolPayload | None:
    if style not in CITATION_STYLES:
        return _error(
            "UNSUPPORTED_CITATION_STYLE",
            f"Unsupported citation style: {style!r}; expected one of {list(CITATION_STYLES)}.",
        )
    return None


def cite_block(document: DocumentIR, block_id: str, style: str = "page-bbox") -> ToolPayload:
    style_error = _validate_style(style)
    if style_error:
        return style_error
    hit = _resolve_block(document, block_id)
    if hit is None:
        return _error("BLOCK_NOT_FOUND", f"No block with id {block_id!r} in document.", block_id=block_id)
    kind, resolved = hit
    facts = _block_facts(document, kind, resolved)
    label_meta = page_citation_metadata(facts["page_refs"], _page_citations(document))
    label = label_meta["citation_label"] or "no page reference"
    return {
        "status": "ok",
        "block_id": block_id,
        "kind": kind,
        "page_refs": facts["page_refs"],
        "page_number": facts["page_refs"][0] if facts["page_refs"] else None,
        "page_label": label,
        "grounding": "visual" if facts["bboxes"] else "logical",
        "bboxes": facts["bboxes"],
        "excerpt": facts["excerpt"],
        "title": facts["title"],
        "citation_string": _citation_string(style, label, facts, block_id),
    }


def cite_chunk(document: DocumentIR, chunk_id: str, style: str = "page-bbox") -> ToolPayload:
    style_error = _validate_style(style)
    if style_error:
        return style_error
    chunk = _find_chunk(document, chunk_id)
    if chunk is None:
        return _error("CHUNK_NOT_FOUND", f"No chunk with id {chunk_id!r} in document.", chunk_id=chunk_id)

    page_refs = [int(ref) for ref in chunk.page_refs]
    single_page = page_refs[0] if len(page_refs) == 1 else None
    bboxes = [
        {"page": single_page, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
        for b in chunk.bbox_refs
    ]
    label_meta = page_citation_metadata(page_refs, _page_citations(document))
    label = label_meta["citation_label"] or "no page reference"

    sources = []
    for source_id in chunk.source_block_ids:
        hit = _resolve_block(document, source_id)
        if hit is None:
            sources.append({"block_id": source_id, "exists": False})
            continue
        kind, resolved = hit
        facts = _block_facts(document, kind, resolved)
        sources.append(
            {
                "block_id": source_id,
                "exists": True,
                "kind": kind,
                "page_refs": facts["page_refs"],
                "has_bbox": bool(facts["bboxes"]),
            }
        )

    facts = {"bboxes": bboxes, "title": None}
    return {
        "status": "ok",
        "chunk_id": chunk_id,
        "page_refs": page_refs,
        "page_number": page_refs[0] if page_refs else None,
        "page_label": label,
        "grounding": "visual" if bboxes else "logical",
        "bboxes": bboxes,
        "excerpt": _truncate(chunk.text.normalized_text or chunk.text.raw_text),
        "heading_path": list(chunk.heading_path),
        "source_blocks": sources,
        "citation_string": _citation_string(style, label, facts, chunk_id),
    }


def render_citation(document: DocumentIR, ref_id: str, style: str = "page-bbox") -> ToolPayload:
    """Render a citation string for either a chunk id or a block id."""
    style_error = _validate_style(style)
    if style_error:
        return style_error
    if _find_chunk(document, ref_id) is not None:
        payload = cite_chunk(document, ref_id, style)
    else:
        payload = cite_block(document, ref_id, style)
        if payload.get("code") == "BLOCK_NOT_FOUND":
            return _error(
                "REFERENCE_NOT_FOUND",
                f"No chunk or block with id {ref_id!r} in document.",
                ref_id=ref_id,
            )
    if payload.get("status") != "ok":
        return payload
    return {
        "status": "ok",
        "ref_id": ref_id,
        "style": style,
        "citation_string": payload["citation_string"],
        "page_label": payload["page_label"],
        "grounding": payload["grounding"],
    }


def source_window(document: DocumentIR, block_id: str, before: int = 1, after: int = 1) -> ToolPayload:
    before = max(0, int(before))
    after = max(0, int(after))
    hit = _resolve_block(document, block_id)
    if hit is None:
        return _error("BLOCK_NOT_FOUND", f"No block with id {block_id!r} in document.", block_id=block_id)
    kind, resolved = hit

    warnings: list[str] = []
    if kind == "document_block":
        ordered = sorted(
            (blk for blk in document.document_blocks if blk.order_index is not None),
            key=lambda blk: blk.order_index,
        )
        target = resolved
        text_of = lambda blk: _truncate(blk.text_preview or document_block_text(document, blk))
        title_of = lambda blk: blk.title
    else:
        ordered = sorted(
            (blk for page in document.pages for blk in page.blocks if blk.order_index is not None),
            key=lambda blk: (blk.page_number, blk.order_index),
        )
        target = resolved[1]
        text_of = lambda blk: _truncate(block_text(blk))
        title_of = lambda blk: None

    try:
        position = next(i for i, blk in enumerate(ordered) if blk.id == target.id)
    except StopIteration:
        warnings.append("Block has no order_index; neighbors cannot be determined.")
        window = []
    else:
        start = max(0, position - before)
        window = [
            {
                "block_id": blk.id,
                "offset": i - position,
                "title": title_of(blk),
                "text": text_of(blk),
            }
            for i, blk in enumerate(ordered[start : position + after + 1], start=start)
        ]

    return {
        "status": "ok",
        "block_id": block_id,
        "kind": kind,
        "window": window,
        "warnings": warnings,
    }


def verify_citations(document: DocumentIR, block_ids: list[str]) -> ToolPayload:
    """Existence and page-provenance check for cited block ids.

    This is deliberately NOT semantic answer verification (which would need an
    LLM); it only guarantees the cited ids exist and carry page references.
    """
    items = []
    for block_id in block_ids:
        hit = _resolve_block(document, str(block_id))
        if hit is None:
            items.append({"block_id": str(block_id), "exists": False, "has_page_ref": False})
            continue
        kind, resolved = hit
        facts = _block_facts(document, kind, resolved)
        items.append(
            {
                "block_id": str(block_id),
                "exists": True,
                "kind": kind,
                "has_page_ref": bool(facts["page_refs"]),
            }
        )
    return {
        "status": "ok",
        "items": items,
        "overall_valid": bool(items) and all(item["exists"] and item["has_page_ref"] for item in items),
    }
