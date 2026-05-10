"""Typed errors for Documa core operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DocumaErrorDetail:
    """Structured error payload suitable for CLI, MCP, and tool-calling output."""

    code: str
    message: str
    recoverable: bool = False
    suggested_action: str | None = None
    context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.suggested_action:
            data["suggested_action"] = self.suggested_action
        if self.context:
            data["context"] = self.context
        return data


class DocumaError(Exception):
    """Base exception that preserves a structured error detail."""

    def __init__(self, detail: DocumaErrorDetail):
        super().__init__(detail.message)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.detail.to_dict()}


class EncodingDetectionError(DocumaError):
    """Raised when bytes cannot be decoded without data loss."""

