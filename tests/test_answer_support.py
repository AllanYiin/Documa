"""AnswerSupportChecker contract and EvidenceBundle tests (R-Stage 6).

No network, no LLM: the contract is exercised with a mock checker, and the
example's streaming line-parser is tested directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from documa.interfaces.answer_support import (
    AnswerSupportChecker,
    AnswerSupportReport,
    ClaimVerdict,
    EvidenceBundle,
    build_evidence_bundle,
)
from documa.interfaces.tools import load_document, process_document_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_REPORT = REPO_ROOT / "fixtures" / "pdf" / "real" / "annual-report.pdf"


@pytest.fixture(scope="module")
def document(tmp_path_factory):
    out = tmp_path_factory.mktemp("answer_support")
    payload = process_document_tool(source=str(ANNUAL_REPORT), out=str(out))
    return load_document(payload["output_path"])


class TestEvidenceBundle:
    def test_bundle_assembles_citation_backed_items(self, document):
        # Agents cite content-bearing blocks (search results), not structural
        # containers like the document root, whose excerpt is legitimately empty.
        block_ids = [b.id for b in document.document_blocks if b.page_refs and b.text_preview][:2]
        assert len(block_ids) == 2
        bundle = build_evidence_bundle(document, block_ids)
        assert bundle.is_complete
        assert bundle.document_id == document.id
        for item in bundle.items:
            assert item.excerpt
            assert item.page_label.startswith("PDF p.")
            assert item.citation_string
            assert item.grounding in {"visual", "logical"}

    def test_unknown_ids_go_to_missing_not_exceptions(self, document):
        real = document.document_blocks[0].id
        bundle = build_evidence_bundle(document, [real, "blk-nope"])
        assert bundle.missing_ids == ["blk-nope"]
        assert not bundle.is_complete
        assert len(bundle.items) == 1

    def test_empty_request_is_incomplete(self, document):
        assert build_evidence_bundle(document, []).is_complete is False


class _MockChecker:
    """Deterministic checker used to exercise the protocol contract."""

    name = "mock"

    def check(self, answer: str, evidence: EvidenceBundle) -> AnswerSupportReport:
        report = AnswerSupportReport(answer=answer, checker=self.name)
        if not answer.strip():
            return report
        for sentence in filter(None, (s.strip() for s in answer.split("."))):
            supported = any(
                word in item.excerpt.lower() for item in evidence.items for word in sentence.lower().split()
            )
            report.verdicts.append(
                ClaimVerdict(claim=sentence, supported=supported,
                             evidence_block_ids=[i.block_id for i in evidence.items[:1]])
            )
        return report


class TestCheckerContract:
    def test_mock_checker_satisfies_the_protocol(self):
        assert isinstance(_MockChecker(), AnswerSupportChecker)

    def test_empty_answer_yields_empty_report(self, document):
        bundle = build_evidence_bundle(document, [document.document_blocks[0].id])
        report = _MockChecker().check("", bundle)
        assert report.verdicts == []
        assert report.supported_ratio == 0.0
        assert report.fully_supported is False

    def test_supported_ratio_ignores_undetermined_verdicts(self):
        report = AnswerSupportReport(
            answer="x",
            verdicts=[
                ClaimVerdict(claim="a", supported=True),
                ClaimVerdict(claim="b", supported=False),
                ClaimVerdict(claim="c", supported=False, undetermined=True),
            ],
        )
        assert report.supported_ratio == 0.5
        assert report.fully_supported is False

    def test_full_support_requires_every_claim_determined_and_supported(self):
        report = AnswerSupportReport(
            answer="x", verdicts=[ClaimVerdict(claim="a", supported=True)]
        )
        assert report.fully_supported is True


class TestExampleStreamParser:
    def test_streamed_jsonl_verdicts_are_parsed_incrementally(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "llm_support_checker",
            REPO_ROOT / "examples" / "answer_verification" / "llm_support_checker.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        report = AnswerSupportReport(answer="x")
        buffer = '{"claim": "first", "supported": true, "evidence_block_ids": ["b1"], "rationale": "ok"}\n{"claim": "sec'
        remainder = module.LlmSupportChecker._drain_lines(buffer, report)
        assert len(report.verdicts) == 1
        assert report.verdicts[0].claim == "first"
        assert remainder.startswith('{"claim": "sec')

        module.LlmSupportChecker._drain_lines(remainder + 'ond", "supported": false}\n', report)
        assert len(report.verdicts) == 2
        assert report.verdicts[1].supported is False
