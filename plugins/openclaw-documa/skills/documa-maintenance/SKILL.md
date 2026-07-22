---
name: documa-maintenance
description: Maintain, diagnose, benchmark, validate, migrate, or release Documa; not for ordinary evidence questions.
---

# Documa Maintenance Workflow

Use the admin tool profile. Diagnose with `documa_doctor` and `documa_inspect_store`, repair derived collection indexes with `documa_index_collection`, validate IR with `documa_validate_ir`, and run `documa_benchmark` before release claims. Sidecars are disposable derived state and must be rebuilt on generation or version mismatch. Report final gate status, exact validation evidence, and unresolved risks.
