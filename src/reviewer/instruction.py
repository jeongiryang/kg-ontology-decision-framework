from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.normalizer.models import (
    Correction,
    CorrectionSet,
    NormalizationResult,
    ValidationCheck,
)


_ACTION_WORDS = ("수정", "변경", "바꿔", "바꾸", "교정", "적용")
_NEGATIVE_WORDS = ("수정하지", "변경하지", "바꾸지", "원문 유지", "그대로 유지")
_REPLACEMENT_PATTERN = re.compile(
    r"(?P<source>[A-Za-z가-힣0-9_.-]+?)(?:을|를)\s+"
    r"(?P<target>[A-Za-z가-힣0-9_.-]+?)(?:으로|로)\s*"
    r"(?:수정|변경|바꿔|바꾸|교정)"
)
_ARROW_PATTERN = re.compile(
    r"(?P<source>[A-Za-z가-힣0-9_.-]+)\s*(?:->|→)\s*"
    r"(?P<target>[A-Za-z가-힣0-9_.-]+)"
)
_TARGET_PATTERN = re.compile(r"^uid=(?P<uid>.+), field=(?P<field>[A-Za-z_][A-Za-z0-9_]*)$")


def _correctable_items(result: NormalizationResult) -> dict[str, Any]:
    curriculum = result.curriculum
    items = (
        curriculum.profile_statements
        + curriculum.program_requirements
        + curriculum.credit_allocations
        + curriculum.designated_courses
        + curriculum.major_courses
    )
    return {item.uid: item for item in items}


def _warning_targets(
    result: NormalizationResult,
    allowed_check_id: str | None = None,
) -> list[tuple[ValidationCheck, str, str, Any]]:
    items = _correctable_items(result)
    targets: list[tuple[ValidationCheck, str, str, Any]] = []
    for check in result.validation_checks:
        if check.status == "pass":
            continue
        if allowed_check_id is not None and check.check_id != allowed_check_id:
            continue
        for encoded_target in check.correction_targets:
            match = _TARGET_PATTERN.fullmatch(encoded_target)
            if match is None:
                continue
            uid = match.group("uid")
            field = match.group("field")
            item = items.get(uid)
            if item is None or field not in type(item).model_fields:
                continue
            targets.append((check, uid, field, getattr(item, field)))
    return targets


def _known_spelling_replacements(
    result: NormalizationResult,
    allowed_check_id: str | None = None,
) -> list[tuple[str, str]]:
    for check in result.validation_checks:
        if allowed_check_id is not None and check.check_id != allowed_check_id:
            continue
        if check.check_id != "source_spelling_candidates" or check.status == "pass":
            continue
        if not isinstance(check.expected, list):
            return []
        replacements: list[tuple[str, str]] = []
        for item in check.expected:
            if not isinstance(item, dict):
                continue
            source = item.get("source_text")
            candidate = item.get("candidate")
            if isinstance(source, str) and isinstance(candidate, str):
                replacements.append((source, candidate))
        return replacements
    return []


def _parse_replacements(
    result: NormalizationResult,
    instruction: str,
    allowed_check_id: str | None = None,
) -> list[tuple[str, str]]:
    if any(word in instruction for word in _NEGATIVE_WORDS):
        raise ValueError(
            "원문 유지 지시는 수정과 다른 검토 결정입니다. 현재 단계에서는 변경 제안만 만들 수 있습니다."
        )
    if not any(word in instruction for word in _ACTION_WORDS) and not (
        "->" in instruction or "→" in instruction
    ):
        raise ValueError("수정 의도를 확인할 수 없습니다. 기존값과 새 값을 함께 적어 주세요.")

    known = _known_spelling_replacements(result, allowed_check_id)
    replacements: list[tuple[str, str]] = []
    wants_all_suggestions = (
        any(word in instruction for word in ("모두", "전부", "두 개", "2개"))
        and "오탈자" in instruction
        and any(word in instruction for word in ("추천", "후보", "제안"))
    )
    if wants_all_suggestions:
        replacements.extend(known)
    else:
        for source, candidate in known:
            if source in instruction and (
                candidate in instruction
                or any(word in instruction for word in ("추천", "후보", "제안"))
            ):
                replacements.append((source, candidate))

    for pattern in (_REPLACEMENT_PATTERN, _ARROW_PATTERN):
        for match in pattern.finditer(instruction):
            replacements.append((match.group("source"), match.group("target")))

    unique: list[tuple[str, str]] = []
    for replacement in replacements:
        if replacement not in unique:
            unique.append(replacement)
    if not unique:
        raise ValueError(
            "변경할 기존값과 새 값을 찾지 못했습니다. 예: '운리의식을 윤리의식으로 수정해줘'"
        )
    return unique


def _replacement_value(current: Any, source: str, target: str) -> Any | None:
    if isinstance(current, str) and source in current:
        return current.replace(source, target)
    if isinstance(current, int) and source.isdigit() and current == int(source):
        if not target.isdigit():
            raise ValueError("숫자 필드의 새 값은 숫자여야 합니다.")
        return int(target)
    return None


def propose_corrections_from_instruction(
    result: NormalizationResult,
    instruction: str,
    reviewer: str,
    allowed_check_id: str | None = None,
) -> CorrectionSet:
    instruction = instruction.strip()
    reviewer = reviewer.strip()
    if not instruction:
        raise ValueError("자연어 지시가 비어 있습니다.")
    if not reviewer:
        raise ValueError("확인자 이름이 비어 있습니다.")

    warning_targets = _warning_targets(result, allowed_check_id)
    proposals: list[Correction] = []
    for source, target in _parse_replacements(
        result, instruction, allowed_check_id
    ):
        matches: list[tuple[ValidationCheck, str, str, Any]] = []
        proposed_values: list[Any] = []
        for check, uid, field, current in warning_targets:
            proposed = _replacement_value(current, source, target)
            if proposed is not None:
                matches.append((check, uid, field, current))
                proposed_values.append(proposed)

        if not matches:
            raise ValueError(
                f"경고 보고서의 보정 대상에서 기존값 '{source}'을(를) 찾지 못했습니다."
            )
        if len(matches) > 1:
            locations = ", ".join(f"{uid}.{field}" for _, uid, field, _ in matches)
            raise ValueError(
                f"기존값 '{source}'의 대상이 여러 개입니다: {locations}. 대상 이름을 더 구체적으로 지시해 주세요."
            )

        check, uid, field, _ = matches[0]
        proposals.append(
            Correction(
                check_id=check.check_id,
                target_uid=uid,
                field=field,
                value=proposed_values[0],
                reason=f"사람의 자연어 지시: {instruction}",
                reviewer=reviewer,
            )
        )

    proposal_keys = {
        (correction.target_uid, correction.field) for correction in proposals
    }
    previous = [
        correction
        for correction in result.corrections_applied
        if (correction.target_uid, correction.field) not in proposal_keys
    ]
    return CorrectionSet(
        schema_version="1.0",
        source_sha256=result.source.sha256,
        description=(
            "자연어 지시에서 도출한 보정 제안이다. normalize 명령에 전달하기 전에 내용을 확인한다."
        ),
        corrections=previous + proposals,
    )


def write_correction_proposal(
    proposal: CorrectionSet, output_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        proposal.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
