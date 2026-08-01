"""Jupyter notebook parser adapter."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from documa.adapters.base import ParseOptions, ParserAdapter
from documa.core.errors import DocumaError, DocumaErrorDetail
from documa.core.ir import BlockIR, BlockType, Confidence, DocumentIR, PageIR, TextContent
from documa.storage.assets import AssetStore, safe_asset_name


def _load_nbformat():
    try:
        import nbformat  # type: ignore

        return nbformat
    except ImportError as exc:
        raise DocumaError(
            DocumaErrorDetail(
                code="IPYNB_DEPENDENCY_NOT_INSTALLED",
                message="nbformat is required for IpynbAdapter.",
                recoverable=True,
                suggested_action="Install or repair the standard runtime: pip install --upgrade documa",
            )
        ) from exc


def _document_id(source_path: Path, size: int) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}\n{size}".encode("utf-8")).hexdigest()[:16]
    return f"doc_ipynb_{digest}"


def _cell_source(cell: Any) -> str:
    source = getattr(cell, "source", "")
    if isinstance(source, list):
        return "".join(str(item) for item in source)
    return str(source or "")


def _first_markdown_heading(source: str) -> tuple[str | None, int | None]:
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if 1 <= hashes <= 6 and stripped[hashes : hashes + 1] == " ":
            title = stripped[hashes:].strip()
            return (title or None, hashes)
    return None, None


def _output_text(output: Any) -> str | None:
    output_type = str(getattr(output, "output_type", "") or "")
    if output_type == "stream":
        text = getattr(output, "text", "")
        return "".join(text) if isinstance(text, list) else str(text or "")
    data = getattr(output, "data", None)
    if isinstance(data, dict):
        value = data.get("text/plain")
        if isinstance(value, list):
            return "".join(str(item) for item in value)
        if value is not None:
            return str(value)
    text = getattr(output, "text", None)
    if text is not None:
        return "".join(text) if isinstance(text, list) else str(text)
    return None


def _attachment_bytes(encoded: Any) -> bytes:
    if isinstance(encoded, list):
        encoded = "".join(str(item) for item in encoded)
    if isinstance(encoded, bytes):
        encoded = encoded.decode("ascii", errors="ignore")
    return base64.b64decode(str(encoded or ""), validate=False)


class IpynbAdapter(ParserAdapter):
    """Parse Jupyter notebooks into cell-order Documa IR blocks."""

    name = "ipynb"

    def parse(self, source: str | Path, options: ParseOptions | None = None) -> DocumentIR:
        options = options or ParseOptions()
        source_path = Path(source)
        nbformat = _load_nbformat()
        asset_store = AssetStore(options.asset_dir) if options.asset_dir else None

        try:
            notebook = nbformat.read(str(source_path), as_version=4)
        except Exception as exc:
            raise DocumaError(
                DocumaErrorDetail(
                    code="IPYNB_OPEN_FAILED",
                    message=f"Unable to open notebook: {source_path}",
                    recoverable=True,
                    suggested_action="Check whether the file exists and is a valid .ipynb file.",
                    context={"source": str(source_path), "error": str(exc)},
                )
            ) from exc

        metadata = dict(getattr(notebook, "metadata", {}) or {})
        size = source_path.stat().st_size if source_path.exists() else 0
        document = DocumentIR(
            id=_document_id(source_path, size),
            source_name=str(source_path),
            parser=self.name,
            metadata={
                "adapter": self.name,
                "format": "ipynb",
                "languages": list(options.languages),
                "page_model": "logical_notebook",
                "nbformat": getattr(notebook, "nbformat", None),
                "nbformat_minor": getattr(notebook, "nbformat_minor", None),
                "kernelspec": metadata.get("kernelspec"),
                "language_info": metadata.get("language_info"),
                "attachments": [],
            },
        )
        page = PageIR(
            id="page_1",
            page_number=1,
            width=1.0,
            height=0.0,
            metadata={"source": "ipynb_cells", "page_model": "logical_notebook"},
        )
        document.pages.append(page)

        order = 0
        for cell_index, cell in enumerate(getattr(notebook, "cells", []) or [], start=1):
            source_text = _cell_source(cell).strip()
            if not source_text:
                continue
            order += 1
            cell_type = str(getattr(cell, "cell_type", "") or "unknown")
            block_type = BlockType.PARAGRAPH
            heading_title, heading_level = _first_markdown_heading(source_text) if cell_type == "markdown" else (None, None)
            if heading_title and heading_level is not None:
                block_type = BlockType.HEADING

            cell_attachments = self._extract_cell_attachments(cell, cell_index, asset_store)
            if cell_attachments:
                document.metadata["attachments"].extend(cell_attachments)

            metadata_payload: dict[str, Any] = {
                "source_type": "ipynb_cell",
                "cell_type": cell_type,
                "cell_index": cell_index,
                "cell_id": getattr(cell, "id", None),
                "attachments": cell_attachments,
            }
            if heading_level is not None:
                metadata_payload["heading_level"] = heading_level
            if cell_type == "code":
                outputs = [_output_text(output) for output in getattr(cell, "outputs", []) or []]
                outputs = [item.strip() for item in outputs if item and item.strip()]
                metadata_payload["execution_count"] = getattr(cell, "execution_count", None)
                metadata_payload["output_count"] = len(getattr(cell, "outputs", []) or [])
                metadata_payload["outputs_preview"] = outputs[:3]

            page.blocks.append(
                BlockIR(
                    id=f"ipynb_cell_{cell_index:04d}",
                    type=block_type,
                    page_number=1,
                    text=TextContent(source_text),
                    confidence=Confidence.HIGH,
                    order_index=order,
                    source_refs=[f"ipynb:cell:{cell_index}"],
                    metadata=metadata_payload,
                )
            )

        page.height = float(max(1, order))
        document.metadata["cell_count"] = len(getattr(notebook, "cells", []) or [])
        return document

    def _extract_cell_attachments(
        self,
        cell: Any,
        cell_index: int,
        asset_store: AssetStore | None,
    ) -> list[dict[str, Any]]:
        attachments = []
        raw_attachments = getattr(cell, "attachments", None) or {}
        if not isinstance(raw_attachments, dict):
            return attachments

        attachment_index = 0
        for filename, media_items in raw_attachments.items():
            if not isinstance(media_items, dict):
                continue
            for media_type, encoded in media_items.items():
                attachment_index += 1
                data = _attachment_bytes(encoded)
                asset_ref = None
                if asset_store is not None:
                    asset_ref = asset_store.write_bytes(
                        f"attachments/ipynb_cell_{cell_index:04d}_{attachment_index:04d}_{safe_asset_name(str(filename))}",
                        data,
                    )
                attachments.append(
                    {
                        "cell_index": cell_index,
                        "filename": str(filename),
                        "content_type": str(media_type),
                        "size": len(data),
                        "asset_ref": asset_ref,
                    }
                )
        return attachments
