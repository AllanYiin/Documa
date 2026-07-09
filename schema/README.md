# schema/

`documa.schema.json` (generated in Stage 2) is the published JSON Schema for
Documa IR payloads. It is generated from the dataclasses in
`src/documa/core/ir.py` by `scripts/generate_schema.py` — never edit it by
hand. CI runs `python scripts/generate_schema.py --check` to enforce that the
committed schema matches the dataclass definitions.

Compatibility contract (semver on `ir_version`): minor versions are strictly
additive — existing consumers may ignore unknown fields; field removals, type
changes, or semantic changes require a major version bump.
