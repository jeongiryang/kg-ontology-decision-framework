"""Assemble citations exclusively from already validated Neo4j result rows."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Mapping, Sequence

from .contracts import AnswerDraft, ChatResponse, ChatStatus, Citation


class CitationRenderer:
    def render(
        self,
        request_id: str,
        draft: AnswerDraft,
        rows: Sequence[Mapping[str, Any]],
    ) -> ChatResponse:
        selected_facts = set(draft.used_fact_ids)
        selected_evidence = set(draft.used_evidence_ids)
        grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for row in rows:
            fact_id = row.get("fact_id")
            evidence_id = row.get("evidence_id")
            if fact_id not in selected_facts or evidence_id not in selected_evidence:
                continue
            item = grouped.setdefault(
                evidence_id,
                {
                    "fact_ids": [],
                    "excerpt_page": row["excerpt_page"],
                    "source_pdf_page": row["source_pdf_page"],
                    "printed_page": row["printed_page"],
                    "source_text": row["source_text"],
                },
            )
            if fact_id not in item["fact_ids"]:
                item["fact_ids"].append(fact_id)
        citations = tuple(
            Citation(
                evidence_id=evidence_id,
                fact_ids=tuple(item["fact_ids"]),
                excerpt_page=item["excerpt_page"],
                source_pdf_page=item["source_pdf_page"],
                printed_page=item["printed_page"],
                source_text=item["source_text"],
            )
            for evidence_id, item in grouped.items()
        )
        return ChatResponse(
            request_id=request_id,
            status=ChatStatus.ANSWERABLE,
            answer_text=draft.answer_text,
            citations=citations,
            used_fact_ids=draft.used_fact_ids,
            used_evidence_ids=draft.used_evidence_ids,
        )
