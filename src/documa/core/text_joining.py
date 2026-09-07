"""Shared boundaries for joining document text without flattening structure."""

from __future__ import annotations

from math import isclose
import re

from documa.core.ir import BlockIR, BlockType, SpanIR


def heading_metadata(metadata: dict) -> bool:
    for key in ("tag", "role", "block_type", "rust_pdf_role", "heading_role"):
        value = str(metadata.get(key, "")).casefold()
        if value in {
            "h",
            "heading",
            "header",
            "footer",
            "page_header",
            "page_footer",
            "document_title",
        } or re.fullmatch(r"h[1-6]", value):
            return True
    return bool(metadata.get("heading_level"))


def is_heading_block(block: BlockIR) -> bool:
    return block.type in {
        BlockType.HEADING,
        BlockType.PAGE_HEADER,
        BlockType.PAGE_FOOTER,
    } or heading_metadata(block.metadata)


def is_caption_block(block: BlockIR, text: str) -> bool:
    if block.metadata.get("caption_kind") in {"figure", "image", "table"}:
        return True
    return bool(re.match(r"^\s*(?:Figure|Fig\.?|Table|圖表?|表)\s*(?:[0-9０-９]+|[A-Z](?:\.\d+)?|[一二三四五六七八九十]+)(?:\s*[:：.)-]|\s+)", text, re.I))


def font_profiles(spans: list[SpanIR]) -> list[tuple]:
    profiles = []
    weights = []
    for span in spans:
        if not span.text.raw_text.strip():
            continue
        size = span.font_size
        if size and 0 < size <= 1.01 and span.bbox and span.bbox[3] - span.bbox[1] > 2:
            size = (span.bbox[3] - span.bbox[1]) / 0.9
        profile = (span.font_name, size, frozenset(span.style))
        match = next(
            (
                i
                for i, existing in enumerate(profiles)
                if _same_profile(profile, existing)
            ),
            None,
        )
        weight = len("".join(span.text.raw_text.split()))
        if match is None:
            profiles.append(profile)
            weights.append(weight)
        else:
            weights[match] += weight
    return [
        profiles[i] for i in sorted(range(len(profiles)), key=lambda i: -weights[i])
    ]


def _same_profile(left: tuple, right: tuple) -> bool:
    if left[0] != right[0] or left[2] != right[2]:
        return False
    if left[1] is None or right[1] is None:
        return left[1] == right[1]
    # Only absorb PDF numeric noise, not a different typographic size.
    return isclose(left[1], right[1], rel_tol=0.0001, abs_tol=0.01)


def profiles_match(left: list[tuple], right: list[tuple]) -> bool:
    return (
        len(left) == len(right)
        and (not left or _same_profile(left[0], right[0]))
        and all(any(_same_profile(a, b) for b in right) for a in left)
    )


def typography_matches(left: list[SpanIR], right: list[SpanIR]) -> bool:
    return profiles_match(font_profiles(left), font_profiles(right))


def is_code_block(block: BlockIR, text: str) -> bool:
    metadata = block.metadata
    if metadata.get("is_code") or any(
        str(metadata.get(key, "")).casefold()
        in {"code", "pre", "code_block", "source_code"}
        for key in ("tag", "role", "block_type", "rust_pdf_role")
    ):
        return True
    if any(
        re.search(r"courier|mono|consolas|menlo", span.font_name or "", re.I)
        for span in block.spans
    ):
        return True
    # Untagged PDFs may expose only opaque font resource names. Err on the
    # side of retaining formatting when text has recognizable code syntax.
    return bool(
        re.search(
            r"(?m)^(?:[ \t]*(?:"
            r"`{3,}|~{3,}|//|#[^\n]*|[{}]\s*$|[\[\]]\s*[,;]?\s*$|"
            r"(?:async\s+def|def|class|function|import|from\s+\S+\s+import|fn|impl|use|package|interface|enum)\s|"
            r"(?:continue|break|pass)\s*;?\s*$|"
            r"(?:return|yield|raise|const|let|var|public|private|static|"
            r"int|float|double|void|char|bool|struct|typedef|namespace)\b|"
            r"(?:if|elif|else|for|while|try|except|switch|catch)\b[^\n]*[:{]\s*$|"
            r"[A-Za-z_$][\w.$]*\(|"
            r"[A-Za-z_$][\w.$\[\]]*\s*(?:=|:=)(?!=)|"
            r"[\"'][^\n\"']+[\"']\s*:|"
            r"[A-Za-z_]\w*_\w+\s*$"
            r")|\t| {4}\S)",
            text,
        )
    )


def list_metadata(metadata: dict) -> bool:
    return any(
        str(metadata.get(key, "")).casefold()
        in {
            "list",
            "list_item",
            "list_body",
            "li",
            "l",
            "lbody",
            "ul",
            "ol",
            "ordered_list",
            "unordered_list",
        }
        for key in ("tag", "role", "block_type", "rust_pdf_role")
    )


def is_list_block(block: BlockIR, text: str) -> bool:
    if list_metadata(block.metadata):
        return True
    return bool(
        re.search(
            r"(?m)^\s*(?:[-+*•●▪◦‣]\s|(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]\s|"
            r"\(\d+\)\s|\[\d+\]\s|[一二三四五六七八九十]+、|[①-⑳])",
            text,
        )
    )
