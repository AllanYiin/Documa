"""Universal human-viewer payloads for Documa documents."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import re
from typing import Any

from documa.core.ir import DocumentBlockIR, DocumentIR
from documa.pipeline import BlockKeywordExtractionStage, BlockTreeBuildingStage, PipelineContext
from documa.pipeline.block_tree import document_block_text
from documa.pipeline.page_refs import ensure_page_citation_map, page_citation_metadata


VIEWER_FORMATS = {"json", "markdown", "html"}
_SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class ViewerOptions:
    query: str = ""
    max_depth: int | None = None
    include_body: bool = False
    body_chars: int = 1200
    result_limit: int = 10


def ensure_viewer_blocks(document: DocumentIR) -> None:
    """Ensure the document has a hierarchical block tree and metadata summary."""

    if not document.document_blocks:
        context = PipelineContext(settings={})
        BlockTreeBuildingStage().run(document, context)
        BlockKeywordExtractionStage().run(document, context)


def build_universal_viewer(document: DocumentIR, options: ViewerOptions | None = None) -> dict[str, Any]:
    """Build a format-neutral human viewer payload from any Documa document."""

    options = options or ViewerOptions()
    ensure_viewer_blocks(document)
    page_citations = ensure_page_citation_map(document)
    by_parent = _children_by_parent(document)
    roots = [_node(document, block, by_parent, page_citations, options) for block in by_parent.get(None, [])]
    flat = [_flat_row(document, block, page_citations, options) for block in _ordered_blocks(document)]
    return {
        "viewer": "documa_universal_viewer",
        "version": 1,
        "document": {
            "id": document.id,
            "source_name": document.source_name,
            "parser": document.parser,
            "ir_version": document.ir_version,
            "page_count": document.page_count,
            "block_count": len(document.document_blocks),
            "chunk_count": len(document.chunks),
            "table_count": len(document.tables),
            "image_count": sum(len(page.images) for page in document.pages),
            "relation_count": len(document.relations),
            "metadata": document.metadata,
        },
        "view_options": {
            "query": options.query,
            "max_depth": options.max_depth,
            "include_body": options.include_body,
            "body_chars": options.body_chars,
            "result_limit": options.result_limit,
        },
        "page_citations": page_citations,
        "tree": roots,
        "blocks": flat,
        "query_results": _query_blocks(document, page_citations, options),
    }


def render_viewer_markdown(payload: dict[str, Any]) -> str:
    document = payload["document"]
    lines = [
        f"# Documa Universal Viewer: {document['source_name']}",
        "",
        "## Metadata",
        "",
        f"- Document ID: `{document['id']}`",
        f"- Parser: `{document.get('parser')}`",
        f"- Pages: `{document['page_count']}`",
        f"- Blocks: `{document['block_count']}`",
        f"- Chunks: `{document['chunk_count']}`",
        "",
    ]
    metadata = document.get("metadata") or {}
    if metadata:
        lines.extend(["```json", json.dumps(metadata, ensure_ascii=False, indent=2), "```", ""])
    if payload.get("query_results"):
        lines.extend(["## Query Results", ""])
        for item in payload["query_results"]:
            title = item.get("title") or item["id"]
            lines.append(f"- `{item['id']}` {title} ({item['type']}, score {item['score']})")
            if item.get("snippet"):
                lines.append(f"  {item['snippet']}")
        lines.append("")
    lines.extend(["## Document Blocks", ""])
    for root in payload.get("tree", []):
        _append_markdown_node(lines, root)
    return "\n".join(lines).rstrip() + "\n"


def render_viewer_html(payload: dict[str, Any]) -> str:
    document = payload["document"]
    tree_html = "\n".join(_html_node(node) for node in payload.get("tree", []))
    results_html = "\n".join(_html_result(item) for item in payload.get("query_results", []))
    metadata_json = escape(json.dumps(document.get("metadata") or {}, ensure_ascii=False, indent=2))
    title = escape(str(document["source_name"]))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Documa Universal Viewer</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #202124;
      --muted: #5f6368;
      --line: #dadce0;
      --accent: #0b57d0;
      --soft: #edf2ff;
      --code: #f1f3f4;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.94);
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0; font-size: 18px; font-weight: 650; }}
    .meta-strip {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}
    main {{
      display: grid;
      grid-template-columns: minmax(260px, 30%) 1fr;
      min-height: calc(100vh - 58px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: var(--panel);
      overflow: auto;
      max-height: calc(100vh - 58px);
      padding: 18px;
    }}
    section.viewer {{
      overflow: auto;
      max-height: calc(100vh - 58px);
      padding: 22px 28px 48px;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin: 0 0 8px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 16px;
    }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      background: #fafafa;
    }}
    .stat strong {{ display: block; font-size: 16px; }}
    details {{
      border: 1px solid var(--line);
      border-radius: 6px;
      margin: 8px 0;
      background: var(--panel);
    }}
    summary {{
      cursor: pointer;
      padding: 10px 12px;
      font-weight: 620;
    }}
    .block-body {{ padding: 0 12px 12px; }}
    .preview {{ color: var(--ink); margin: 6px 0; }}
    .block-meta, pre {{
      color: var(--muted);
      background: var(--code);
      border-radius: 6px;
      overflow: auto;
      padding: 9px;
      font-size: 12px;
    }}
    .children {{ padding-left: 18px; border-left: 2px solid var(--soft); margin-left: 12px; }}
    .result {{
      border-left: 4px solid var(--accent);
      background: var(--soft);
      padding: 10px 12px;
      border-radius: 6px;
      margin: 8px 0 14px;
    }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 820px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      main {{ grid-template-columns: 1fr; }}
      aside, section.viewer {{ max-height: none; }}
      aside {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .meta-strip {{ white-space: normal; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="meta-strip">parser {escape(str(document.get("parser")))} · {document["page_count"]} pages · {document["block_count"]} blocks</div>
  </header>
  <main>
    <aside>
      <p class="label">Document Metadata</p>
      <div class="stats">
        <div class="stat"><strong>{document["page_count"]}</strong>pages</div>
        <div class="stat"><strong>{document["block_count"]}</strong>blocks</div>
        <div class="stat"><strong>{document["chunk_count"]}</strong>chunks</div>
        <div class="stat"><strong>{document["relation_count"]}</strong>relations</div>
      </div>
      <pre>{metadata_json}</pre>
      <p class="label">Query Results</p>
      {results_html or '<p class="empty">No query results.</p>'}
    </aside>
    <section class="viewer" aria-label="Documa human viewer">
      <p class="label">Hierarchical Blocks</p>
      {tree_html}
    </section>
  </main>
</body>
</html>
"""


def render_viewer(payload: dict[str, Any], format: str) -> Any:
    if format == "json":
        return payload
    if format == "markdown":
        return render_viewer_markdown(payload)
    if format == "html":
        return render_viewer_html(payload)
    raise ValueError(f"Unsupported viewer format: {format}")


def _children_by_parent(document: DocumentIR) -> dict[str | None, list[DocumentBlockIR]]:
    output: dict[str | None, list[DocumentBlockIR]] = {}
    for block in document.document_blocks:
        output.setdefault(block.parent_id, []).append(block)
    for children in output.values():
        children.sort(key=lambda item: (item.order_index is None, item.order_index or 0, item.id))
    return output


def _ordered_blocks(document: DocumentIR) -> list[DocumentBlockIR]:
    return sorted(document.document_blocks, key=lambda item: (item.order_index is None, item.order_index or 0, item.id))


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _node(
    document: DocumentIR,
    block: DocumentBlockIR,
    by_parent: dict[str | None, list[DocumentBlockIR]],
    page_citations: dict[str, dict[str, Any]],
    options: ViewerOptions,
) -> dict[str, Any]:
    body = document_block_text(document, block)
    children = []
    if options.max_depth is None or block.depth < options.max_depth:
        children = [_node(document, child, by_parent, page_citations, options) for child in by_parent.get(block.id, [])]
    return {
        **_block_base(block, page_citations),
        "text_preview": block.text_preview,
        "body": _truncate(body, options.body_chars) if options.include_body and body else None,
        "metadata": block.metadata,
        "children": children,
    }


def _flat_row(
    document: DocumentIR,
    block: DocumentBlockIR,
    page_citations: dict[str, dict[str, Any]],
    options: ViewerOptions,
) -> dict[str, Any]:
    body = document_block_text(document, block)
    return {
        **_block_base(block, page_citations),
        "text_preview": block.text_preview,
        "body": _truncate(body, options.body_chars) if options.include_body and body else None,
        "metadata": block.metadata,
    }


def _block_base(block: DocumentBlockIR, page_citations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": block.id,
        "type": block.type.value,
        "title": block.title,
        "depth": block.depth,
        "order_index": block.order_index,
        "page_refs": block.page_refs,
        **page_citation_metadata(block.page_refs, page_citations),
        "source_block_ids": block.source_block_ids,
        "source_chunk_ids": block.source_chunk_ids,
        "children_count": len(block.child_ids),
    }


def _query_blocks(
    document: DocumentIR,
    page_citations: dict[str, dict[str, Any]],
    options: ViewerOptions,
) -> list[dict[str, Any]]:
    terms = [term.casefold() for term in _SPACE_RE.split(options.query.strip()) if term.strip()]
    if not terms:
        return []
    results = []
    for block in document.document_blocks:
        body = document_block_text(document, block)
        fields = {
            "title": block.title or "",
            "preview": block.text_preview or "",
            "body": body,
            "metadata": json.dumps(block.metadata, ensure_ascii=False),
        }
        score = 0
        matched = []
        snippet = None
        for term in terms:
            term_hit = False
            for field_name, text in fields.items():
                lowered = text.casefold()
                index = lowered.find(term)
                if index < 0:
                    continue
                term_hit = True
                score += {"title": 5, "preview": 3, "body": 2, "metadata": 1}[field_name]
                if snippet is None:
                    snippet = _snippet(text, index, len(term))
            if term_hit:
                matched.append(term)
        if score > 0:
            results.append(
                {
                    **_block_base(block, page_citations),
                    "score": score,
                    "matched": matched,
                    "snippet": snippet,
                    "text_preview": block.text_preview,
                }
            )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[: max(0, options.result_limit)]


def _snippet(text: str, start: int, size: int) -> str:
    clean = _SPACE_RE.sub(" ", text).strip()
    start = max(0, min(start, len(clean)))
    left = max(0, start - 44)
    right = min(len(clean), start + size + 44)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(clean) else ""
    return f"{prefix}{clean[left:right]}{suffix}"


def _append_markdown_node(lines: list[str], node: dict[str, Any]) -> None:
    indent = "  " * int(node.get("depth", 0))
    title = node.get("title") or node["id"]
    lines.append(f"{indent}- `{node['id']}` {title} ({node['type']})")
    if node.get("citation_label"):
        lines.append(f"{indent}  - source: {node['citation_label']}")
    preview = _dedupe_display_text(node.get("text_preview"), title)
    body = _dedupe_display_text(node.get("body"), title, preview)
    if preview:
        lines.append(f"{indent}  - preview: {preview}")
    if node.get("metadata"):
        compact = json.dumps(node["metadata"], ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{indent}  - metadata: `{compact}`")
    if body:
        lines.append(f"{indent}  - body: {body}")
    for child in node.get("children", []):
        _append_markdown_node(lines, child)


def _html_node(node: dict[str, Any]) -> str:
    raw_title = str(node.get("title") or node["id"])
    title = escape(raw_title)
    preview_text = _dedupe_display_text(node.get("text_preview"), raw_title)
    body_text = _dedupe_display_text(node.get("body"), raw_title, preview_text)
    preview = escape(preview_text or "")
    body = escape(body_text or "")
    metadata = escape(json.dumps(node.get("metadata") or {}, ensure_ascii=False, indent=2))
    children = "\n".join(_html_node(child) for child in node.get("children", []))
    return f"""
<details open>
  <summary>{title} <span class="empty">· {escape(node['type'])} · {escape(node['id'])}</span></summary>
  <div class="block-body">
    <div class="block-meta">pages: {escape(str(node.get('citation_label') or node.get('page_refs') or []))}</div>
    {f'<p class="preview">{preview}</p>' if preview else ''}
    {f'<p class="preview">{body}</p>' if body else ''}
    <details><summary>metadata</summary><pre>{metadata}</pre></details>
    {f'<div class="children">{children}</div>' if children else ''}
  </div>
</details>
"""


def _dedupe_display_text(value: Any, *already_visible: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = _SPACE_RE.sub(" ", text).strip()
    for existing in already_visible:
        existing_text = str(existing or "").strip()
        if not existing_text:
            continue
        if normalized == _SPACE_RE.sub(" ", existing_text).strip():
            return None
    return text


def _html_result(item: dict[str, Any]) -> str:
    title = escape(str(item.get("title") or item["id"]))
    snippet = escape(str(item.get("snippet") or ""))
    return f"""
<div class="result">
  <strong>{title}</strong>
  <div class="empty">{escape(item['id'])} · score {item['score']}</div>
  {f'<div>{snippet}</div>' if snippet else ''}
</div>
"""
