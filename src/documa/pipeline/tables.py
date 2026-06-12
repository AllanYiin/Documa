"""Table normalization stage.

Stage 3 does not attempt full table discovery from arbitrary geometry. It
normalizes table candidates emitted by adapters or previous stages into TableIR
objects while preserving low-confidence evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from documa.core.ir import BlockType, Confidence, DocumentIR, TableIR
from documa.pipeline.base import PipelineContext, PipelineStage, StageResult


def rows_to_markdown(rows: list[list[str | None]]) -> str:
    """Convert rows into a simple GitHub-flavored Markdown table."""

    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    separator = ["---"] * width
    body = normalized[1:] or [[""] * width]

    def render_cell(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", "<br>")

    def render(row: list[str]) -> str:
        return "| " + " | ".join(render_cell(cell) for cell in row) + " |"

    return "\n".join([render(header), render(separator), *[render(row) for row in body]])


@dataclass(slots=True)
class TableNormalizationStage(PipelineStage):
    """Normalize adapter-provided table candidates into TableIR."""

    name: str = "table_normalization"

    def run(self, document: DocumentIR, context: PipelineContext | None = None) -> StageResult:
        existing = {table.block_id for table in document.tables}
        created = 0

        for page in document.pages:
            for block in page.blocks:
                if block.id in existing:
                    continue
                candidate_rows = block.metadata.get("table_rows")
                if not candidate_rows:
                    continue
                rows = [[None if cell is None else str(cell) for cell in row] for row in candidate_rows]
                markdown = rows_to_markdown(rows)
                table = TableIR(
                    id=f"table_{block.id}",
                    block_id=block.id,
                    rows=rows,
                    markdown=markdown,
                    confidence=Confidence.MEDIUM,
                    metadata={
                        "source": "block_metadata_table_rows",
                        "source_page": page.page_number,
                        "source_block_id": block.id,
                    },
                )
                document.tables.append(table)
                block.type = BlockType.TABLE
                block.confidence = Confidence.MEDIUM
                block.metadata["table_id"] = table.id
                existing.add(block.id)
                created += 1

        return StageResult(
            document=document,
            stage_name=self.name,
            changed=created > 0,
            report={"tables_created": created},
        )
