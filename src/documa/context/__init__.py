"""Cross-source ContextIR projection and graph-guided evidence runtime."""

from documa.context.adapters import context_from_code, context_from_document, context_from_skill
from documa.context.io import load_context_ir, save_context_ir
from documa.context.models import (
    CONTEXT_IR_VERSION,
    ContextAuthority,
    ContextBlock,
    ContextIR,
    ContextRelation,
    ContextSourceKind,
    RelationOrigin,
    SourceSpan,
    context_ir_from_plain_data,
    context_ir_to_plain_data,
    sha256_text,
)
from documa.context.service import ContextContractError, ContextService

__all__ = [
    "CONTEXT_IR_VERSION",
    "ContextAuthority",
    "ContextBlock",
    "ContextContractError",
    "ContextIR",
    "ContextRelation",
    "ContextService",
    "ContextSourceKind",
    "RelationOrigin",
    "SourceSpan",
    "context_from_code",
    "context_from_document",
    "context_from_skill",
    "context_ir_from_plain_data",
    "context_ir_to_plain_data",
    "load_context_ir",
    "save_context_ir",
    "sha256_text",
]
