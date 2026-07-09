"""Structured diff between two IR payloads: added/missing/reordered blocks, table cells.

Backs the ``documa diff`` CLI command. Block ids in Documa are positional
(``p1_b2``), so id-based comparison is meaningful across runs of the same
source. Refuses to diff across major IR versions.

Quality tooling operates on IR data only and must not import pipeline internals.
"""

from __future__ import annotations

from typing import Any

_BBOX_TOLERANCE = 0.5
_MAX_CELL_DIFFS = 50


def _major(version: Any) -> str:
    return str(version or "").split(".", 1)[0]


def _block_summary(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    for page in payload.get("pages", []) or []:
        for block in page.get("blocks", []) or []:
            text = block.get("text") or {}
            blocks[str(block.get("id"))] = {
                "page": page.get("page_number"),
                "type": block.get("type"),
                "text": " ".join(str(text.get("raw_text", "")).split()),
                "order_index": block.get("order_index"),
                "bbox": block.get("bbox"),
            }
    return blocks


def _bbox_moved(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a != b
    return any(abs(float(x) - float(y)) > _BBOX_TOLERANCE for x, y in zip(a, b))


def diff_documents(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Return a structured difference report between two full IR payloads."""
    if _major(actual.get("ir_version")) != _major(expected.get("ir_version")):
        return {
            "status": "error",
            "code": "IR_MAJOR_VERSION_MISMATCH",
            "message": (
                f"Cannot diff across major IR versions "
                f"({actual.get('ir_version')} vs {expected.get('ir_version')})."
            ),
        }

    actual_blocks = _block_summary(actual)
    expected_blocks = _block_summary(expected)

    added = sorted(set(actual_blocks) - set(expected_blocks))
    missing = sorted(set(expected_blocks) - set(actual_blocks))
    text_changed = []
    reordered = []
    moved = []
    for block_id in sorted(set(actual_blocks) & set(expected_blocks)):
        a, e = actual_blocks[block_id], expected_blocks[block_id]
        if a["text"] != e["text"]:
            text_changed.append({"block_id": block_id, "actual": a["text"][:120], "expected": e["text"][:120]})
        if a["order_index"] != e["order_index"]:
            reordered.append(
                {"block_id": block_id, "actual_order": a["order_index"], "expected_order": e["order_index"]}
            )
        if _bbox_moved(a["bbox"], e["bbox"]):
            moved.append({"block_id": block_id})

    actual_tables = {str(t.get("id")): t for t in actual.get("tables", []) or []}
    expected_tables = {str(t.get("id")): t for t in expected.get("tables", []) or []}
    table_diffs = []
    for table_id in sorted(set(actual_tables) | set(expected_tables)):
        a_rows = (actual_tables.get(table_id) or {}).get("rows") or []
        e_rows = (expected_tables.get(table_id) or {}).get("rows") or []
        if table_id not in actual_tables or table_id not in expected_tables:
            table_diffs.append({"table_id": table_id, "issue": "missing_in_" + ("actual" if table_id not in actual_tables else "expected")})
            continue
        cell_diffs = []
        for r in range(max(len(a_rows), len(e_rows))):
            a_row = a_rows[r] if r < len(a_rows) else []
            e_row = e_rows[r] if r < len(e_rows) else []
            for c in range(max(len(a_row), len(e_row))):
                a_cell = a_row[c] if c < len(a_row) else None
                e_cell = e_row[c] if c < len(e_row) else None
                if (a_cell or "") != (e_cell or ""):
                    cell_diffs.append({"row": r, "col": c, "actual": a_cell, "expected": e_cell})
                if len(cell_diffs) >= _MAX_CELL_DIFFS:
                    break
            if len(cell_diffs) >= _MAX_CELL_DIFFS:
                break
        if cell_diffs:
            table_diffs.append({"table_id": table_id, "cell_diffs": cell_diffs, "truncated": len(cell_diffs) >= _MAX_CELL_DIFFS})

    identical = not (added or missing or text_changed or reordered or moved or table_diffs)
    summary_lines = []
    if identical:
        summary_lines.append("Documents are identical at block and table level.")
    else:
        for label, items in (
            ("blocks only in actual", added),
            ("blocks missing from actual", missing),
            ("blocks with changed text", text_changed),
            ("blocks reordered", reordered),
            ("blocks moved (bbox)", moved),
            ("tables with differences", table_diffs),
        ):
            if items:
                summary_lines.append(f"{len(items)} {label}")

    return {
        "status": "ok",
        "identical": identical,
        "summary": summary_lines,
        "blocks": {
            "added": added,
            "missing": missing,
            "text_changed": text_changed,
            "reordered": reordered,
            "moved": moved,
        },
        "tables": table_diffs,
        "counts": {
            "actual_blocks": len(actual_blocks),
            "expected_blocks": len(expected_blocks),
            "actual_chunks": len(actual.get("chunks", []) or []),
            "expected_chunks": len(expected.get("chunks", []) or []),
        },
    }
