"""Optional adapter for already-decoded SCIP index payloads.

Documa deliberately does not vendor a protobuf runtime into the base install.
Callers may decode ``index.scip`` with their preferred SCIP binding and pass
the resulting mapping through this adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from documa.codegraph.models import CodeNode, CodeNodeKind, CodeSpan, stable_id


class ScipIndexAdapter:
    name = "scip-decoded-v1"
    version = "1"

    def symbols(self, payload: Mapping[str, Any], workspace_id: str) -> list[CodeNode]:
        """Map decoded SCIP documents/symbol metadata into CodeGraph nodes."""

        output: list[CodeNode] = []
        seen: set[str] = set()
        for document in payload.get("documents", []) or []:
            relative_path = str(document.get("relative_path") or document.get("relativePath") or "")
            file_id = stable_id("cf", workspace_id, relative_path)
            for item in document.get("symbols", []) or []:
                symbol = str(item.get("symbol") or "")
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                documentation = item.get("documentation") or []
                docstring = "\n\n".join(str(value) for value in documentation) if documentation else None
                kind_name = str(item.get("kind") or "symbol").casefold()
                kind = (
                    CodeNodeKind.CLASS
                    if "class" in kind_name or "struct" in kind_name or "interface" in kind_name
                    else CodeNodeKind.METHOD
                    if "method" in kind_name
                    else CodeNodeKind.FUNCTION
                )
                output.append(
                    CodeNode(
                        node_id=stable_id("cs", workspace_id, "scip", symbol),
                        kind=kind,
                        qualified_name=symbol,
                        display_name=symbol.rstrip("#.)").rsplit("/", 1)[-1],
                        source_locator=relative_path or None,
                        content_hash=None,
                        file_id=file_id if relative_path else None,
                        docstring=docstring,
                        summary=(docstring.split("\n\n", 1)[0] if docstring else f"SCIP symbol {symbol}."),
                        metadata={"adapter": self.name, "scipKind": item.get("kind")},
                    )
                )
        return output

    @staticmethod
    def occurrence_span(occurrence: Mapping[str, Any]) -> CodeSpan | None:
        """Read SCIP's legacy packed range or a decoded typed range."""

        values = occurrence.get("range")
        if isinstance(values, list) and len(values) == 3:
            return CodeSpan(int(values[0]) + 1, int(values[0]) + 1, int(values[1]), int(values[2]))
        if isinstance(values, list) and len(values) >= 4:
            return CodeSpan(int(values[0]) + 1, int(values[2]) + 1, int(values[1]), int(values[3]))
        typed = occurrence.get("single_line_range") or occurrence.get("singleLineRange")
        if isinstance(typed, Mapping):
            return CodeSpan(
                int(typed.get("line", 0)) + 1,
                int(typed.get("line", 0)) + 1,
                int(typed.get("start_character", typed.get("startCharacter", 0))),
                int(typed.get("end_character", typed.get("endCharacter", 0))),
            )
        return None
