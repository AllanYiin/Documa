"""Markdown parser adapter for structure-first block querying."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, PageIR, TableIR, TextContent


_ATX_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.*?)(?:\s+#+\s*)?$")
_MDP_BLOCK_HEADER_RE = re.compile(
    r"^(?P<indent>\s*)-\s+\*\*#(?P<id>[a-z0-9][a-z0-9-]*[a-z0-9]|[a-z0-9])\*\*"
    r"(?P<meta>(?:\s+`[^`]+`)*)\s*$"
)
_META_PAIR_RE = re.compile(r"`([a-z][a-z0-9-]*):([^`]+)`")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _metadata(value: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _META_PAIR_RE.finditer(value)}


def _clean_title(value: str, *, max_chars: int = 120) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)
    value = re.sub(r"\[[^\]]+\]\([^)]+\)", lambda match: match.group(0).split("](", 1)[0][1:], value)
    value = re.sub(r"^[>\s#*\-`_]+|[>\s#*\-`_]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > max_chars:
        return value[:max_chars].rstrip() + "…"
    return value


def _paragraph_title(lines: list[str], fallback: str | None = None) -> str | None:
    for line in lines:
        title = _clean_title(line)
        if title:
            return title
    return fallback


def _is_table(lines: list[str]) -> bool:
    compact = [line for line in lines if line.strip()]
    return (
        len(compact) >= 2
        and _TABLE_ROW_RE.match(compact[0]) is not None
        and _TABLE_SEP_RE.match(compact[1]) is not None
    )


def _document_id(source_path: Path, text: str) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}\n{text}".encode("utf-8")).hexdigest()[:16]
    return f"doc_md_{digest}"


@dataclass(slots=True)
class _OpenMarkdownPlusBlock:
    block: BlockIR
    explicit_title: bool


class MarkdownAdapter(ParserAdapter):
    """Parse Markdown or Markdown+ text into Documa IR blocks.

    The adapter intentionally stays conservative: headings and Markdown+ block
    headers become structural heading blocks; prose paragraphs and tables become
    leaf blocks. Downstream stages then build the queryable document block tree
    and keyword metadata.
    """

    name = "markdown"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        text = source_path.read_text(encoding="utf-8")
        document = DocumentIR(
            id=_document_id(source_path, text),
            source_name=str(source_path),
            parser=self.name,
            metadata={
                "adapter": self.name,
                "line_count": len(text.splitlines()),
                "languages": list(options.languages),
                "format": self._format_label(source_path),
            },
        )
        page = PageIR(
            id="page_1",
            page_number=1,
            width=0,
            height=max(1, len(text.splitlines())),
            metadata={"source": "markdown_text", "line_count": len(text.splitlines())},
        )
        document.pages.append(page)

        self._parse_lines(text.splitlines(), document, page)
        return document

    def _format_label(self, source_path: Path) -> str:
        suffix = source_path.suffix.lower()
        suffixes = [item.lower() for item in source_path.suffixes]
        if suffix in {".mdp"} or suffixes[-2:] == [".mdp", ".md"]:
            return "markdown_plus"
        if suffix in {".md", ".markdown"}:
            return "markdown"
        return "text"

    def _parse_lines(self, lines: list[str], document: DocumentIR, page: PageIR) -> None:
        order = 0
        paragraph: list[tuple[int, str]] = []
        in_fence = False
        fence_marker = ""
        heading_path: list[str] = []
        open_mdp: list[_OpenMarkdownPlusBlock] = []

        def add_block(block_type: BlockType, text: str, start_line: int, end_line: int, metadata: dict) -> BlockIR:
            nonlocal order
            order += 1
            block = BlockIR(
                id=f"md_b{order:04d}",
                type=block_type,
                page_number=1,
                text=TextContent(text),
                confidence=Confidence.HIGH if text.strip() else Confidence.UNKNOWN,
                order_index=order,
                source_refs=[f"markdown:line:{start_line}-{end_line}"],
                metadata={
                    "source_type": "markdown",
                    "line_start": start_line,
                    "line_end": end_line,
                    **metadata,
                },
            )
            page.blocks.append(block)
            return block

        def current_mdp_context() -> dict:
            if not open_mdp:
                return {}
            return {
                "markdown_plus_parent_id": open_mdp[-1].block.metadata.get("markdown_plus_id"),
                "markdown_plus_path": [
                    str(item.block.metadata.get("markdown_plus_id"))
                    for item in open_mdp
                    if item.block.metadata.get("markdown_plus_id")
                ],
            }

        def flush_paragraph() -> None:
            nonlocal paragraph
            if not paragraph:
                return
            start_line = paragraph[0][0]
            end_line = paragraph[-1][0]
            body_lines = [line for _, line in paragraph]
            body = "\n".join(body_lines).strip("\n")
            if not body.strip():
                paragraph = []
                return
            title = _paragraph_title(body_lines)
            block_type = BlockType.TABLE if _is_table(body_lines) else BlockType.PARAGRAPH
            metadata = {
                "paragraph_title": title,
                "heading_path": list(heading_path),
                "build_strategy": "markdown_paragraph",
                **current_mdp_context(),
            }
            if block_type == BlockType.TABLE:
                metadata["table_title"] = title
            block = add_block(block_type, body, start_line, end_line, metadata)
            if block_type == BlockType.TABLE:
                document.tables.append(
                    TableIR(
                        id=f"table_{block.id}",
                        block_id=block.id,
                        markdown=body,
                        confidence=Confidence.MEDIUM,
                        metadata={"source": "markdown_table"},
                    )
                )
            if open_mdp and not open_mdp[-1].explicit_title and title:
                open_mdp[-1].block.text = TextContent(title)
                open_mdp[-1].block.metadata["title_fallback_source_block_id"] = block.id
                open_mdp[-1].explicit_title = True
            paragraph = []

        for line_number, raw in enumerate(lines, start=1):
            stripped = raw.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                marker = stripped[:3]
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                elif stripped.startswith(fence_marker):
                    in_fence = False
                    fence_marker = ""
                paragraph.append((line_number, raw))
                continue

            if not in_fence:
                mdp_header = _MDP_BLOCK_HEADER_RE.match(raw)
                if mdp_header:
                    flush_paragraph()
                    metadata = _metadata(mdp_header.group("meta") or "")
                    block_id = mdp_header.group("id")
                    indent = len(mdp_header.group("indent"))
                    level = max(1, min(indent // 2 + 1, 6))
                    while len(open_mdp) >= level:
                        open_mdp.pop()
                    title = metadata.get("title") or block_id.replace("-", " ")
                    block = add_block(
                        BlockType.HEADING,
                        title,
                        line_number,
                        line_number,
                        {
                            "heading_level": level,
                            "markdown_plus_id": block_id,
                            "markdown_plus_metadata": metadata,
                            "build_strategy": "markdown_plus_block_header",
                        },
                    )
                    open_mdp.append(_OpenMarkdownPlusBlock(block=block, explicit_title=bool(metadata.get("title"))))
                    heading_path[:] = [item.block.text.raw_text for item in open_mdp]
                    continue

                heading = _ATX_HEADING_RE.match(raw)
                if heading:
                    flush_paragraph()
                    level = len(heading.group("marks"))
                    title = _clean_title(heading.group("title"))
                    heading_path[:] = heading_path[: level - 1] + [title]
                    open_mdp.clear()
                    add_block(
                        BlockType.HEADING,
                        title,
                        line_number,
                        line_number,
                        {
                            "heading_level": level,
                            "heading_path": list(heading_path),
                            "build_strategy": "markdown_atx_heading",
                        },
                    )
                    continue

                if not raw.strip():
                    flush_paragraph()
                    continue

            paragraph.append((line_number, raw))

        flush_paragraph()
