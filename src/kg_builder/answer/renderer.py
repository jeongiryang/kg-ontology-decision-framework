"""Assemble reproducibly ordered citations from validated Claim provenance."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import Any

from .contracts import ChatResponse, ChatStatus, Citation, GroundedClaim, GroundingError
from .korean_renderer import RenderedAnswer


_PAYLOAD_SEAL = object()
_PAYLOAD_KEY = secrets.token_bytes(32)
_COMPOSITE_SEAL = object()
_COMPOSITE_KEY = secrets.token_bytes(32)


def _payload_digest(answer: RenderedAnswer, citations: tuple[Citation, ...]) -> str:
    payload = repr((answer._approval, citations)).encode("utf-8")
    return hmac.new(_PAYLOAD_KEY, payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class _ApprovedAnswerPayload:
    """CitationRenderer-issued answer and citations bound to one approval."""

    answer: RenderedAnswer
    citations: tuple[Citation, ...]
    _approval: str = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        answer: RenderedAnswer,
        citations: tuple[Citation, ...],
        *,
        _approval: str = "",
        _seal: object | None = None,
    ) -> None:
        expected = _payload_digest(answer, citations)
        if (
            _seal is not _PAYLOAD_SEAL
            or not answer._is_approved()
            or not hmac.compare_digest(_approval, expected)
        ):
            raise TypeError("answer payload can only be issued by CitationRenderer")
        object.__setattr__(self, "answer", answer)
        object.__setattr__(self, "citations", citations)
        object.__setattr__(self, "_approval", _approval)
        object.__setattr__(self, "_seal", _seal)

    @classmethod
    def _issue(
        cls, answer: RenderedAnswer, citations: tuple[Citation, ...]
    ) -> "_ApprovedAnswerPayload":
        return cls(
            answer,
            citations,
            _approval=_payload_digest(answer, citations),
            _seal=_PAYLOAD_SEAL,
        )

    def _is_approved(self) -> bool:
        return (
            self._seal is _PAYLOAD_SEAL
            and self.answer._is_approved()
            and hmac.compare_digest(
                self._approval, _payload_digest(self.answer, self.citations)
            )
        )


def _composite_digest(
    answer_text: str,
    claims: tuple[GroundedClaim, ...],
    citations: tuple[Citation, ...],
) -> str:
    return hmac.new(
        _COMPOSITE_KEY,
        repr((answer_text, claims, citations)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class _ApprovedCompositePayload:
    """Merge only independently sealed ANSWERABLE results.

    It is intentionally internal.  The answer text is the deterministic concatenation
    of the source answers; the agent model may select/order queries but cannot replace
    a factual sentence or attach unrelated citations.
    """

    answer_text: str
    claims: tuple[GroundedClaim, ...]
    citations: tuple[Citation, ...]
    _approval: str = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        answer_text: str,
        claims: tuple[GroundedClaim, ...],
        citations: tuple[Citation, ...],
        *,
        _approval: str = "",
        _seal: object | None = None,
    ) -> None:
        expected = _composite_digest(answer_text, claims, citations)
        if (
            _seal is not _COMPOSITE_SEAL
            or not hmac.compare_digest(_approval, expected)
            or not answer_text.strip()
            or not claims
            or not citations
        ):
            raise TypeError("composite payload requires sealed grounded source answers")
        object.__setattr__(self, "answer_text", answer_text)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "citations", citations)
        object.__setattr__(self, "_approval", _approval)
        object.__setattr__(self, "_seal", _seal)

    @classmethod
    def _issue(cls, responses: tuple[ChatResponse, ...]) -> "_ApprovedCompositePayload":
        if not 2 <= len(responses) <= 4 or any(
            response.status is not ChatStatus.ANSWERABLE or not response._is_approved()
            for response in responses
        ):
            raise TypeError("composite answers require 2..4 approved ANSWERABLE sources")
        claim_by_id: dict[str, GroundedClaim] = {}
        for response in responses:
            for claim in response.grounded_claims:
                previous = claim_by_id.setdefault(claim.claim_id, claim)
                if previous != claim:
                    raise GroundingError(
                        "ANSWER_CLAIM_VALIDATION_FAILED",
                        "same Claim ID has inconsistent source values",
                    )
        citation_by_id: dict[str, Citation] = {}
        for response in responses:
            for citation in response.citations:
                previous = citation_by_id.get(citation.evidence_id)
                if previous is None:
                    citation_by_id[citation.evidence_id] = citation
                    continue
                if (
                    previous.excerpt_page,
                    previous.source_pdf_page,
                    previous.printed_page,
                    previous.source_text,
                ) != (
                    citation.excerpt_page,
                    citation.source_pdf_page,
                    citation.printed_page,
                    citation.source_text,
                ):
                    raise GroundingError(
                        "ANSWER_CITATION_INVALID", "Evidence metadata differs across tool results"
                    )
                citation_by_id[citation.evidence_id] = Citation(
                    evidence_id=citation.evidence_id,
                    fact_ids=tuple(sorted(set(previous.fact_ids) | set(citation.fact_ids))),
                    excerpt_page=citation.excerpt_page,
                    source_pdf_page=citation.source_pdf_page,
                    printed_page=citation.printed_page,
                    source_text=citation.source_text,
                )
        citations = tuple(
            sorted(
                citation_by_id.values(),
                key=lambda item: (
                    item.excerpt_page,
                    item.source_pdf_page,
                    item.printed_page,
                    item.evidence_id,
                    item.fact_ids,
                ),
            )
        )
        if len(citations) > 250:
            raise GroundingError("ANSWER_TOO_MANY_CITATIONS", "composite citation limit exceeded")
        answer_text = "\n\n".join(
            dict.fromkeys(response.answer_text for response in responses)
        )
        claims = tuple(sorted(claim_by_id.values(), key=lambda item: item.claim_id))
        return cls(
            answer_text,
            claims,
            citations,
            _approval=_composite_digest(answer_text, claims, citations),
            _seal=_COMPOSITE_SEAL,
        )

    def _is_approved(self) -> bool:
        return self._seal is _COMPOSITE_SEAL and hmac.compare_digest(
            self._approval,
            _composite_digest(self.answer_text, self.claims, self.citations),
        )


class CitationRenderer:
    def __init__(self, *, max_citations: int = 250, max_source_chars: int = 4_000):
        self.max_citations = max_citations
        self.max_source_chars = max_source_chars

    def render(
        self,
        request_id: str,
        answer: RenderedAnswer,
    ) -> ChatResponse:
        if not isinstance(answer, RenderedAnswer) or not answer._is_approved():
            raise GroundingError(
                "ANSWER_RENDER_APPROVAL_REQUIRED",
                "CitationRenderer accepts only KoreanAnswerRenderer-issued answers",
            )
        validated = answer.validated_claims
        selected_pairs = {
            (link.fact_id, link.evidence_id)
            for claim in answer.claims
            for link in claim.provenance
        }
        grouped: dict[str, dict[str, Any]] = {}
        found_pairs: set[tuple[str, str]] = set()
        for source in sorted(validated.citation_sources):
            pair = (source.fact_id, source.evidence_id)
            if pair not in selected_pairs:
                continue
            found_pairs.add(pair)
            evidence_id, fact_id = pair[1], pair[0]
            item = grouped.setdefault(
                evidence_id,
                {
                    "fact_ids": set(),
                    "excerpt_page": source.excerpt_page,
                    "source_pdf_page": source.source_pdf_page,
                    "printed_page": source.printed_page,
                    "source_text": source.source_text,
                },
            )
            expected = (
                item["excerpt_page"],
                item["source_pdf_page"],
                item["printed_page"],
                item["source_text"],
            )
            actual = (
                source.excerpt_page,
                source.source_pdf_page,
                source.printed_page,
                source.source_text,
            )
            if expected != actual:
                raise GroundingError(
                    "ANSWER_CITATION_INVALID", "Evidence metadata is inconsistent"
                )
            item["fact_ids"].add(fact_id)
        if found_pairs != selected_pairs:
            raise GroundingError(
                "ANSWER_FACT_EVIDENCE_MISMATCH",
                "rendered Claims are not bound to the approved citation sources",
            )
        if len(grouped) > self.max_citations:
            raise GroundingError("ANSWER_TOO_MANY_CITATIONS", "citation limit exceeded")
        citations = tuple(
            sorted(
                (
                    Citation(
                        evidence_id=evidence_id,
                        fact_ids=tuple(sorted(item["fact_ids"])),
                        excerpt_page=item["excerpt_page"],
                        source_pdf_page=item["source_pdf_page"],
                        printed_page=item["printed_page"],
                        source_text=self._source(item["source_text"]),
                    )
                    for evidence_id, item in grouped.items()
                ),
                key=lambda item: (
                    item.excerpt_page,
                    item.source_pdf_page,
                    item.printed_page,
                    item.evidence_id,
                    item.fact_ids,
                ),
            )
        )
        if not citations:
            raise GroundingError("ANSWER_CITATION_INVALID", "no citations were assembled")
        return ChatResponse.from_approved_answer(
            request_id,
            _ApprovedAnswerPayload._issue(answer, citations),
        )

    def _source(self, source: Any) -> str:
        if not isinstance(source, str) or not source.strip():
            raise GroundingError("ANSWER_CITATION_INVALID", "Evidence source_text is empty")
        if len(source) > self.max_source_chars:
            raise GroundingError("ANSWER_CITATION_TOO_LARGE", "Evidence source_text is too large")
        return source
