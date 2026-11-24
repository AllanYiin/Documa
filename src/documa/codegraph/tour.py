"""Deterministic, source-located repository tours."""

from __future__ import annotations

import re
import sqlite3
from html import escape
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote


def tour_entries(connection: sqlite3.Connection, limit: int) -> list[str]:
    seen: set[str] = set()
    entries = []
    rows = connection.execute("""
        SELECT nodes.node_id, nodes.qualified_name, nodes.source_locator, nodes.kind
        FROM nodes LEFT JOIN metrics USING(node_id)
        WHERE source_locator IS NOT NULL AND kind IN ('module','crate','function') AND COALESCE(role, '') != 'test'
        ORDER BY CASE WHEN display_name IN ('main','cli','__main__') THEN 0 ELSE 1 END,
                 COALESCE(metrics.fan_out + metrics.fan_in, 0) DESC, nodes.qualified_name, nodes.node_id
    """)
    for row in rows:
        name = row["qualified_name"]
        if row["kind"] == "crate":
            group = name
        else:
            path = PurePosixPath(row["source_locator"])
            group = "/".join(path.parts[:2]) if len(path.parts) > 2 else path.parent.as_posix()
        if group not in seen:
            seen.add(group)
            entries.append(row["node_id"])
        if len(entries) == limit:
            break
    return entries


def tour_steps(result: dict[str, Any], digests: dict[str, str]) -> list[dict[str, Any]]:
    paths = {path["nodeId"]: path["edgeIds"] for path in result["proofPaths"]}
    edges = {edge["edgeId"]: edge for edge in result["edges"]}
    steps = []
    for node in result["nodes"]:
        path = paths.get(node["nodeId"], [])
        locator = node.get("sourceLocator")
        if not locator or locator not in digests:
            continue
        relation = edges[path[-1]]["type"] if path else None
        role = "entry" if not relation else {
            "calls": "flow", "constructs": "branch", "binds": "boundary", "documents": "reference",
        }.get(relation, "dependency")
        steps.append({
            "nodeId": node["nodeId"], "title": node["qualifiedName"], "role": role,
            "locator": locator, "sourceSpan": node.get("sourceSpan"), "sourceHash": digests[locator],
            "proofEdgeIds": path,
        })
    return steps


def render_tour_markdown(result: dict[str, Any]) -> str:
    """Render verified locators without interpreting repository text as Markdown."""
    names = {"entry": "入口", "flow": "呼叫", "branch": "分支", "boundary": "語言邊界", "reference": "文件", "dependency": "依賴"}
    output = ["# 專案導覽", "", f"索引世代 `{result['generation']}`", ""]
    for number, step in enumerate(result["tour"], 1):
        safe_title = re.sub(r"[\x00-\x1f`<>]", " ", step["title"]).strip()
        locator = step["locator"]
        label = escape(locator).replace("[", "\\[").replace("]", "\\]").replace("\n", " ").replace("\r", " ")
        href = quote(locator, safe="/")
        if step["sourceSpan"]:
            start, end = step["sourceSpan"]["startLine"], step["sourceSpan"]["endLine"]
            label += f":{start}"
            href += f"#L{start}-L{end}"
        output.extend([f"{number}. **{names[step['role']]}** `{safe_title}` · [{label}]({href})"])
    if not result["tour"]:
        output.append("目前查詢範圍內沒有可定位的導覽步驟。")
    if result["uncertaintyReceipt"]["count"]:
        output.extend(["", f"另有 {result['uncertaintyReceipt']['count']} 個靜態分析缺口可在結構化回應中查看。"])
    return "\n".join(output) + "\n"
