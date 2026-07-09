"""Answer-support verification interface (the layer above citation-id checks).

Verification layers:

1. ``documa_verify_citations`` (deterministic): cited block ids exist and
   carry page references.
2. ``build_evidence_bundle`` (deterministic, this module): assemble the cited
   blocks' excerpts, page labels, and citation strings into an EvidenceBundle.
3. ``AnswerSupportChecker`` (pluggable contract, this module): split an answer
   into claims and judge whether each claim is supported by the evidence.
   Implementations live OUTSIDE core (see examples/answer_verification/) and
   may use an LLM or NLI model; ML/LLM inference never enters core or CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from documa.core.ir import DocumentIR
from documa.interfaces.citation import cite_block


@dataclass(slots=True)
class EvidenceItem:
    block_id: str
    excerpt: str
    page_label: str
    citation_string: str
    grounding: str  # "visual" | "logical"


@dataclass(slots=True)
class EvidenceBundle:
    document_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_ids and bool(self.items)


@dataclass(slots=True)
class ClaimVerdict:
    claim: str
    supported: bool
    evidence_block_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    undetermined: bool = False  # e.g. verification was interrupted mid-run


@dataclass(slots=True)
class AnswerSupportReport:
    answer: str
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    checker: str = "unknown"

    @property
    def supported_ratio(self) -> float:
        judged = [v for v in self.verdicts if not v.undetermined]
        if not judged:
            return 0.0
        return sum(1 for v in judged if v.supported) / len(judged)

    @property
    def fully_supported(self) -> bool:
        return bool(self.verdicts) and all(
            v.supported and not v.undetermined for v in self.verdicts
        )


@runtime_checkable
class AnswerSupportChecker(Protocol):
    """Contract for pluggable claim-level answer verification."""

    def check(self, answer: str, evidence: EvidenceBundle) -> AnswerSupportReport:
        """Judge every claim in ``answer`` against ``evidence``.

        Contract: an empty answer yields an empty report (no exception);
        interrupted runs keep finished verdicts and mark the rest
        ``undetermined=True``.
        """
        ...


def build_evidence_bundle(document: DocumentIR, block_ids: list[str]) -> EvidenceBundle:
    """Deterministically assemble citation-backed evidence for the given blocks.

    Unknown ids land in ``missing_ids`` instead of raising: the caller decides
    whether incomplete evidence is acceptable.
    """
    bundle = EvidenceBundle(document_id=document.id)
    for block_id in block_ids:
        cited = cite_block(document, str(block_id))
        if cited.get("status") != "ok":
            bundle.missing_ids.append(str(block_id))
            continue
        bundle.items.append(
            EvidenceItem(
                block_id=str(block_id),
                excerpt=cited["excerpt"],
                page_label=cited["page_label"],
                citation_string=cited["citation_string"],
                grounding=cited["grounding"],
            )
        )
    return bundle
