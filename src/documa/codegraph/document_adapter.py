"""Project local DocumentIR blocks and explicit references into a repository graph."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from documa.adapters.registry import adapter_for_source
from documa.codegraph.models import (
    CodeEdge, CodeEdgeType, CodeNode, CodeNodeKind, CodeOccurrence, CodeSpan, EdgeResolution,
    ParseStatus, ParsedCodeFile, edge_id, sha256_bytes, sha256_text, stable_id,
)
from documa.core.errors import DocumaError
from documa.core.ir import BlockIR, BlockType, DocumentIR
from documa.core.serialization import document_from_plain_data


_LINK = re.compile(r"(?<!!)\[[^\]\n]+\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+[^\n)]*)?\)")
_INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_NAME = re.compile(r"[^\W\d]\w*(?:\.[^\W\d]\w*)*", re.UNICODE)


def is_repository_markdown(relative: str) -> bool:
    path = PurePosixPath(relative.casefold())
    return path.suffix in {".md", ".markdown", ".mdp"} and (
        path.stem.startswith(("readme", "adr-", "rfc-", "spec-"))
        or bool(set(path.parts[:-1]) & {"adr", "adrs", "rfc", "rfcs", "spec", "specs"})
    )


def read_local_document(path: Path) -> DocumentIR:
    if path.name.casefold().endswith(".ir.json"):
        return document_from_plain_data(json.loads(path.read_text(encoding="utf-8")))
    return adapter_for_source(path).parse(path)


def block_body(block: BlockIR) -> str:
    if block.text:
        return block.text.raw_text
    return "\n".join(span.text.raw_text for span in block.spans)


def _references(body: str, locator: str) -> Iterator[tuple[str, dict]]:
    fence = ""
    prose = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
        elif stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        else:
            prose.append(line)
    body = "\n".join(prose)
    for link in _LINK.finditer(body):
        destination = link.group(1).strip("<>")
        try:
            url = urlsplit(destination)
        except ValueError:
            continue
        if url.scheme or url.netloc or not url.path or url.path.startswith(("/", "\\")):
            continue
        relative = posixpath.normpath(posixpath.join(posixpath.dirname(locator), unquote(url.path)))
        if relative == ".." or relative.startswith(("../", "/")) or "\\" in relative:
            continue
        yield destination, {"targetPath": relative, "anchor": unquote(url.fragment)}
    for match in _INLINE_CODE.finditer(body):
        name = match.group(1).strip().removesuffix("()")
        if _NAME.fullmatch(name):
            yield name, {"targetName": name}


class RepositoryDocumentAdapter:
    name = "repository-document"
    version = "document-ir-v1"

    def parse(self, root: Path, path: Path, workspace_id: str) -> ParsedCodeFile:
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        digest = sha256_bytes(raw)
        file_id = stable_id("cf", workspace_id, relative)
        result = ParsedCodeFile(file_id, relative, "document", digest, ParseStatus.OK)
        try:
            document = read_local_document(path)
        except (DocumaError, ValueError, OSError) as exc:
            result.parse_status = ParseStatus.UNAVAILABLE
            result.error_code = "CODE_DOCUMENT_UNAVAILABLE"
            result.error_message = str(exc)
            return result

        result.nodes.append(CodeNode(
            file_id, CodeNodeKind.FILE, relative, path.name, relative, digest,
            file_id=file_id, metadata={"language": "document", "parser": document.parser},
        ))
        markdown_lines = raw.decode("utf-8").splitlines() if document.parser == "markdown" and path.suffix != ".json" else []
        heading = path.stem
        for page in document.pages:
            for block in page.blocks:
                body = block_body(block)
                if not body.strip():
                    continue
                span = None
                if markdown_lines and {"line_start", "line_end"} <= block.metadata.keys():
                    span = CodeSpan(int(block.metadata["line_start"]), int(block.metadata["line_end"]))
                    body = "\n".join(markdown_lines[span.start_line - 1:span.end_line])
                if block.type == BlockType.HEADING and body.lstrip("# ").strip():
                    heading = body.lstrip("# ").splitlines()[0]
                node_id = stable_id("cd", workspace_id, relative, block.id)
                result.nodes.append(CodeNode(
                    node_id, CodeNodeKind.DOCUMENT_BLOCK, f"{relative}#{block.id}", heading, relative,
                    sha256_text(body), span=span, file_id=file_id, parent_id=file_id, summary=body[:320],
                    metadata={"language": "document", "blockId": block.id, "page": page.page_number,
                              "blockType": block.type.value, "sourceRefs": block.source_refs},
                ))
                result.structural_edges.append(CodeEdge(
                    edge_id(file_id, node_id, CodeEdgeType.CONTAINS), file_id, node_id, CodeEdgeType.CONTAINS,
                    EdgeResolution.EXACT, self.version, evidence_file_id=file_id, evidence_span=span, evidence_hash=digest,
                ))
                for text, target in _references(body, relative):
                    occurrence_id = stable_id("co", node_id, "document", text)
                    result.occurrences.append(CodeOccurrence(
                        occurrence_id, file_id, node_id, "document", span or CodeSpan(1, 1), text,
                        {**target, "sourceDigest": digest, "sourceLocator": relative, "indexedBlockId": block.id,
                         "hasLineSpan": span is not None},
                    ))
        return result
