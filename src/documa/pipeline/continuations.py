"""Link interrupted body paragraphs while retaining page-local source blocks."""

import re

from documa.core.ir import BlockType, DocumentIR
from documa.core.text_joining import (
    font_profiles,
    profiles_match,
    is_caption_block,
    is_code_block,
    is_heading_block,
    is_list_block,
)


def join_fragments(parts: list[str]) -> str:
    output = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        separator = " "
        if (
            not output
            or (output.endswith("-") and part[:1].islower())
            or (
                output
                and (
                    "\u3400" <= output[-1] <= "\u9fff"
                    or "\u3400" <= part[0] <= "\u9fff"
                )
            )
        ):
            separator = ""
        output += separator + part
    return output


def _boundary(block, *, first):
    spans = [s for s in block.spans if s.bbox and s.text.raw_text.strip()]
    profiles = font_profiles(spans)
    if not spans or not profiles or not profiles[0][1]:
        return None
    em = profiles[0][1]
    anchor = spans[0] if first else spans[-1]
    center = (anchor.bbox[1] + anchor.bbox[3]) / 2
    row = [s for s in spans if abs((s.bbox[1] + s.bbox[3]) / 2 - center) <= em * 0.6]
    box = (
        min(s.bbox[0] for s in row),
        min(s.bbox[1] for s in row),
        max(s.bbox[2] for s in row),
        max(s.bbox[3] for s in row),
    )
    return box, font_profiles(row), em


def _can_continue(left, right, cross_page, interrupted):
    if not left.text or not right.text or not left.bbox or not right.bbox:
        return False
    a, b = left.text.raw_text.rstrip(), right.text.raw_text.lstrip()
    end = re.sub(r"(?:\s*\[\d[\d,\s–-]*\])+$", "", a).rstrip(" \"'”’)]")
    if not a or not b or re.search(r"[.!?。！？](?:\s*\d{1,3})?$", end):
        return False
    open_word = re.search(
        r"\b(?:the|a|an|of|to|and|or|by|with|for|from|in|on|between)$", a, re.I
    )
    if not (b[0].islower() or "\u3400" <= b[0] <= "\u9fff" or open_word):
        return False
    last, first = _boundary(left, first=False), _boundary(right, first=True)
    if not last or not first:
        return False
    x, lp, em = last
    y, rp, _ = first
    if not profiles_match(lp[:1], rp[:1]):
        return False
    width = left.bbox[2] - left.bbox[0]
    next_width = right.bbox[2] - right.bbox[0]
    same_column = abs(x[0] - y[0]) <= em
    next_column = not cross_page and y[0] - x[0] >= width * 0.8 and y[1] < x[1] - em * 3
    return (
        (interrupted or cross_page or next_column)
        and (same_column or next_column)
        and abs(width - next_width) <= max(width * 0.2, em * 2)
        and x[2] - x[0] >= width * 0.85
        and x[2] >= left.bbox[2] - max(width * 0.1, em * 1.5)
    )


def link_paragraph_continuations(document: DocumentIR) -> list[dict]:
    if document.parser not in {"rust_pdf", "pymupdf"}:
        return []
    for page in document.pages:
        for block in page.blocks:
            block.metadata.pop("paragraph_continuation_id", None)
    groups, active, interrupted = [], None, False
    skipped = []
    for page in sorted(document.pages, key=lambda p: p.page_number):
        if not page.blocks:
            active, interrupted = None, False
            skipped = []
        for block in sorted(page.blocks, key=lambda b: b.order_index or 0):
            text = block.text.raw_text if block.text else ""
            floating = (
                block.type
                in {
                    BlockType.PAGE_HEADER,
                    BlockType.PAGE_FOOTER,
                    BlockType.FOOTNOTE,
                    BlockType.IMAGE,
                    BlockType.CHART,
                    BlockType.TABLE,
                }
                or block.metadata.get("figure_group_id")
                or (
                    is_caption_block(block, text)
                    and (
                        block.metadata.get("caption_kind")
                        or re.match(
                            r"^\s*(?:Figure|Fig\.?|Table|圖表?|表)\s*[\dA-Z一二三四五六七八九十]+\s*[:：.\-]",
                            text,
                            re.I,
                        )
                    )
                )
            )
            if floating:
                interrupted = active is not None
                if active is not None:
                    skipped.append(block.id)
                continue
            if (
                is_heading_block(block)
                or is_list_block(block, text)
                or is_code_block(block, text)
                or block.type not in {BlockType.TEXT, BlockType.PARAGRAPH}
            ):
                active, interrupted = None, False
                skipped = []
                continue
            if not text.strip():
                continue
            if active and _can_continue(
                active, block, active.page_number != block.page_number, interrupted
            ):
                group = next(
                    (g for g in groups if g["source_block_ids"][-1] == active.id), None
                )
                if group is None:
                    group = {
                        "id": f"continuation_{active.id}",
                        "source_block_ids": [active.id],
                        "strategy": "body_flow_across_float_or_page",
                        "font_policy": "dominant_body_font",
                    }
                    groups.append(group)
                group.setdefault("bridges", []).append(
                    {
                        "from_id": active.id,
                        "to_id": block.id,
                        "skipped_block_ids": list(skipped),
                        "from_page": active.page_number,
                        "to_page": block.page_number,
                        "confidence": "medium",
                    }
                )
                group["source_block_ids"].append(block.id)
                active.metadata["paragraph_continuation_id"] = group["id"]
                block.metadata["paragraph_continuation_id"] = group["id"]
            active, interrupted = block, False
            skipped = []
    if groups:
        document.metadata["paragraph_continuations"] = groups
    else:
        document.metadata.pop("paragraph_continuations", None)
    return groups
