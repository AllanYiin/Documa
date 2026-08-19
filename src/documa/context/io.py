"""UTF-8 serialization for disposable ContextIR projections."""

from __future__ import annotations

import json
from pathlib import Path

from documa.context.models import ContextIR, context_ir_from_plain_data, context_ir_to_plain_data


def load_context_ir(path: str | Path) -> ContextIR:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ContextIR root must be an object.")
    return context_ir_from_plain_data(data)


def save_context_ir(context: ContextIR, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(context_ir_to_plain_data(context), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output
