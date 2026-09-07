"""Geometry-backed soft-wrap removal for the human-readable PDF export.

Only the exported view changes. Source block/span text and evidence offsets are
left intact; ambiguous text-to-span alignment keeps the original line breaks.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import re
from statistics import median

from documa.core.ir import BlockIR, BlockType, SpanIR
from documa.core.text_joining import (
    font_profiles,
    profiles_match,
    heading_metadata,
    list_metadata,
    is_heading_block,
    is_code_block as _is_code,
    is_list_block as _is_list,
)


@dataclass(frozen=True)
class _Line:
    bbox: tuple[float, float, float, float]
    em: float
    typography: list[tuple]
    protected: bool


def _compact(text: str) -> str:
    return "".join(text.split())


def _line_geometry(block: BlockIR, lines: list[str]) -> list[_Line | None] | None:
    ends: list[int] = []
    position = 0
    for text in lines:
        position += len(_compact(text))
        ends.append(position)
    spans = [
        (span, _compact(span.text.normalized_text or span.text.raw_text))
        for span in block.spans
    ]
    if "".join(text for _, text in spans) != "".join(_compact(text) for text in lines):
        return None
    groups: list[list[SpanIR]] = [[] for _ in lines]
    position = 0
    for span, text in spans:
        if not text:
            continue
        start = bisect_right(ends, position)
        position += len(text)
        end = bisect_left(ends, position)
        # A span crossing textual rows has no independently known row boxes.
        if start != end or not span.bbox:
            return None
        groups[start].append(span)
    result: list[_Line | None] = []
    for group in groups:
        if not group:
            result.append(None)
            continue
        boxes = [span.bbox for span in group]
        bbox = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        profiles = font_profiles(group)
        size = profiles[0][1] if profiles else None
        result.append(
            _Line(
                bbox,
                size if size and size > 0 else max(1.0, bbox[3] - bbox[1]),
                profiles,
                any(
                    heading_metadata(span.metadata) or list_metadata(span.metadata)
                    for span in group
                ),
            )
        )
    return result


def _sentence_end(text: str) -> bool:
    # Closing quotes/brackets and numeric citations do not hide a full stop.
    text = text.rstrip()
    text = re.sub(r"(?:\s*\[\d[\d,\s–-]*\])+$", "", text)
    return text.rstrip(" \t\"'”’」』）》)]").endswith(tuple(".!?。！？"))


def _separator(left: str, right: str) -> str:
    # Retain source hyphens (including real compound words); no lexical guesses.
    if len(left) > 1 and left[-1] == "-" and left[-2].isalpha() and right[:1].islower():
        return ""
    if (
        left
        and right
        and ("\u3400" <= left[-1] <= "\u9fff" or "\u3400" <= right[0] <= "\u9fff")
    ):
        return ""
    return " "


def reflow_pdf_text(block: BlockIR, text: str) -> str:
    """Join full-width, non-terminal rows at normal spacing within a block."""
    if (
        block.type not in (BlockType.TEXT, BlockType.PARAGRAPH)
        or not block.bbox
        or not text.strip()
    ):
        return text
    if is_heading_block(block) or _is_code(block, text) or _is_list(block, text):
        return text
    lines = text.splitlines()
    if len(lines) < 2 or not block.spans:
        return text
    geometry = _line_geometry(block, lines)
    if geometry is None:
        return text
    width = block.bbox[2] - block.bbox[0]
    if width <= 0:
        return text
    em = median(line.em for line in geometry if line is not None)
    gaps = [
        right.bbox[1] - left.bbox[3]
        for left, right in zip(geometry, geometry[1:])
        if left and right and 0 <= right.bbox[1] - left.bbox[3] <= 0.8 * em
    ]
    # Use the lower median: a paragraph gap must not inflate the usual spacing
    # in a short block with only one ordinary gap and one paragraph gap.
    normal_gap = sorted(gaps)[(len(gaps) - 1) // 2] if gaps else 0.3 * em
    output = lines[0]
    for index, (left, right) in enumerate(zip(geometry, geometry[1:])):
        previous, current = lines[index].rstrip(), lines[index + 1].lstrip()
        join = False
        if (
            left
            and right
            and previous
            and current
            and (block.type == BlockType.PARAGRAPH or not _sentence_end(previous))
            and not left.protected
            and not right.protected
            and profiles_match(
                left.typography[:1]
                if block.type == BlockType.PARAGRAPH
                else left.typography,
                right.typography[:1]
                if block.type == BlockType.PARAGRAPH
                else right.typography,
            )
        ):
            gap = right.bbox[1] - left.bbox[3]
            join = (
                left.bbox[2] >= block.bbox[2] - max(em, width * 0.05)
                and left.bbox[2] - left.bbox[0] >= width * 0.85
                and abs(right.bbox[0] - block.bbox[0]) <= em * 0.5
                and right.bbox[1] - left.bbox[1] >= em * 0.5
                and -em * 0.2 <= gap <= min(normal_gap + em * 0.3, em * 0.8)
                and not re.match(r"(?:[-*•]|\d+[.)])\s", current)
            )
        output = (
            output.rstrip() + _separator(previous, current) + current
            if join
            else output + "\n" + lines[index + 1]
        )
    return output
