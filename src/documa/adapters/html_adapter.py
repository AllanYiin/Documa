"""HTML parser adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, PageIR, TableIR, TextContent


_BLOCK_TAGS = {"p", "li", "blockquote", "pre"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


def _load_bs4():
    try:
        from bs4 import BeautifulSoup, Tag  # type: ignore

        return BeautifulSoup, Tag
    except ImportError as exc:
        raise DocumaError(
            DocumaErrorDetail(
                code="HTML_DEPENDENCY_NOT_INSTALLED",
                message="beautifulsoup4 is required for HtmlAdapter.",
                recoverable=True,
                suggested_action="Install or repair the standard runtime: pip install --upgrade documa",
            )
        ) from exc


def _document_id(source_path: Path, size: int) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}\n{size}".encode("utf-8")).hexdigest()[:16]
    return f"doc_html_{digest}"


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _table_rows(table: Any) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    for tr in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) or None for cell in tr.find_all(["th", "td"], recursive=False)]
        if any(cells):
            rows.append(cells)
    return rows


def _table_text(rows: list[list[str | None]]) -> str:
    return "\n".join(" | ".join("" if cell is None else cell for cell in row) for row in rows)


def _links(node: Any) -> list[dict[str, str]]:
    links = []
    for anchor in node.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        label = _text(anchor)
        if href or label:
            links.append({"href": href, "text": label})
    return links


def _iter_content_nodes(root: Any, Tag: type) -> Iterable[Any]:
    for child in root.children:
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if name in _SKIP_TAGS:
            continue
        if name in {"table", "h1", "h2", "h3", "h4", "h5", "h6", *_BLOCK_TAGS}:
            yield child
            continue
        yield from _iter_content_nodes(child, Tag)


class HtmlAdapter(ParserAdapter):
    """Parse HTML into DOM-order Documa IR blocks."""

    name = "html"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        BeautifulSoup, Tag = _load_bs4()

        try:
            raw = source_path.read_bytes()
            soup = BeautifulSoup(raw, "html.parser")
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="HTML_OPEN_FAILED",
                    message=f"Unable to open HTML: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists and is readable.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc

        title = _text(soup.title) if soup.title else None
        document = DocumentIR(
            id=_document_id(source_path, source_path.stat().st_size if source_path.exists() else len(raw)),
            source_name=str(source_path),
            parser=self.name,
            metadata={
                "adapter": self.name,
                "format": "html",
                "languages": list(options.languages),
                "title": title,
                "page_model": "dom_order",
            },
        )
        page = PageIR(
            id="page_1",
            page_number=1,
            width=0.0,
            height=0.0,
            metadata={"source": "html_dom", "page_model": "dom_order"},
        )
        document.pages.append(page)

        root = soup.body or soup
        order = 0
        table_index = 0
        for node in _iter_content_nodes(root, Tag):
            name = (node.name or "").lower()
            if name == "table":
                rows = _table_rows(node)
                if not rows:
                    continue
                order += 1
                table_index += 1
                block = BlockIR(
                    id=f"html_table{table_index:04d}",
                    type=BlockType.TABLE,
                    page_number=1,
                    text=TextContent(_table_text(rows)),
                    confidence=Confidence.MEDIUM,
                    order_index=order,
                    source_refs=[f"html:table:{table_index}"],
                    metadata={
                        "source_type": "html_table",
                        "tag": name,
                        "table_rows": rows,
                        "table_title": rows[0][0] if rows and rows[0] else None,
                    },
                )
                page.blocks.append(block)
                document.tables.append(
                    TableIR(
                        id=f"table_{block.id}",
                        block_id=block.id,
                        rows=rows,
                        confidence=Confidence.MEDIUM,
                        metadata={"source": "html_table"},
                    )
                )
                continue

            text = _text(node)
            if not text:
                continue
            order += 1
            metadata: dict[str, Any] = {
                "source_type": "html_element",
                "tag": name,
                "id": node.get("id"),
                "classes": list(node.get("class") or []),
                "links": _links(node),
            }
            if name.startswith("h") and len(name) == 2 and name[1].isdigit():
                block_type = BlockType.HEADING
                metadata["heading_level"] = int(name[1])
            else:
                block_type = BlockType.PARAGRAPH
                metadata["paragraph_title"] = text
            page.blocks.append(
                BlockIR(
                    id=f"html_b{order:04d}",
                    type=block_type,
                    page_number=1,
                    text=TextContent(text),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"html:{name}:{order}"],
                    metadata=metadata,
                )
            )

        page.height = float(max(1, order))
        return document
