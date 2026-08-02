"""Document-region inference shared by single-document and collection ranking.

Lives in core so both `documa.interfaces.search_ranking` (single-document
in-memory search) and `documa.collections.sqlite_index` (FTS collection
search) demote navigation/boilerplate regions with identical rules, without
an interfaces<->collections import cycle.
"""

from __future__ import annotations

# Score multipliers by document region: navigation and boilerplate regions are
# demoted — never excluded — so body evidence outranks TOC/header noise while
# region hits stay reachable for explicitly structural queries.
DOC_REGION_MULTIPLIERS = {
    "toc": 0.3,
    "header_footer": 0.3,
    "footnote": 0.45,
    "references": 0.6,
    "metadata": 0.6,
}


def doc_region_multiplier(doc_region: str) -> float:
    return DOC_REGION_MULTIPLIERS.get(doc_region, 1.0)


def infer_doc_region(
    heading_titles: list[str],
    title: str | None,
    block_type: str,
    *,
    source_type: str = "",
    role: str = "",
) -> str:
    """Classify a block into a coarse document region from path/type signals."""
    joined = " ".join([*heading_titles, title or ""]).casefold()
    source_type = source_type.casefold()
    role = role.casefold()
    if block_type in {"toc", "table_of_content"} or "table of contents" in joined or joined.strip() == "contents":
        return "toc"
    if block_type == "metadata":
        return "metadata"
    if block_type == "footnote" or "footnote" in {source_type, role}:
        return "footnote"
    if "page_header" in {source_type, role} or "page_footer" in {source_type, role}:
        return "header_footer"
    if any(
        term in joined
        for term in (
            "references",
            "bibliography",
            "works cited",
            "參考文獻",
            "参考文献",
            "參考資料",
            "参考资料",
            "引用文獻",
            "引用文献",
            "書目",
            "书目",
        )
    ):
        return "references"
    if "appendix" in joined or "annex" in joined:
        return "appendix"
    return "body"
