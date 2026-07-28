from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from src.normalizer.models import NormalizationResult
from src.pdf_parser.extractor import (
    extract_pdf_pages,
    parse_page_range,
    write_extraction_json,
)
from src.normalizer import (
    normalize_curriculum_pdf,
    write_normalized_json,
    write_review_markdown,
)
from src.reviewer import (
    propose_corrections_from_instruction,
    write_correction_proposal,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="교육과정 PDF 근거 기반 지식 그래프 PoC"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract", help="PDF의 지정된 페이지를 출처 정보와 함께 JSON으로 추출"
    )
    extract_parser.add_argument("--pdf", required=True, type=Path, help="원본 PDF 경로")
    extract_parser.add_argument(
        "--pages",
        required=True,
        help="1부터 시작하는 연속 페이지 범위(예: 128-130)",
    )
    extract_parser.add_argument(
        "--output", required=True, type=Path, help="추출 JSON 저장 경로"
    )

    normalize_parser = subparsers.add_parser(
        "normalize",
        help="PDF 128–130쪽의 표를 좌표로 복원하고 교차 검증한 JSON 생성",
    )
    normalize_parser.add_argument(
        "--pdf", required=True, type=Path, help="원본 PDF 경로"
    )
    normalize_parser.add_argument(
        "--output", required=True, type=Path, help="정규화 JSON 저장 경로"
    )
    normalize_parser.add_argument(
        "--review-output",
        type=Path,
        help="사람이 확인할 경고 보고서 경로(생략 시 정규화 JSON 옆에 자동 생성)",
    )
    normalize_parser.add_argument(
        "--corrections",
        type=Path,
        help="검토자가 작성한 보정 JSON 경로",
    )

    proposal_parser = subparsers.add_parser(
        "propose-corrections",
        help="자연어 검토 지시를 검증 가능한 보정 제안 JSON으로 변환",
    )
    proposal_parser.add_argument(
        "--normalized", required=True, type=Path, help="검토할 정규화 JSON 경로"
    )
    proposal_parser.add_argument(
        "--instruction", required=True, help="사람이 작성한 자연어 수정 지시"
    )
    proposal_parser.add_argument(
        "--reviewer", required=True, help="수정 지시 확인자"
    )
    proposal_parser.add_argument(
        "--output", required=True, type=Path, help="보정 제안 JSON 저장 경로"
    )

    revise_parser = subparsers.add_parser(
        "revise",
        help="자연어 지시를 기록하고 원본 PDF에서 정규화 JSON을 다시 생성",
    )
    revise_parser.add_argument("--pdf", required=True, type=Path, help="원본 PDF 경로")
    revise_parser.add_argument(
        "--normalized", required=True, type=Path, help="현재 정규화 JSON 경로"
    )
    revise_parser.add_argument(
        "--instruction", required=True, help="사람이 작성한 자연어 수정 지시"
    )
    revise_parser.add_argument("--reviewer", required=True, help="수정 지시 확인자")
    revise_parser.add_argument(
        "--corrections-output",
        required=True,
        type=Path,
        help="자연어에서 도출한 누적 보정 기록 경로",
    )
    revise_parser.add_argument(
        "--output", required=True, type=Path, help="다시 생성할 정규화 JSON 경로"
    )
    revise_parser.add_argument(
        "--review-output",
        type=Path,
        help="다시 생성할 검토 보고서 경로(생략 시 정규화 JSON 옆에 생성)",
    )
    return parser


def run_extract(args: argparse.Namespace) -> int:
    pages = parse_page_range(args.pages)
    result = extract_pdf_pages(args.pdf, pages)
    write_extraction_json(result, args.output)

    print(f"추출 완료: {args.output}")
    print(f"원본 전체 페이지: {result['source']['total_pdf_pages']}")
    for page in result["pages"]:
        print(
            f"PDF {page['pdf_page']}쪽: "
            f"텍스트 블록 {page['block_count']}개, "
            f"문자 {page['character_count']}자"
        )
    return 0


def _print_normalization_summary(
    result: NormalizationResult, output: Path, review_output: Path
) -> int:
    print(f"정규화 완료: {output}")
    print(f"검토 보고서: {review_output}")
    print(f"학과지정교과목: {len(result.curriculum.designated_courses)}개")
    print(f"전공교과목: {len(result.curriculum.major_courses)}개")
    passed = sum(check.status == "pass" for check in result.validation_checks)
    warnings = sum(check.status == "warning" for check in result.validation_checks)
    failed = sum(check.status == "fail" for check in result.validation_checks)
    print(
        f"자동 검증: {result.validation_status.upper()} "
        f"(통과 {passed}, 경고 {warnings}, 실패 {failed})"
    )
    for check in result.validation_checks:
        if check.status != "pass":
            print(f"- [{check.status.upper()}] {check.message}: {check.actual}")
    if result.validation_status == "fail":
        print("FAIL이 있어 다음 단계로 진행할 수 없습니다.")
        return 1
    if result.validation_status == "warning":
        print("사람이 확인할 WARNING이 있어 다음 단계로 진행하지 않습니다.")
        return 2
    return 0


def run_normalize(args: argparse.Namespace) -> int:
    result = normalize_curriculum_pdf(args.pdf, args.corrections)
    write_normalized_json(result, args.output)
    review_output = args.review_output or args.output.with_name(
        f"{args.output.stem}_review.md"
    )
    write_review_markdown(result, review_output)
    return _print_normalization_summary(result, args.output, review_output)


def run_propose_corrections(args: argparse.Namespace) -> int:
    if not args.normalized.is_file():
        raise FileNotFoundError(f"정규화 JSON을 찾을 수 없습니다: {args.normalized}")
    result = NormalizationResult.model_validate_json(
        args.normalized.read_text(encoding="utf-8")
    )
    proposal = propose_corrections_from_instruction(
        result,
        instruction=args.instruction,
        reviewer=args.reviewer,
    )
    write_correction_proposal(proposal, args.output)

    print(f"보정 제안 생성: {args.output}")
    print(f"제안 항목: {len(proposal.corrections)}개")
    for correction in proposal.corrections:
        print(
            f"- {correction.target_uid}.{correction.field} -> {correction.value!r} "
            f"(검사: {correction.check_id})"
        )
    print("아직 정규화 JSON에는 적용하지 않았습니다.")
    return 0


def run_revise(args: argparse.Namespace) -> int:
    if not args.normalized.is_file():
        raise FileNotFoundError(f"정규화 JSON을 찾을 수 없습니다: {args.normalized}")
    current = NormalizationResult.model_validate_json(
        args.normalized.read_text(encoding="utf-8")
    )
    proposal = propose_corrections_from_instruction(
        current,
        instruction=args.instruction,
        reviewer=args.reviewer,
    )
    write_correction_proposal(proposal, args.corrections_output)

    revised = normalize_curriculum_pdf(args.pdf, args.corrections_output)
    write_normalized_json(revised, args.output)
    review_output = args.review_output or args.output.with_name(
        f"{args.output.stem}_review.md"
    )
    write_review_markdown(revised, review_output)

    print(f"자연어 지시를 보정 {len(proposal.corrections)}건으로 기록했습니다.")
    return _print_normalization_summary(revised, args.output, review_output)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "extract":
        try:
            return run_extract(args)
        except (FileNotFoundError, ValueError) as error:
            parser.error(str(error))

    if args.command == "normalize":
        try:
            return run_normalize(args)
        except (FileNotFoundError, ValueError) as error:
            parser.error(str(error))

    if args.command == "propose-corrections":
        try:
            return run_propose_corrections(args)
        except (FileNotFoundError, ValueError) as error:
            parser.error(str(error))

    if args.command == "revise":
        try:
            return run_revise(args)
        except (FileNotFoundError, ValueError) as error:
            parser.error(str(error))

    parser.error(f"지원하지 않는 명령입니다: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
