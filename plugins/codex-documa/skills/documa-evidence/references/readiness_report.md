# Documa Evidence Readiness Report

Audit date: 2026-08-02

Skill version: 2026.8.2

## Scope

This report covers the Codex `documa-evidence` workflow skill at `plugins/codex-documa/skills/documa-evidence`.

The skill is intended to route Codex document-understanding tasks through Documa MCP tools:

1. process a source document into Documa IR,
2. search/list blocks before reading full content,
3. read selected evidence blocks,
4. answer with block ids and source/page metadata when available.

## Current Release Claim

Draft readiness only. This skill does not claim published package readiness, benchmarked ROI, live integration quality, or cross-host performance.

Missing benchmark evidence is a limitation, not a publish blocker for draft review. Strict benchmark claims require `--require-benchmark` or `--require-live-benchmark` with a benchmark artifact.

## Mechanical Evidence

Latest intended validation commands:

```powershell
python scripts\validate_agent_plugins.py
python C:\Users\allan\.agents\skills\skillops-studio\scripts\release_gate.py D:\PycharmProjects\Documa\plugins\codex-documa\skills\documa-evidence --stage package --json
```

Latest executed package gate on 2026-08-02: `PASS`（11 eval cases；format/reference/orphan/security audits PASS；live benchmark SKIPPED and no benchmark claim made）。

Gate precedence rule:

- 任一 final gate、stage gate 或 policy gate 為 FAIL / BLOCKED 時，結論只能是 FAIL 或 BLOCKED。
- 局部 PASS 只可列在定位資訊，且必須明確標註不具放行效力。

## Known Limitations

- Live Documa MCP availability depends on the Codex plugin environment.
- Eval fixtures are static trigger and workflow expectations; they are not a live benchmark.
- Visual/layout PDF inspection remains out of scope and should route to a visual PDF workflow.

## Boundary Notes

- The skill stays inside `plugins/codex-documa` and does not modify Documa core.
- The skill defines workflow behavior; Documa MCP tools provide runtime document processing and block evidence retrieval.
- Parser-native objects must not leak into the skill contract.
