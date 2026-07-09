"""LLM-backed AnswerSupportChecker (RAGAS-faithfulness style) with streaming output.

Usage:
    python llm_support_checker.py <ir_path_or_document_id> <block_id> [block_id ...] --answer "..."

Requires the ``anthropic`` package and an ANTHROPIC_API_KEY environment
variable. This lives in examples on purpose: LLM inference never enters
Documa core or CI. The verification protocol:

1. Ask the model to split the answer into atomic claims.
2. Stream one verification pass over all claims against the evidence bundle;
   verdict lines are parsed as they stream, so an interrupted run keeps every
   finished verdict and marks the rest ``undetermined``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from documa.interfaces.answer_support import (
    AnswerSupportReport,
    ClaimVerdict,
    EvidenceBundle,
    build_evidence_bundle,
)
from documa.interfaces.tools import load_document

MODEL = "claude-sonnet-5"

_PROMPT = """You are verifying whether an answer is supported by cited evidence.

Answer to verify:
{answer}

Evidence blocks:
{evidence}

Step 1: Split the answer into atomic factual claims.
Step 2: For EACH claim, decide if the evidence supports it.

Output one line per claim, streaming them as you finish each one, in exactly
this JSON-lines format (no other text):
{{"claim": "...", "supported": true/false, "evidence_block_ids": ["..."], "rationale": "..."}}
"""


class LlmSupportChecker:
    """AnswerSupportChecker implementation backed by the Anthropic API."""

    name = f"llm/{MODEL}"

    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self._client = client

    def check(self, answer: str, evidence: EvidenceBundle) -> AnswerSupportReport:
        report = AnswerSupportReport(answer=answer, checker=self.name)
        if not answer.strip():
            return report  # contract: empty answer -> empty report

        evidence_text = "\n".join(
            f"[{item.block_id}] ({item.page_label}) {item.excerpt}" for item in evidence.items
        )
        prompt = _PROMPT.format(answer=answer, evidence=evidence_text)

        buffer = ""
        try:
            # Streaming is mandatory: verdicts appear as they are produced and
            # an interruption keeps everything parsed so far.
            with self._client.messages.stream(
                model=MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for text in stream.text_stream:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    buffer += text
                    buffer = self._drain_lines(buffer, report)
        except KeyboardInterrupt:
            print("\n[interrupted - keeping finished verdicts]", file=sys.stderr)
            report.verdicts.append(
                ClaimVerdict(claim="(remaining claims)", supported=False, undetermined=True,
                             rationale="verification interrupted before this point")
            )
            return report
        self._drain_lines(buffer + "\n", report)
        return report

    @staticmethod
    def _drain_lines(buffer: str, report: AnswerSupportReport) -> str:
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            report.verdicts.append(
                ClaimVerdict(
                    claim=str(data.get("claim", "")),
                    supported=bool(data.get("supported")),
                    evidence_block_ids=[str(x) for x in data.get("evidence_block_ids", [])],
                    rationale=str(data.get("rationale", "")),
                )
            )
        return buffer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ir_ref", help="IR path or document_id (doc-...)")
    parser.add_argument("block_ids", nargs="+", help="Cited block ids forming the evidence")
    parser.add_argument("--answer", required=True, help="Answer text to verify")
    args = parser.parse_args()

    document = load_document(args.ir_ref)
    bundle = build_evidence_bundle(document, args.block_ids)
    if bundle.missing_ids:
        print(f"warning: unknown block ids skipped: {bundle.missing_ids}", file=sys.stderr)

    report = LlmSupportChecker().check(args.answer, bundle)
    print("\n--- report ---")
    for verdict in report.verdicts:
        flag = "?" if verdict.undetermined else ("+" if verdict.supported else "-")
        print(f" [{flag}] {verdict.claim}  <- {', '.join(verdict.evidence_block_ids) or 'no evidence'}")
    print(f"supported ratio: {report.supported_ratio:.2f}  fully supported: {report.fully_supported}")
    return 0 if report.fully_supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
