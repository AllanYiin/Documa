"""Table structure scoring: TableIR-to-HTML-tree conversion and TEDS / TEDS-S.

TEDS = 1 - TreeEditDist(T_actual, T_gold) / max(|T_actual|, |T_gold|), computed
over table -> tr -> td trees. TEDS-S ignores cell text (structure only).

Implementation notes: the edit distance here is a memoized top-down ordered
tree edit distance (rows match rows, cells match cells), which is exact for
the fixed-depth trees tables produce; rename cost for same-label cells is 1
when texts differ, 0 otherwise. Quality metrics operate on IR data only and
must not import pipeline internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any


@dataclass(slots=True)
class TableTreeNode:
    label: str
    text: str | None = None
    children: list["TableTreeNode"] = field(default_factory=list)


def _normalize_cell(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split())


def table_tree_from_rows(rows: list[list[Any]]) -> TableTreeNode:
    """Build the table->tr->td tree from TableIR-style row grids."""
    table = TableTreeNode("table")
    for row in rows or []:
        tr = TableTreeNode("tr")
        for cell in row:
            tr.children.append(TableTreeNode("td", text=_normalize_cell(cell)))
        table.children.append(tr)
    return table


class _TableHtmlParser(HTMLParser):
    """Minimal stdlib parser for gold table HTML (td/th; thead/tbody flattened).

    Collects per-row cells with colspan/rowspan so the tree builder can expand
    them into the flat grid convention used by the PDF table extractor:
    merged-cell content lives in the top-left covered position, all other
    covered positions are empty cells (verified against PyMuPDF output on
    fixtures/pdf/real/merged-cells-report.pdf).
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_spans: tuple[int, int] = (1, 1)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            attributes = dict(attrs)
            self._cell_spans = (
                max(int(attributes.get("colspan", 1) or 1), 1),
                max(int(attributes.get("rowspan", 1) or 1), 1),
            )
            self._cell_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell_text is not None and self._row is not None:
            self._row.append(("".join(self._cell_text), *self._cell_spans))
            self._cell_text = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)


def _expand_spans_to_grid(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Expand colspan/rowspan cells into the flat extractor grid convention."""
    grid: list[list[str | None]] = []
    pending: dict[tuple[int, int], str] = {}  # (row, col) -> "" placeholders from rowspans

    for r, row in enumerate(rows):
        grid_row: list[str | None] = []
        col = 0
        cells = iter(row)
        while True:
            while (r, col) in pending:
                grid_row.append(pending.pop((r, col)))
                col += 1
            cell = next(cells, None)
            if cell is None:
                break
            text, colspan, rowspan = cell
            grid_row.append(text)
            for extra_row in range(1, rowspan):
                pending[(r + extra_row, col)] = ""
            col += 1
            for _ in range(1, colspan):
                while (r, col) in pending:
                    grid_row.append(pending.pop((r, col)))
                    col += 1
                grid_row.append("")
                for extra_row in range(1, rowspan):
                    pending[(r + extra_row, col)] = ""
                col += 1
        while (r, col) in pending:
            grid_row.append(pending.pop((r, col)))
            col += 1
        grid.append(grid_row)
    return grid


def table_tree_from_html(html: str) -> TableTreeNode:
    parser = _TableHtmlParser()
    parser.feed(html or "")
    table = TableTreeNode("table")
    for grid_row in _expand_spans_to_grid(parser.rows):
        tr = TableTreeNode("tr")
        for cell in grid_row:
            tr.children.append(TableTreeNode("td", text=_normalize_cell(cell)))
        table.children.append(tr)
    return table


def tree_size(node: TableTreeNode) -> int:
    return 1 + sum(tree_size(child) for child in node.children)


def _rename_cost(a: TableTreeNode, b: TableTreeNode, structure_only: bool) -> int:
    if a.label != b.label:
        return 1
    if structure_only:
        return 0
    return 0 if _normalize_cell(a.text) == _normalize_cell(b.text) else 1


def _tree_dist(a: TableTreeNode, b: TableTreeNode, structure_only: bool, memo: dict) -> int:
    key = (id(a), id(b))
    if key in memo:
        return memo[key]
    cost = _rename_cost(a, b, structure_only) + _forest_dist(a.children, b.children, structure_only, memo)
    memo[key] = cost
    return cost


def _forest_dist(
    forest_a: list[TableTreeNode],
    forest_b: list[TableTreeNode],
    structure_only: bool,
    memo: dict,
) -> int:
    rows = len(forest_a) + 1
    cols = len(forest_b) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        table[i][0] = table[i - 1][0] + tree_size(forest_a[i - 1])
    for j in range(1, cols):
        table[0][j] = table[0][j - 1] + tree_size(forest_b[j - 1])
    for i in range(1, rows):
        for j in range(1, cols):
            table[i][j] = min(
                table[i - 1][j] + tree_size(forest_a[i - 1]),
                table[i][j - 1] + tree_size(forest_b[j - 1]),
                table[i - 1][j - 1] + _tree_dist(forest_a[i - 1], forest_b[j - 1], structure_only, memo),
            )
    return table[-1][-1]


def teds(actual: TableTreeNode, gold: TableTreeNode, *, structure_only: bool = False) -> float:
    """Tree-Edit-Distance-based Similarity in [0, 1]; 1.0 means identical."""
    denominator = max(tree_size(actual), tree_size(gold))
    if denominator <= 1:
        return 1.0
    distance = _tree_dist(actual, gold, structure_only, {})
    return max(0.0, 1.0 - distance / denominator)


def score_table(actual_rows: list[list[Any]], gold_html: str) -> dict[str, float]:
    """Score a TableIR row grid against gold HTML; returns teds and teds_s."""
    actual_tree = table_tree_from_rows(actual_rows)
    gold_tree = table_tree_from_html(gold_html)
    return {
        "teds": round(teds(actual_tree, gold_tree, structure_only=False), 4),
        "teds_s": round(teds(actual_tree, gold_tree, structure_only=True), 4),
    }
