"""Encoding utilities for ANSI/Unicode safe file handling."""

from __future__ import annotations

from pathlib import Path

from documa.core.errors import DocumaErrorDetail, EncodingDetectionError
from documa.core.text_normalization import remove_utf8_bom


COMMON_TEXT_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "cp950",
    "big5",
    "gb18030",
    "cp1252",
)


def decode_text_bytes(
    data: bytes,
    encodings: tuple[str, ...] = COMMON_TEXT_ENCODINGS,
    *,
    allow_surrogateescape: bool = False,
) -> tuple[str, str]:
    """Decode bytes using common Unicode and ANSI encodings.

    Returns a tuple of ``(text, encoding_used)``. By default the function refuses
    lossy replacement, because silent U+FFFD output would damage evidence.
    """

    for encoding in encodings:
        try:
            text = data.decode(encoding, errors="strict")
            return remove_utf8_bom(text), encoding
        except UnicodeDecodeError:
            continue

    if allow_surrogateescape:
        return data.decode("utf-8", errors="surrogateescape"), "utf-8-surrogateescape"

    raise EncodingDetectionError(
        DocumaErrorDetail(
            code="TEXT_ENCODING_UNDETECTED",
            message="Unable to decode text bytes without data loss.",
            recoverable=True,
            suggested_action="Provide an explicit encoding or allow surrogateescape.",
        )
    )


def read_text(path: str | Path, *, encoding: str | None = None) -> tuple[str, str]:
    """Read a text file and return ``(text, encoding_used)``."""

    file_path = Path(path)
    if encoding:
        return remove_utf8_bom(file_path.read_text(encoding=encoding)), encoding
    return decode_text_bytes(file_path.read_bytes())


def write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text with an explicit encoding and LF newlines."""

    Path(path).write_text(text, encoding=encoding, newline="\n")

