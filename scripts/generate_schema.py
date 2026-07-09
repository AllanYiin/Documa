"""Generate schema/documa.schema.json from the DocumentIR dataclass definitions.

The dataclasses in ``documa.core.ir`` are the single source of truth; the
schema is derived by ``documa.core.schema_validation.build_documa_schema``.
``--check`` regenerates in memory and exits non-zero if the committed file is
out of sync (enforced in CI). Never edit documa.schema.json by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from documa.core.schema_validation import build_documa_schema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "documa.schema.json"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    generated = build_documa_schema()

    if "--check" in args:
        if not SCHEMA_PATH.exists():
            print(f"generate_schema --check: FAILED - {SCHEMA_PATH} is missing; run scripts/generate_schema.py.")
            return 1
        committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        if committed != generated:
            print(
                "generate_schema --check: FAILED - schema/documa.schema.json is out of sync with "
                "src/documa/core/ir.py; run scripts/generate_schema.py and commit the result."
            )
            return 1
        print("generate_schema --check: OK - schema is in sync.")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Schema written to {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
