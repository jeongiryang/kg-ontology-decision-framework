"""Assemble reproducibly ordered citations from validated Claim provenance."""

from __future__ import annotations

from typing import Any

from .contracts import ChatResponse, ChatStatus, Citation, GroundingError
from .korean_renderer import RenderedAnswer


class CitationRenderer:
    def __init__(self, *, max_citations: int = 20, max_source_chars: int = 4_000):
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
        return ChatResponse(
            request_id=request_id,
            status=ChatStatus.ANSWERABLE,
            answer_text=answer.answer_text,
            citations=citations,
            used_fact_ids=answer.used_fact_ids,
            used_evidence_ids=answer.used_evidence_ids,
            grounded_claims=answer.claims,
        )

    def _source(self, source: Any) -> str:
        if not isinstance(source, str) or not source.strip():
            raise GroundingError("ANSWER_CITATION_INVALID", "Evidence source_text is empty")
        if len(source) > self.max_source_chars:
            raise GroundingError("ANSWER_CITATION_TOO_LARGE", "Evidence source_text is too large")
        return source
