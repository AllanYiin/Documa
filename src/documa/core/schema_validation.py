"""Build the Documa IR JSON Schema from the dataclasses and validate payloads.

The dataclasses in ``documa.core.ir`` are the single source of truth:
``build_documa_schema()`` derives the JSON Schema by reflection, so the schema
can never drift from the code. ``validate_document_payload()`` backs the
``documa validate-ir`` CLI command; violations carry JSON-pointer paths and a
nesting-depth limit rejects hostile inputs before recursive validation runs.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from enum import Enum
from typing import Any

from documa.core import ir as ir_module
from documa.core.ir import DocumentIR
from documa.core.language import LanguageHint

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
KNOWN_MAJOR_VERSIONS = {"0"}
MAX_NESTING_DEPTH = 100

_PRIMITIVES: dict[type, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


def _type_to_schema(annotation: Any, defs: dict[str, Any]) -> dict[str, Any]:
    if annotation is Any:
        return {}
    if annotation is type(None):
        return {"type": "null"}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"type": "string", "enum": [member.value for member in annotation]}
    if isinstance(annotation, type) and annotation in _PRIMITIVES:
        return dict(_PRIMITIVES[annotation])
    if dataclasses.is_dataclass(annotation):
        name = annotation.__name__
        if name not in defs:
            defs[name] = {}  # reserve first to break recursion cycles
            defs[name] = _dataclass_to_schema(annotation, defs)
        return {"$ref": f"#/$defs/{name}"}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        variants = [_type_to_schema(arg, defs) for arg in args]
        return {"anyOf": variants}
    if origin is list:
        item = _type_to_schema(args[0], defs) if args else {}
        return {"type": "array", "items": item}
    if origin is tuple:
        if args and args[-1] is Ellipsis:
            return {"type": "array", "items": _type_to_schema(args[0], defs)}
        return {
            "type": "array",
            "prefixItems": [_type_to_schema(arg, defs) for arg in args],
            "minItems": len(args),
            "maxItems": len(args),
        }
    if origin is dict:
        return {"type": "object"}
    raise TypeError(f"Unsupported annotation in IR dataclasses: {annotation!r}")


def _dataclass_to_schema(cls: type, defs: dict[str, Any]) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field_info in dataclasses.fields(cls):
        properties[field_info.name] = _type_to_schema(hints[field_info.name], defs)
        has_default = (
            field_info.default is not dataclasses.MISSING
            or field_info.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        )
        if not has_default:
            required.append(field_info.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def build_documa_schema() -> dict[str, Any]:
    """Derive the full IR JSON Schema from the DocumentIR dataclass tree."""
    defs: dict[str, Any] = {}
    root = _dataclass_to_schema(DocumentIR, defs)
    # Touch LanguageHint explicitly: it lives outside ir.py but inside the tree.
    _type_to_schema(LanguageHint, defs)
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": "https://github.com/AllanYiin/Documa/schema/documa.schema.json",
        "$comment": (
            "Generated from src/documa/core/ir.py by scripts/generate_schema.py - do not edit by hand. "
            "Compatibility contract: ir_version follows semver; minor bumps are strictly additive "
            "(consumers must ignore unknown fields); removing or retyping fields requires a major bump."
        ),
        "title": "DocumaDocumentIR",
        "ir_version": ir_module.DocumentIR("", "").ir_version,
        **root,
        "$defs": defs,
    }


def _measure_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_NESTING_DEPTH:
        return depth
    if isinstance(value, dict):
        return max((_measure_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_measure_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _semantic_violations(payload: dict[str, Any]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []

    version = str(payload.get("ir_version", ""))
    major = version.split(".", 1)[0] if version else ""
    if major not in KNOWN_MAJOR_VERSIONS:
        violations.append(
            {
                "path": "/ir_version",
                "message": f"Unknown ir_version major '{version}'; this validator understands majors {sorted(KNOWN_MAJOR_VERSIONS)}.",
            }
        )

    def check_bbox(bbox: Any, path: str) -> None:
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            x0, y0, x1, y1 = bbox
            if x1 < x0 or y1 < y0:
                violations.append({"path": path, "message": "bbox is inverted: expected x0 <= x1 and y0 <= y1."})

    for p, page in enumerate(payload.get("pages", []) or []):
        if not isinstance(page, dict):
            continue
        if isinstance(page.get("page_number"), int) and page["page_number"] < 1:
            violations.append({"path": f"/pages/{p}/page_number", "message": "page_number must be >= 1."})
        for b, block in enumerate(page.get("blocks", []) or []):
            if isinstance(block, dict):
                if isinstance(block.get("page_number"), int) and block["page_number"] < 1:
                    violations.append(
                        {"path": f"/pages/{p}/blocks/{b}/page_number", "message": "page_number must be >= 1."}
                    )
                check_bbox(block.get("bbox"), f"/pages/{p}/blocks/{b}/bbox")
        for i, image in enumerate(page.get("images", []) or []):
            if isinstance(image, dict):
                check_bbox(image.get("bbox"), f"/pages/{p}/images/{i}/bbox")

    return violations


def validate_document_payload(payload: Any) -> dict[str, Any]:
    """Validate an IR payload; returns {"valid": bool, "violations": [...]}."""
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "violations": [{"path": "", "message": "IR payload must be a JSON object."}],
        }
    if _measure_depth(payload) > MAX_NESTING_DEPTH:
        return {
            "valid": False,
            "violations": [{"path": "", "message": f"Maximum nesting depth exceeded ({MAX_NESTING_DEPTH})."}],
        }

    import jsonschema

    validator = jsonschema.Draft202012Validator(build_documa_schema())
    violations = [
        {
            "path": "/" + "/".join(str(part) for part in error.absolute_path),
            "message": error.message,
        }
        for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    ]
    violations.extend(_semantic_violations(payload))
    return {"valid": not violations, "violations": violations}
