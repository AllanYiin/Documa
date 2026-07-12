# Migration Governance

This skill is not currently being renamed, merged, split, deprecated, or retired.

## Rename

Current name: `documa-evidence`.

If the skill is renamed, keep a compatibility note for users who know the old name, update the frontmatter `name`, update trigger eval prompts, and record the old-to-new mapping in `references/readiness_report.md`.

## Deprecate

Do not deprecate this skill while `plugins/codex-documa` still exposes Documa MCP document-understanding tools as its primary user workflow.

If deprecation becomes necessary, add replacement routing, update negative trigger evals, and run the revise stage gate before announcing the change.

## Merge

Do not merge this skill with visual PDF rendering, generic long-document reading, or host-specific OpenClaw/Claude wrappers. Those workflows have different tool boundaries.

A merge is allowed only if another skill has the same primary job: Codex plus Documa MCP evidence retrieval.

## Split

Split only if the workflow grows into separate primary jobs, such as one skill for Documa MCP document QA and another for Documa store administration.

A split must preserve direct, indirect, negative, near-miss, and overlap-neighbor eval coverage for each resulting skill.

## Compatibility

Compatibility policy:

- Keep `documa-evidence` stable for existing Codex plugin users.
- Keep `plugins/codex-documa` as the Codex host wrapper boundary.
- Keep Documa core outside the skill folder.
- Keep document evidence claims grounded in Documa block ids and source/page metadata.

## Migration Evidence

Before claiming any migration is complete, update:

- `SKILL.md`
- `skill_lifecycle.yaml`
- `assets/evals/evals.json`
- `assets/evals/regression_gates.json`
- `references/readiness_report.md`
- `references/migration-governance.md`

Required checks:

```powershell
python scripts\validate_agent_plugins.py
python C:\Users\allan\.agents\skills\skill-creator-advanced\scripts\stage_gate.py D:\PycharmProjects\Documa\plugins\codex-documa\skills\documa-evidence --stage revise --json
```

Do not claim a migration is complete if any final gate, stage gate, or policy gate returns FAIL or BLOCKED.
