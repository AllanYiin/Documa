"""Shared contract for native parser bindings owned by the Documa distribution.

This module deliberately stops at the binding boundary. Format-aware parsing and
IR mapping stay in their dedicated Rust cores and Documa adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from documa.core.errors import DocumaError, DocumaErrorDetail


@dataclass(frozen=True, slots=True)
class NativeBindingSpec:
    """Expected identity and callable surface for one bundled native parser."""

    parser_id: str
    module_name: str
    identity_labels: tuple[str, ...]
    expected_identity: tuple[str | None, ...]
    required_calls: tuple[str, ...]
    not_installed_code: str
    incompatible_code: str
    suggested_action: str

    def __post_init__(self) -> None:
        if len(self.identity_labels) != len(self.expected_identity):
            raise ValueError("native binding identity labels and values must align")


@dataclass(frozen=True, slots=True)
class NativeBinding:
    """Validated native module plus stable identity/capability metadata."""

    module: ModuleType
    identity: dict[str, str]
    capabilities: dict[str, Any] = field(default_factory=dict)


def _binding_error(
    spec: NativeBindingSpec,
    *,
    code: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> DocumaError:
    return DocumaError(
        DocumaErrorDetail(
            code=code,
            message=message,
            recoverable=True,
            suggested_action=spec.suggested_action,
            context=context,
        )
    )


def load_native_binding(spec: NativeBindingSpec) -> NativeBinding:
    """Import and validate a parser binding through one shared contract."""

    try:
        module = import_module(spec.module_name)
    except ImportError as exc:
        raise _binding_error(
            spec,
            code=spec.not_installed_code,
            message=f"The bundled {spec.parser_id} native binding is unavailable.",
            context={"module": spec.module_name, "error": str(exc)},
        ) from exc

    try:
        raw_identity = tuple(module.version_info())
    except (AttributeError, TypeError, ValueError) as exc:
        raise _binding_error(
            spec,
            code=spec.incompatible_code,
            message=f"The {spec.parser_id} binding does not expose its version contract.",
            context={"module": spec.module_name, "error": str(exc)},
        ) from exc

    if len(raw_identity) != len(spec.identity_labels):
        raise _binding_error(
            spec,
            code=spec.incompatible_code,
            message=f"The {spec.parser_id} binding returned an invalid version contract.",
            context={
                "expected_fields": list(spec.identity_labels),
                "actual_field_count": len(raw_identity),
            },
        )

    identity = {
        label: str(value)
        for label, value in zip(spec.identity_labels, raw_identity, strict=True)
    }
    mismatches = {
        label: {"expected": expected, "actual": identity[label]}
        for label, expected in zip(
            spec.identity_labels, spec.expected_identity, strict=True
        )
        if expected is not None and identity[label] != expected
    }
    missing_calls = [
        name for name in spec.required_calls if not callable(getattr(module, name, None))
    ]
    if mismatches or missing_calls:
        context: dict[str, Any] = {
            "identity": identity,
            "mismatches": mismatches,
            "missing_calls": missing_calls,
        }
        if len(mismatches) == 1:
            mismatch = next(iter(mismatches.values()))
            context.update(
                {"required": mismatch["expected"], "actual": mismatch["actual"]}
            )
        raise _binding_error(
            spec,
            code=spec.incompatible_code,
            message=f"The {spec.parser_id} binding does not match Documa's native parser contract.",
            context=context,
        )

    capabilities: dict[str, Any] = {}
    capability_call = getattr(module, "capabilities", None)
    if callable(capability_call):
        try:
            raw_capabilities = capability_call()
        except Exception as exc:
            raise _binding_error(
                spec,
                code=spec.incompatible_code,
                message=f"The {spec.parser_id} capability contract could not be read.",
                context={"error": str(exc)},
            ) from exc
        if not isinstance(raw_capabilities, dict):
            raise _binding_error(
                spec,
                code=spec.incompatible_code,
                message=f"The {spec.parser_id} capability contract must be an object.",
            )
        capabilities = raw_capabilities

    return NativeBinding(module=module, identity=identity, capabilities=capabilities)


def native_exception_to_documa(
    exc: Exception,
    *,
    source: str | Path,
    default_code: str,
    default_message: str,
    default_recoverable: bool,
    suggested_action: str,
) -> DocumaError:
    """Convert the shared JSON native-error envelope into ``DocumaError``."""

    try:
        raw_payload = json.loads(str(exc))
    except (TypeError, json.JSONDecodeError):
        raw_payload = {}
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_context = payload.get("context")
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    context = {"source": str(source), **context}
    return DocumaError(
        DocumaErrorDetail(
            code=str(payload.get("code") or default_code),
            message=str(payload.get("message") or default_message),
            recoverable=bool(payload.get("recoverable", default_recoverable)),
            suggested_action=suggested_action,
            context=context,
        )
    )


__all__ = [
    "NativeBinding",
    "NativeBindingSpec",
    "load_native_binding",
    "native_exception_to_documa",
]
