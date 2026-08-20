"""Evaluate dynamic skill routing and context reduction from a JSONL case set.

Each line must contain ``task`` and ``expected_skill``. Set ``explicit`` to
true for exact-name cases. Optional candidate/baseline agent pass rates enable
the no-more-than-two-percentage-point regression gate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from documa.interfaces import token_counting
from documa.skills import load_skill_bundle
from documa.skills.index import query_skill_candidates
from documa.skills.store import active_skill_entries, load_skill_ir


def _cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--store-dir", default=".documa")
    parser.add_argument("--baseline-pass-rate", type=float)
    parser.add_argument("--candidate-pass-rate", type=float)
    args = parser.parse_args()

    counter = token_counting.get_token_counter()
    if counter is None:
        print(json.dumps({"status": "error", "code": "TOKEN_COUNTER_REQUIRED"}))
        return 1
    cases = _cases(args.cases)
    if not cases:
        print(json.dumps({"status": "error", "code": "SKILL_EVAL_CASES_REQUIRED"}))
        return 1

    explicit_total = explicit_top1 = held_total = held_recall = 0
    reductions: list[float] = []
    failures: list[dict] = []
    entries = {entry["skill_id"]: entry for entry in active_skill_entries(args.store_dir)}
    for case in cases:
        expected = str(case["expected_skill"]).casefold()
        result = query_skill_candidates(str(case["task"]), max_skills=3, store_dir=args.store_dir)
        names = {
            value.casefold()
            for candidate in result.get("candidates", [])
            for value in (candidate["skill_id"], candidate["qualified_name"], candidate["name"])
        }
        top_candidate = result.get("candidates", [{}])[0] if result.get("candidates") else {}
        top_names = {
            str(top_candidate.get(key, "")).casefold()
            for key in ("skill_id", "qualified_name", "name")
        }
        if case.get("explicit"):
            explicit_total += 1
            explicit_top1 += int(expected in top_names)
        else:
            held_total += 1
            held_recall += int(expected in names)
        if expected not in names:
            failures.append({"task": case["task"], "expected_skill": case["expected_skill"], "code": result.get("code")})
            continue
        bundle = load_skill_bundle(str(case["task"]), store_dir=args.store_dir)
        if bundle.status != "ok":
            continue
        full_tokens = 0
        for selected in bundle.selected_skills:
            entry = entries.get(selected["skill_id"])
            if entry:
                skill = load_skill_ir(entry, args.store_dir)
                full_tokens += counter.count((Path(skill.source_path) / "SKILL.md").read_text(encoding="utf-8"))
        if full_tokens:
            reductions.append(1.0 - float(bundle.budget["spent_tokens"]) / full_tokens)

    explicit_rate = explicit_top1 / explicit_total if explicit_total else None
    recall = held_recall / held_total if held_total else None
    median_reduction = statistics.median(reductions) if reductions else None
    gates = {
        "explicit_name_top1": explicit_rate is None or explicit_rate == 1.0,
        "held_out_recall_at_3": recall is None or recall >= 0.95,
        "median_context_reduction": median_reduction is not None and median_reduction >= 0.50,
    }
    pass_rate_gate = None
    if args.baseline_pass_rate is not None and args.candidate_pass_rate is not None:
        pass_rate_gate = args.candidate_pass_rate >= args.baseline_pass_rate - 0.02
        gates["agent_pass_rate"] = pass_rate_gate
    payload = {
        "status": "ok" if all(gates.values()) else "failed",
        "case_count": len(cases),
        "explicit_name_top1": explicit_rate,
        "held_out_recall_at_3": recall,
        "median_context_reduction": median_reduction,
        "agent_pass_rate_gate": pass_rate_gate,
        "gates": gates,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
