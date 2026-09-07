"""Recover PDF heading boundaries and levels before paragraph/tree building."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
import copy
import re
from statistics import median

from documa.core.ir import (
    BlockIR,
    BlockType,
    Confidence,
    DocumentIR,
    TextContent,
    to_plain_data,
)

_TEXT = {BlockType.TEXT, BlockType.PARAGRAPH, BlockType.HEADING}
_LABELS = {
    "abstract": 2,
    "摘要": 2,
    "keywords": 2,
    **dict.fromkeys(
        (
            "introduction",
            "background",
            "related work",
            "methods",
            "methodology",
            "results",
            "discussion",
            "conclusion",
            "conclusions",
            "references",
            "acknowledgments",
            "acknowledgements",
            "appendix",
            "前言",
            "結論",
            "參考文獻",
        ),
        2,
    ),
}
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)(?:[.)])?\s+([^\n]+)$")


def _span_font(span) -> float:
    size = span.font_size or 0.0
    # A 1-unit font can be scaled by the PDF text matrix. Keep the source
    # value and use rendered height only for this heading comparison.
    if 0 < size <= 1.01 and span.bbox and span.bbox[3] - span.bbox[1] > 2:
        return (span.bbox[3] - span.bbox[1]) / 0.9
    return size


def pdf_block_font_size(block: BlockIR) -> float:
    sizes = [
        _span_font(span)
        for span in block.spans
        if span.font_size and span.font_size > 0 and span.text.raw_text.strip()
    ]
    return float(median(sizes)) if sizes else 0.0


_font = pdf_block_font_size


def _body_font(document: DocumentIR) -> float:
    weights: Counter[float] = Counter()
    for page in document.pages:
        for block in page.blocks:
            if block.type not in {BlockType.TEXT, BlockType.PARAGRAPH}:
                continue
            if block.metadata.get("reading_region") == "figure":
                continue
            for span in block.spans:
                if span.font_size and span.font_size > 0:
                    weights[_span_font(span)] += len(
                        "".join(span.text.raw_text.split())
                    )
    total, accumulated = sum(weights.values()), 0
    for size, weight in sorted(weights.items()):
        accumulated += weight
        if accumulated >= total / 2:
            return float(size)
    return 0.0


def _protected(block: BlockIR) -> bool:
    if block.metadata.get("reading_region") == "figure":
        return True
    tag = str(block.metadata.get("tag", "")).casefold()
    return tag in {"code", "pre", "li", "lbody", "ul", "ol"} or any(
        re.search(r"mono|courier|consolas|menlo", span.font_name or "", re.I)
        for span in block.spans
    )


def _level(text: str, *, strong: bool) -> int | None:
    text = " ".join(text.split()).rstrip(":：")
    if text.casefold() in _LABELS:
        return _LABELS[text.casefold()]
    numbered = _NUMBERED.fullmatch(text)
    if (
        numbered
        and len(text) <= 140
        and len(text.split()) <= 18
        and not text.endswith(tuple(".!?,;。！？，；"))
    ):
        title = numbered.group(2).strip()
        title_like = title[0].isupper() or "\u3400" <= title[0] <= "\u9fff"
        if (
            strong
            and title_like
            and numbered.group(1).split(".")[0] != "0"
            and not re.search(r"[.!?。！？](?:\s|$)", title)
        ):
            return min(6, 2 + numbered.group(1).count("."))
    return None


def pdf_heading_geometry(block: BlockIR, width: float, height: float) -> bool:
    if not block.bbox or not (
        0 <= block.bbox[0] < block.bbox[2] <= width + 1
        and 0 <= block.bbox[1] < block.bbox[3] <= height + 1
    ):
        return False
    ratios = [
        (s.bbox[3] - s.bbox[1]) / s.font_size
        for s in block.spans
        if s.bbox and s.font_size
    ]
    return not ratios or median(ratios) >= 0.55


def _split(
    block: BlockIR,
    body_font: float,
    sources: dict,
    used_ids: set[str],
    heading_fonts: set[str],
) -> list[BlockIR]:
    if block.type not in _TEXT or not block.text or _protected(block):
        return [block]
    raw = block.text.raw_text
    # Map character ranges against unchanged source spans, without guessing
    # offsets when the parser/pipeline has transformed or reordered text.
    positions = [i for i, char in enumerate(raw) if not char.isspace()]
    compact_spans = ["".join(span.text.raw_text.split()) for span in block.spans]
    if "".join(compact_spans) != "".join(raw.split()) or not positions:
        return [block]
    span_ranges = []
    offset = 0
    for span, text in zip(block.spans, compact_spans):
        if text:
            span_ranges.append((offset, offset + len(text), span))
        offset += len(text)
    span_starts = [a for a, _, _ in span_ranges]
    span_ends = [b for _, b, _ in span_ranges]
    span_end_by_id = {span.id: b for _, b, span in span_ranges}

    def selected(start: int, end: int):
        lo, hi = bisect_left(positions, start), bisect_left(positions, end)
        matches = span_ranges[
            bisect_right(span_ends, lo) : bisect_left(span_starts, hi)
        ]
        if not matches or matches[0][0] != lo or matches[-1][1] != hi:
            return None
        return [span for _, _, span in matches]

    ranges: list[tuple[int, int, int]] = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        end = offset + len(line)
        spans = selected(offset, end)
        heading_end = end
        # A chart label can share the visual row of a real section heading.
        # Stop at a large gap or a transformed-font run, retaining the label
        # as a separate source slice instead of adding it to the heading.
        if spans and _NUMBERED.match(line.strip()):
            kept = []
            for span in spans:
                if kept and kept[-1].bbox and span.bbox:
                    scale = max(kept[-1].font_size or body_font, body_font)
                    compressed = (
                        span.font_size
                        and span.bbox[3] - span.bbox[1] < span.font_size * 0.55
                    )
                    if span.bbox[0] - kept[-1].bbox[2] > scale * 2 or compressed:
                        break
                kept.append(span)
            if kept and len(kept) < len(spans):
                heading_end = positions[span_end_by_id[kept[-1].id] - 1] + 1
                while heading_end < end and raw[heading_end].isspace():
                    heading_end += 1
                spans = kept
        sizes = [
            _span_font(s)
            for s in spans or []
            if s.font_size and s.text.raw_text.strip()
        ]
        size = float(median(sizes)) if sizes else 0.0
        strong = bool(body_font and size >= body_font * 1.10)
        strong |= bool(
            size >= body_font * 0.98
            and spans
            and all(s.font_name in heading_fonts for s in spans)
        )
        # Explicit dotted section numbering also supports same-size headings;
        # a short, isolated title must occupy less width than its body block.
        boxes = [s.bbox for s in spans or [] if s.bbox]
        numbered = _NUMBERED.match(line.strip())
        if boxes and block.bbox and numbered and "." in numbered.group(1):
            row_width = max(b[2] for b in boxes) - min(b[0] for b in boxes)
            strong |= (
                row_width < (block.bbox[2] - block.bbox[0]) * 0.8
                and size >= body_font * 0.98
            )
        ratios = [
            (s.bbox[3] - s.bbox[1]) / s.font_size
            for s in spans or []
            if s.bbox and s.font_size
        ]
        level = (
            _level(raw[offset:heading_end].strip(), strong=strong)
            if not ratios or median(ratios) >= 0.55
            else None
        )
        if level is not None and spans:
            ranges.append((offset, heading_end, level))
        offset = end

    # A previous grouping pass may have flattened "Abstract\nWe ...". Only
    # split this prefix if its own spans establish a separate larger heading.
    prefix = re.match(r"^\s*(Abstract|摘要)(?:\s+|[:：]\s*)", raw, re.I)
    if prefix and not ranges:
        end = prefix.end()
        head, body = selected(0, end), selected(end, len(raw))
        head_size = median([_span_font(s) for s in head or [] if s.font_size] or [0])
        body_size = median([_span_font(s) for s in body or [] if s.font_size] or [0])
        if head and body and head_size >= body_size * 1.10:
            ranges = [(0, end, 2)]
    if not ranges:
        return [block]

    segments = []
    offset = 0
    for start, end, level in ranges:
        if start > offset:
            segments.append((offset, start, None))
        segments.append((start, end, level))
        offset = end
    if offset < len(raw):
        segments.append((offset, len(raw), None))
    if len(segments) == 1:
        block.type = BlockType.HEADING
        block.metadata.update(
            heading_level=segments[0][2],
            heading_strategy="pdf_semantic_label_or_number",
        )
        return [block]
    selections = [selected(start, end) for start, end, _ in segments]
    if any(spans is None for spans in selections):
        return [block]
    sources.setdefault(
        block.id,
        {
            "text": to_plain_data(block.text),
            "bbox": block.bbox,
            "source_refs": list(block.source_refs),
            "page_number": block.page_number,
            "span_ids": [span.id for span in block.spans],
        },
    )
    parts = []
    for index, ((start, end, level), spans) in enumerate(zip(segments, selections)):
        part = copy.copy(block)
        part.metadata = copy.deepcopy(block.metadata)
        if index:
            suffix = index + 1
            part.id = f"{block.id}__part{suffix}"
            while part.id in used_ids:
                suffix += 1
                part.id = f"{block.id}__part{suffix}"
            used_ids.add(part.id)
        part.spans = spans
        part.text = TextContent(raw[start:end], unicode_form=block.text.unicode_form)
        part.type = BlockType.HEADING if level else BlockType.TEXT
        boxes = [span.bbox for span in spans if span.bbox]
        if boxes:
            part.bbox = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
        part.metadata["heading_source_range"] = {
            "block_id": block.id,
            "raw_start": start,
            "raw_end": end,
        }
        part.metadata.pop("heading_level", None)
        if level:
            part.metadata.update(
                heading_level=level, heading_strategy="pdf_semantic_label_or_number"
            )
            part.confidence = Confidence.MEDIUM
        parts.append(part)
    assert "".join(part.text.raw_text for part in parts) == raw
    return parts


def split_pdf_headings(document: DocumentIR) -> int:
    if document.parser not in {"rust_pdf", "pymupdf"}:
        return 0
    body_font = _body_font(document)
    sources = document.metadata.get("pdf_heading_sources", {})
    used_ids = {b.id for p in document.pages for b in p.blocks}
    changed = 0
    heading_fonts: set[str] = set()
    # First recover typographically clear headings; then recognize their
    # same-font, body-size numbered subsections (common in academic PDFs).
    for _ in range(2):
        for page in document.pages:
            blocks = []
            for block in page.blocks:
                previous_type = block.type
                parts = _split(block, body_font, sources, used_ids, heading_fonts)
                changed += int(len(parts) > 1 or block.type != previous_type)
                blocks.extend(parts)
            page.blocks = blocks
            for index, block in enumerate(blocks):
                block.order_index = index + 1
        heading_fonts.update(
            s.font_name
            for p in document.pages
            for b in p.blocks
            if b.type == BlockType.HEADING and b.metadata.get("heading_strategy")
            for s in b.spans
            if s.font_name
        )
    if sources:
        document.metadata["pdf_heading_sources"] = sources
    return changed


def assign_pdf_heading_levels(document: DocumentIR) -> int:
    if document.parser not in {"rust_pdf", "pymupdf"} or not document.pages:
        return 0
    headings = [
        b
        for p in document.pages
        for b in p.blocks
        if b.type == BlockType.HEADING and b.text and b.metadata.get("reading_region") != "figure"
    ]
    body_font = _body_font(document)
    title_candidates = [
        b
        for b in document.pages[0].blocks
        if b in headings
        and b.bbox
        and b.bbox[1] < document.pages[0].height * 0.45
        and _font(b) >= body_font * 1.35
        and len(b.text.raw_text.strip()) >= 8
        and any(c.isalpha() for c in b.text.raw_text)
        and not re.match(r"^\d+(?:\.\d+)*\s+", b.text.raw_text.strip())
        and b.text.raw_text.strip().casefold() not in _LABELS
    ]
    title = max(title_candidates, key=_font) if title_candidates else None
    title_parts = []
    if title is not None:
        title_parts = [title]
        first_page = document.pages[0].blocks
        for candidate in first_page[first_page.index(title) + 1 :]:
            previous = title_parts[-1]
            if (
                candidate not in title_candidates
                or not candidate.bbox
                or not previous.bbox
                or abs(_font(candidate) - _font(title)) > _font(title) * 0.05
                or not 0 <= candidate.bbox[1] - previous.bbox[3] <= _font(title) * 0.8
            ):
                break
            title_parts.append(candidate)
    title_ids = {part.id for part in title_parts}
    before = [
        (b.id, b.metadata.get("heading_level"), b.metadata.get("heading_role"))
        for b in headings
    ]
    if "document_title_block_id" in document.metadata:
        document.metadata.pop("document_title", None)
        document.metadata.pop("document_title_block_id", None)
        document.metadata.pop("document_title_block_ids", None)
    for heading in headings:
        if (
            heading.id not in title_ids
            and heading.metadata.get("heading_role") == "document_title"
        ):
            heading.metadata.pop("heading_role")
    # The whole document is available here: assign a stable global font scale
    # after detection, excluding the document title and non-heading regions.
    sizes = []
    for size in sorted(
        {_font(b) for b in headings if b.id not in title_ids and _font(b) > 0},
        reverse=True,
    ):
        if not sizes or abs(size - sizes[-1]) > 0.1:
            sizes.append(size)

    def rank(index: int) -> int:
        return index + 2 if len(sizes) <= 5 else 2 + index * 4 // (len(sizes) - 1)

    document.metadata["pdf_heading_font_scale"] = [
        {"font_size": size, "heading_level": rank(index)}
        for index, size in enumerate(sizes)
    ]
    base_levels = {
        h.id: rank(min(range(len(sizes)), key=lambda i: abs(sizes[i] - _font(h))))
        if sizes
        else 2
        for h in headings
        if h.id not in title_ids
    }
    positions = {h.id: index for index, h in enumerate(headings)}
    numbered = {}
    keys = {}
    for h in headings:
        match = _NUMBERED.fullmatch(" ".join(h.text.raw_text.split()))
        if h.id not in title_ids and match:
            key = tuple(match.group(1).split("."))
            keys[h.id] = key
            numbered.setdefault(key, []).append(h)
    decisions = {}

    def decide(h):
        if h.id in decisions:
            return decisions[h.id]
        tag = re.fullmatch(r"H([1-6])", str(h.metadata.get("tag", "")), re.I)
        inferred = (
            "heading_level" not in h.metadata
            or h.metadata.get("heading_strategy") == "pdf_semantic_label_or_number"
            or h.metadata.get("heading_level_source")
            in {"global_font_scale", "font_scale_and_numbering"}
        )
        if tag:
            result = (min(6, int(tag.group(1)) + 1), "source_tag")
        elif not inferred:
            result = (max(2, min(6, int(h.metadata["heading_level"]))), "source_level")
        else:
            level, source = base_levels[h.id], "global_font_scale"
            key = keys.get(h.id, ())
            parents = numbered.get(key[:-1], []) if len(key) > 1 else []
            if parents:
                earlier = [p for p in parents if positions[p.id] < positions[h.id]]
                parent = earlier[-1] if earlier else parents[0]
                level = min(6, max(level, decide(parent)[0] + 1))
                source = "font_scale_and_numbering"
            result = (level, source)
        decisions[h.id] = result
        return result

    for heading in headings:
        if heading.id in title_ids:
            heading.metadata.update(heading_level=1, heading_role="document_title")
            document.metadata["document_title"] = " ".join(
                " ".join(part.text.raw_text.split()) for part in title_parts
            )
            document.metadata["document_title_block_id"] = title.id
            if len(title_parts) > 1:
                document.metadata["document_title_block_ids"] = [
                    part.id for part in title_parts
                ]
            continue
        level, source = decide(heading)
        heading.metadata.update(
            heading_level=level,
            heading_font_level=base_levels[heading.id],
            heading_level_source=source,
        )
    after = [
        (b.id, b.metadata.get("heading_level"), b.metadata.get("heading_role"))
        for b in headings
    ]
    return sum(old != new for old, new in zip(before, after))
