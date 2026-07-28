from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

import pdfplumber
import pymupdf

from .models import (
    CatalogSummary,
    CompetencySummaryItem,
    Correction,
    CorrectionSet,
    Course,
    CreditAllocation,
    CurriculumData,
    EvidenceRef,
    NormalizationResult,
    ProfileStatement,
    ProgramRequirement,
    SourceInfo,
    ValidationCheck,
)


PDF_PAGES = (128, 129, 130)
COURSE_CODE_PATTERN = re.compile(r"^(?:CDA|GEA)\d{4}$")
CIRCLED_DIGITS = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", value)
    return int(match.group()) if match else None


def _printed_page(page: pymupdf.Page) -> int:
    header_words = [word[4] for word in page.get_text("words") if word[1] < 80]
    for word in header_words:
        match = re.search(r"(?<!\d)(\d{3})(?!\d)", word)
        if match:
            return int(match.group(1))
    raise ValueError(f"PDF {page.number + 1}쪽의 인쇄 페이지 번호를 찾지 못했습니다.")


def _evidence(
    *,
    pdf_page: int,
    printed_pages: dict[int, int],
    section: str,
    bbox: Sequence[float],
    raw_text: str,
    extraction_method: str = "PyMuPDF coordinates",
) -> EvidenceRef:
    return EvidenceRef(
        pdf_page=pdf_page,
        printed_page=printed_pages[pdf_page],
        section=section,
        bbox=tuple(round(float(value), 3) for value in bbox),
        raw_text=_clean_text(raw_text),
        extraction_method=extraction_method,
    )


def _join_words(words: Iterable[Sequence[Any]], separator: str = " ") -> str:
    ordered = sorted(words, key=lambda word: (round(float(word[1]), 1), float(word[0])))
    return separator.join(str(word[4]) for word in ordered).strip()


def _split_bilingual_name(raw_name: str) -> tuple[str, str | None]:
    cleaned = _clean_text(raw_name.replace("※", ""))
    match = re.match(r"^(.*?)\((.*?)\)(.*)$", cleaned)
    if match is None:
        return cleaned, None
    korean, english, suffix = match.groups()
    suffix = suffix.strip()
    korean_name = f"{korean.strip()}{suffix}"
    english_name = f"{english.strip()} {suffix}".strip()
    return korean_name, english_name


def _profile_statements(
    page: pymupdf.Page, printed_pages: dict[int, int]
) -> list[ProfileStatement]:
    definitions = [
        ("educational_goal", 165, 185),
        ("talent_profile", 218, 270),
        ("competency_definition", 302, 380),
    ]
    counters = {kind: 0 for kind, _, _ in definitions}
    statements: list[ProfileStatement] = []

    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text = block[:5]
        for kind, start_y, end_y in definitions:
            if start_y <= y0 < end_y:
                counters[kind] += 1
                order = counters[kind]
                statements.append(
                    ProfileStatement(
                        uid=f"profile:2022:computer-engineering:{kind}:{order}",
                        kind=kind,
                        order=order,
                        text=_clean_text(text),
                        evidence=_evidence(
                            pdf_page=128,
                            printed_pages=printed_pages,
                            section=kind,
                            bbox=(x0, y0, x1, y1),
                            raw_text=text,
                        ),
                    )
                )
                break
    return statements


def _pdfplumber_profile_statements(
    pdf_path: Path, printed_pages: dict[int, int]
) -> list[ProfileStatement]:
    heading_to_kind = {
        "1.": "educational_goal",
        "2.": "talent_profile",
        "3.": "competency_definition",
    }
    counters = {kind: 0 for kind in heading_to_kind.values()}
    statements: list[ProfileStatement] = []
    current_kind: str | None = None

    with pdfplumber.open(pdf_path) as document:
        page = document.pages[127]
        lines = page.crop((60, 130, 480, 430)).extract_text_lines(
            strip=True, return_chars=False
        )
        for line in lines:
            text = _clean_text(line["text"])
            heading = next(
                (prefix for prefix in heading_to_kind if text.startswith(prefix)),
                None,
            )
            if heading is not None:
                current_kind = heading_to_kind[heading]
                continue
            if current_kind is None or text == "컴퓨터공학과":
                continue
            counters[current_kind] += 1
            order = counters[current_kind]
            statements.append(
                ProfileStatement(
                    uid=f"profile:2022:computer-engineering:{current_kind}:{order}",
                    kind=current_kind,
                    order=order,
                    text=text,
                    evidence=_evidence(
                        pdf_page=128,
                        printed_pages=printed_pages,
                        section=current_kind,
                        bbox=(line["x0"], line["top"], line["x1"], line["bottom"]),
                        raw_text=text,
                        extraction_method="pdfplumber text lines",
                    ),
                )
            )
    return statements


def _program_requirements(
    page: pymupdf.Page, printed_pages: dict[int, int]
) -> list[ProgramRequirement]:
    table = page.find_tables().tables[0]
    rows = table.extract()[2:]
    results: list[ProgramRequirement] = []
    for index, row in enumerate(rows):
        track = _clean_text(row[1] or "")
        row_cells = [
            cell for cell in table.rows[index + 2].cells if cell is not None
        ]
        row_bbox = (
            min(cell[0] for cell in row_cells),
            min(cell[1] for cell in row_cells),
            max(cell[2] for cell in row_cells),
            max(cell[3] for cell in row_cells),
        )
        result = ProgramRequirement(
            uid=f"requirement:2022:computer-engineering:{track}",
            track=track,
            basic_general_credits=int(row[2]),
            balanced_general_credits=int(row[3]),
            general_remaining_credits=int(row[4]),
            general_total_credits=int(row[5]),
            major_foundation_credits=_int_or_none(row[6]),
            major_required_credits=int(row[7]),
            major_elective_credits=int(row[8]),
            major_total_credits=int(row[9]),
            graduation_remaining_credits=int(row[10]),
            graduation_total_credits=int(row[11]),
            minimum_major_recognition_enabled=(
                True if row[12] and "○" in row[12] else None
            ),
            evidence=_evidence(
                pdf_page=128,
                printed_pages=printed_pages,
                section="기본이수 학점구조표",
                bbox=row_bbox,
                raw_text=" | ".join("" if value is None else value for value in row),
            ),
        )
        results.append(result)
    return results


def _competency_summary(page: pymupdf.Page) -> list[CompetencySummaryItem]:
    table = page.find_tables().tables[1]
    values = table.extract()[1][1:6]
    summary: list[CompetencySummaryItem] = []
    for competency_id, value in enumerate(values, start=1):
        numbers = [int(number) for number in re.findall(r"\d+", value or "")]
        if len(numbers) != 2:
            raise ValueError(f"전공능력 {competency_id} 요약값을 해석할 수 없습니다: {value}")
        summary.append(
            CompetencySummaryItem(
                competency_id=competency_id,
                course_count=numbers[0],
                credit_sum=numbers[1],
            )
        )
    return summary


def _course_table(page: pymupdf.Page) -> Any:
    for table in page.find_tables().tables:
        extracted = table.extract()
        header = " ".join(value or "" for value in extracted[0])
        if "학수번호" in header and "전공능력" in header:
            return table
    raise ValueError(f"PDF {page.number + 1}쪽에서 전공교육과정표를 찾지 못했습니다.")


def _column_cells(table: Any) -> list[tuple[float, float, float, float]]:
    first = table.rows[0].cells
    second = table.rows[1].cells
    cells = [first[0], first[1], first[2], first[3], second[4], second[5], first[6], first[7]]
    if any(cell is None for cell in cells):
        raise ValueError("전공교육과정표의 열 경계를 찾지 못했습니다.")
    return cells


def _words_in_cell(
    words: list[Sequence[Any]],
    column: Sequence[float],
    row_top: float,
    row_bottom: float,
) -> list[Sequence[Any]]:
    x0, _, x1, _ = column
    selected: list[Sequence[Any]] = []
    for word in words:
        center_x = (float(word[0]) + float(word[2])) / 2
        center_y = (float(word[1]) + float(word[3])) / 2
        if x0 <= center_x < x1 and row_top <= center_y < row_bottom:
            selected.append(word)
    return selected


def _row_bounds(
    code_words: list[Sequence[Any]], data_top: float, data_bottom: float
) -> list[tuple[float, float]]:
    centers = [(float(word[1]) + float(word[3])) / 2 for word in code_words]
    boundaries = [data_top]
    boundaries.extend((left + right) / 2 for left, right in zip(centers, centers[1:]))
    boundaries.append(data_bottom)
    return list(zip(boundaries, boundaries[1:]))


def _parse_practice(value: str) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    weeks = re.fullmatch(r"(\d+)주", value)
    if weeks:
        return None, int(weeks.group(1))
    return int(value), None


def _major_courses(
    pages: list[pymupdf.Page], printed_pages: dict[int, int]
) -> list[Course]:
    required_codes: set[str] = set()
    # PyMuPDF의 Table 객체는 다음 find_tables() 호출 뒤 재사용하지 않고 즉시 소비한다.
    for page in pages:
        table = _course_table(page)
        for row in table.extract()[2:]:
            if row[0] and "필수" in row[0]:
                required_codes.update(re.findall(r"CDA\d{4}", row[1] or ""))

    courses: list[Course] = []
    source_order = 0
    for page in pages:
        table = _course_table(page)
        pdf_page = page.number + 1
        columns = _column_cells(table)
        words = page.get_text("words", clip=pymupdf.Rect(table.bbox), sort=False)
        code_words = sorted(
            [word for word in words if re.fullmatch(r"CDA\d{4}", str(word[4]))],
            key=lambda word: float(word[1]),
        )
        data_top = max(cell[3] for cell in table.rows[1].cells if cell is not None)
        extracted = table.extract()
        data_bottom = table.bbox[3]
        if extracted[-1][0] and _clean_text(extracted[-1][0]) == "계":
            data_bottom = min(
                cell[1] for cell in table.rows[-1].cells if cell is not None
            )

        for code_word, (row_top, row_bottom) in zip(
            code_words, _row_bounds(code_words, data_top, data_bottom)
        ):
            source_order += 1
            cells = [
                _words_in_cell(words, column, row_top, row_bottom)
                for column in columns
            ]
            code = str(code_word[4])
            raw_name = _join_words(cells[2])
            name_ko, name_en = _split_bilingual_name(raw_name)
            credit_text = _join_words(cells[3], separator="")
            lecture_text = _join_words(cells[4], separator="")
            practice_text = _join_words(cells[5], separator="")
            offering = _join_words(cells[6], separator="")
            competency_text = _join_words(cells[7], separator="")
            practice_hours, practice_weeks = _parse_practice(practice_text)
            course_type = "major_required" if code in required_codes else "major_elective"
            raw_row = " | ".join(
                [
                    code,
                    raw_name,
                    credit_text,
                    lecture_text,
                    practice_text,
                    offering,
                    competency_text,
                ]
            )
            courses.append(
                Course(
                    uid=f"course:{code}",
                    code=code,
                    course_type=course_type,
                    category="전공필수" if course_type == "major_required" else "전공선택",
                    name_ko=name_ko,
                    name_en=name_en,
                    credits=int(credit_text),
                    lecture_hours=int(lecture_text) if lecture_text else None,
                    practice_hours=practice_hours,
                    practice_weeks=practice_weeks,
                    offering=offering,
                    competency_ids=[
                        CIRCLED_DIGITS[character]
                        for character in competency_text
                        if character in CIRCLED_DIGITS
                    ],
                    minor_required="※" in raw_name,
                    source_order=source_order,
                    evidence=_evidence(
                        pdf_page=pdf_page,
                        printed_pages=printed_pages,
                        section="전공교육과정표",
                        bbox=(table.bbox[0], row_top, table.bbox[2], row_bottom),
                        raw_text=raw_row,
                    ),
                )
            )
    return courses


def _parse_general_category(raw: str) -> tuple[str, str | None]:
    cleaned = _clean_text(raw)
    category_match = re.search(r"(균형교양|확대교양)", cleaned)
    if not category_match:
        raise ValueError(f"학과지정교과목의 구분을 해석할 수 없습니다: {raw}")
    category = category_match.group(1)
    area_match = re.search(r"\((.*?)\)", cleaned)
    area = area_match.group(1).strip() if area_match else None
    if area:
        area = re.sub(r"\s*·\s*", "·", area)
        area = re.sub(r"^(\d+)\.\s*", r"\1. ", area)
    return category, area


def _designated_courses(
    page: pymupdf.Page, printed_pages: dict[int, int]
) -> list[Course]:
    table = next(
        table
        for table in page.find_tables().tables
        if table.col_count == 6 and "GEA" in " ".join("" if value is None else value for row in table.extract() for value in row)
    )
    columns = table.rows[0].cells
    words = page.get_text("words", clip=pymupdf.Rect(table.bbox), sort=False)
    code_words = sorted(
        [word for word in words if re.fullmatch(r"GEA\d{4}", str(word[4]))],
        key=lambda word: float(word[1]),
    )
    bounds = _row_bounds(code_words, table.rows[0].cells[0][3], table.bbox[3])
    courses: list[Course] = []
    current_category: str | None = None
    current_area: str | None = None

    for source_order, (code_word, (row_top, row_bottom)) in enumerate(
        zip(code_words, bounds), start=1
    ):
        cells = [
            _words_in_cell(words, column, row_top, row_bottom)
            for column in columns
        ]
        category_text = _join_words(cells[0])
        if category_text:
            current_category, current_area = _parse_general_category(category_text)
        if current_category is None:
            raise ValueError("첫 학과지정교과목의 구분이 비어 있습니다.")

        code = str(code_word[4])
        name = _join_words(cells[2])
        credits = _join_words(cells[3], separator="")
        total_hours = _join_words(cells[4], separator="")
        offering = _join_words(cells[5], separator="")
        raw_row = " | ".join(
            [category_text or current_category, code, name, credits, total_hours, offering]
        )
        courses.append(
            Course(
                uid=f"course:{code}",
                code=code,
                course_type="designated_general",
                category=current_category,
                area=current_area,
                name_ko=name,
                credits=int(credits),
                total_hours=int(total_hours),
                offering=offering,
                source_order=source_order,
                evidence=_evidence(
                    pdf_page=129,
                    printed_pages=printed_pages,
                    section="학과지정교과목",
                    bbox=(table.bbox[0], row_top, table.bbox[2], row_bottom),
                    raw_text=raw_row,
                ),
            )
        )
    return courses


def _credit_allocations(
    page: pymupdf.Page, printed_pages: dict[int, int]
) -> list[CreditAllocation]:
    table = page.find_tables().tables[0]
    words = page.get_text("words", clip=pymupdf.Rect(table.bbox), sort=False)
    data_words = [word for word in words if float(word[1]) >= table.rows[1].cells[2][3]]

    lines: list[list[Sequence[Any]]] = []
    for word in sorted(data_words, key=lambda item: (float(item[1]), float(item[0]))):
        if not lines or abs(float(lines[-1][0][1]) - float(word[1])) > 0.8:
            lines.append([word])
        else:
            lines[-1].append(word)

    term_columns = table.rows[1].cells[2:10]
    total_column = table.rows[0].cells[10]
    allocations: list[CreditAllocation] = []
    section = "교양"
    for line in lines:
        label = "".join(
            str(word[4])
            for word in sorted(line, key=lambda item: float(item[0]))
            if (float(word[0]) + float(word[2])) / 2 < table.rows[1].cells[2][0]
        )
        if not label:
            continue
        if label.startswith("교양") and label != "교양":
            label = label[len("교양") :]
        if label.startswith("전공전공"):
            label = label[len("전공") :]
            section = "전공"
        if label == "소계":
            category = f"{section} 소계"
        elif label in {"전공필수", "전공선택"}:
            section = "전공"
            category = label
        elif label in {"기초교양", "균형교양", "확대교양", "잔여학점"}:
            category = label
        elif label == "(교양)+(전공)=계":
            section = "전체"
            category = "교양+전공"
        elif label in {"졸업잔여학점", "졸업학점"}:
            section = "졸업"
            category = label
        else:
            continue

        term_credits: list[int] = []
        for column in term_columns:
            values = _words_in_cell(line, column, -float("inf"), float("inf"))
            number = _int_or_none(_join_words(values, separator=""))
            term_credits.append(number or 0)
        total_values = _words_in_cell(line, total_column, -float("inf"), float("inf"))
        declared_total = _int_or_none(_join_words(total_values, separator=""))
        y0 = min(float(word[1]) for word in line)
        y1 = max(float(word[3]) for word in line)
        allocations.append(
            CreditAllocation(
                uid=f"allocation:2022:{section}:{category}",
                section=section,
                category=category,
                term_credits=term_credits,
                declared_total=declared_total,
                evidence=_evidence(
                    pdf_page=129,
                    printed_pages=printed_pages,
                    section="전공심화과정 학점배분구조표",
                    bbox=(table.bbox[0], y0, table.bbox[2], y1),
                    raw_text=_join_words(line),
                ),
            )
        )
    return allocations


def _catalog_summary(
    page: pymupdf.Page, printed_pages: dict[int, int]
) -> CatalogSummary:
    table = _course_table(page)
    row = table.extract()[-1]
    numbers = [int(number) for number in re.findall(r"\d+", " ".join(value or "" for value in row))]
    if len(numbers) != 2:
        raise ValueError(f"전공교육과정표 합계를 해석할 수 없습니다: {row}")
    cells = [cell for cell in table.rows[-1].cells if cell is not None]
    bbox = (
        min(cell[0] for cell in cells),
        min(cell[1] for cell in cells),
        max(cell[2] for cell in cells),
        max(cell[3] for cell in cells),
    )
    return CatalogSummary(
        declared_course_count=numbers[0],
        declared_credit_sum=numbers[1],
        evidence=_evidence(
            pdf_page=130,
            printed_pages=printed_pages,
            section="전공교육과정표 합계",
            bbox=bbox,
            raw_text=" | ".join(value or "" for value in row),
        ),
    )


def _pdfplumber_course_credits(pdf_path: Path) -> tuple[dict[str, int], list[int]]:
    results: dict[str, int] = {}
    table_counts: list[int] = []
    with pdfplumber.open(pdf_path) as document:
        for pdf_page in PDF_PAGES:
            page = document.pages[pdf_page - 1]
            tables = page.find_tables()
            table_counts.append(len(tables))
            words = page.extract_words()
            for table in tables:
                extracted = table.extract()
                header = " ".join(value or "" for value in extracted[0])
                if "학수번호" not in header or len(table.rows[0].cells) < 4:
                    continue
                credit_column = table.rows[0].cells[3]
                if credit_column is None:
                    continue
                table_words = [
                    word
                    for word in words
                    if table.bbox[0] <= (word["x0"] + word["x1"]) / 2 < table.bbox[2]
                    and table.bbox[1] <= (word["top"] + word["bottom"]) / 2 < table.bbox[3]
                ]
                code_words = sorted(
                    [
                        word
                        for word in table_words
                        if COURSE_CODE_PATTERN.fullmatch(word["text"])
                    ],
                    key=lambda word: word["top"],
                )
                credit_words = [
                    word
                    for word in table_words
                    if word["text"].isdigit()
                    and credit_column[0]
                    <= (word["x0"] + word["x1"]) / 2
                    < credit_column[2]
                ]
                for code_word in code_words:
                    center_y = (code_word["top"] + code_word["bottom"]) / 2
                    nearest = min(
                        credit_words,
                        key=lambda candidate: abs(
                            (candidate["top"] + candidate["bottom"]) / 2
                            - center_y
                        ),
                    )
                    results[code_word["text"]] = int(nearest["text"])
    return results, table_counts


def _load_corrections(
    corrections_path: Path | None, source_sha256: str
) -> list[Correction]:
    if corrections_path is None:
        return []
    if not corrections_path.is_file():
        raise FileNotFoundError(f"보정 파일을 찾을 수 없습니다: {corrections_path}")
    correction_set = CorrectionSet.model_validate_json(
        corrections_path.read_text(encoding="utf-8")
    )
    if correction_set.source_sha256 != source_sha256:
        raise ValueError(
            "보정 파일의 원본 SHA-256이 현재 PDF와 다릅니다. 다른 문서의 보정을 적용할 수 없습니다."
        )
    return correction_set.corrections


def _apply_corrections(
    curriculum: CurriculumData, corrections: list[Correction]
) -> list[Correction]:
    correctable_items = (
        curriculum.profile_statements
        + curriculum.program_requirements
        + curriculum.credit_allocations
        + curriculum.designated_courses
        + curriculum.major_courses
    )
    by_uid = {item.uid: item for item in correctable_items}
    protected_fields = {"uid", "code", "evidence", "source_order"}

    for correction in corrections:
        target = by_uid.get(correction.target_uid)
        if target is None:
            raise ValueError(f"보정 대상 UID를 찾을 수 없습니다: {correction.target_uid}")
        if correction.field in protected_fields:
            raise ValueError(f"보정할 수 없는 식별·근거 필드입니다: {correction.field}")
        if correction.field not in type(target).model_fields:
            raise ValueError(
                f"{correction.target_uid}에 {correction.field} 필드가 없습니다."
            )
        setattr(target, correction.field, correction.value)
    return corrections


def _validation_checks(
    curriculum: CurriculumData,
    pdfplumber_credits: dict[str, int],
    pymupdf_table_counts: list[int],
    pdfplumber_table_counts: list[int],
    pymupdf_profile: list[ProfileStatement],
    corrections: list[Correction],
) -> list[ValidationCheck]:
    checks: list[ValidationCheck] = []
    all_courses = curriculum.designated_courses + curriculum.major_courses
    codes = [course.code for course in all_courses]
    checks.append(
        ValidationCheck(
            check_id="unique_course_codes",
            status="pass" if len(codes) == len(set(codes)) else "fail",
            message="학수번호 중복 검사",
            expected=len(codes),
            actual=len(set(codes)),
        )
    )

    major_count = len(curriculum.major_courses)
    checks.append(
        ValidationCheck(
            check_id="major_catalog_course_count",
            status=(
                "pass"
                if major_count == curriculum.catalog_summary.declared_course_count
                else "fail"
            ),
            message="전공교육과정표의 과목 수 합계 검사",
            expected=curriculum.catalog_summary.declared_course_count,
            actual=major_count,
        )
    )
    major_credit_sum = sum(course.credits for course in curriculum.major_courses)
    checks.append(
        ValidationCheck(
            check_id="major_catalog_credit_sum",
            status=(
                "pass"
                if major_credit_sum == curriculum.catalog_summary.declared_credit_sum
                else "fail"
            ),
            message="전공교육과정표의 개설 학점 합계 검사",
            expected=curriculum.catalog_summary.declared_credit_sum,
            actual=major_credit_sum,
        )
    )

    pymupdf_credits = {course.code: course.credits for course in all_courses}
    cross_parser_ok = pymupdf_credits == pdfplumber_credits
    checks.append(
        ValidationCheck(
            check_id="cross_parser_course_credits",
            status="pass" if cross_parser_ok else "fail",
            message="PyMuPDF와 pdfplumber가 읽은 학수번호·학점 비교",
            expected=pymupdf_credits,
            actual=pdfplumber_credits,
        )
    )
    checks.append(
        ValidationCheck(
            check_id="cross_parser_table_counts",
            status="pass" if pymupdf_table_counts == pdfplumber_table_counts else "warning",
            message="두 PDF 엔진이 찾은 페이지별 표 개수 비교",
            expected=pymupdf_table_counts,
            actual=pdfplumber_table_counts,
        )
    )

    requirement_totals_ok = all(
        requirement.general_total_credits
        + requirement.major_total_credits
        + requirement.graduation_remaining_credits
        == requirement.graduation_total_credits
        for requirement in curriculum.program_requirements
    )
    checks.append(
        ValidationCheck(
            check_id="program_requirement_totals",
            status="pass" if requirement_totals_ok else "fail",
            message="기본이수 학점구조표의 교양·전공·잔여학점 합계 검사",
            expected="각 과정의 합계가 졸업학점과 일치",
            actual=requirement_totals_ok,
        )
    )

    for allocation in curriculum.credit_allocations:
        if allocation.declared_total is None:
            continue
        calculated = sum(allocation.term_credits)
        matches = calculated == allocation.declared_total
        checks.append(
            ValidationCheck(
                check_id=f"allocation_total:{allocation.uid}",
                status="pass" if matches else "warning",
                message=f"{allocation.category} 학기별 학점 합계 검사",
                expected=allocation.declared_total,
                actual=calculated,
                reason=(
                    None
                    if matches
                    else "같은 행의 8개 학기 값을 더한 결과와 PDF에 인쇄된 총계가 다릅니다. 자동으로 어느 값이 오자인지 결정하지 않았습니다."
                ),
                evidence=[] if matches else [allocation.evidence],
                review_steps=(
                    []
                    if matches
                    else [
                        "PDF 파일 129쪽(인쇄 282쪽)의 '5. 전공심화과정 학점배분구조표'를 엽니다.",
                        f"'{allocation.category}' 행의 학기별 값을 왼쪽부터 더합니다: {allocation.term_credits} = {calculated}.",
                        f"같은 행 오른쪽 총계 {allocation.declared_total} 및 바로 아래 전공 소계와 비교합니다.",
                        "원본 발행기관의 정정 자료가 있으면 확인한 값을 보정 파일에 기록합니다.",
                    ]
                ),
                correction_targets=(
                    []
                    if matches
                    else [
                        f"uid={allocation.uid}, field=term_credits",
                        f"uid={allocation.uid}, field=declared_total",
                    ]
                ),
            )
        )

    required_catalog_credits = sum(
        course.credits
        for course in curriculum.major_courses
        if course.course_type == "major_required"
    )
    required_requirement_credits = curriculum.program_requirements[0].major_required_credits
    required_allocation = next(
        allocation
        for allocation in curriculum.credit_allocations
        if allocation.category == "전공필수"
    )
    required_values = [
        required_catalog_credits,
        required_requirement_credits,
        required_allocation.declared_total,
    ]
    checks.append(
        ValidationCheck(
            check_id="required_credit_cross_section",
            status="pass" if len(set(required_values)) == 1 else "warning",
            message="전공필수 학점을 세 표에서 교차 검사",
            expected="세 표의 값이 일치",
            actual={
                "course_catalog": required_catalog_credits,
                "degree_requirement": required_requirement_credits,
                "semester_allocation": required_allocation.declared_total,
            },
            reason=(
                None
                if len(set(required_values)) == 1
                else "PDF 128쪽의 졸업요건 및 과목표 합계는 21학점이지만, PDF 129쪽 학기배분표는 24학점으로 서로 다릅니다."
            ),
            evidence=(
                []
                if len(set(required_values)) == 1
                else [
                    curriculum.program_requirements[0].evidence,
                    required_allocation.evidence,
                    next(
                        course.evidence
                        for course in curriculum.major_courses
                        if course.course_type == "major_required"
                    ),
                ]
            ),
            review_steps=(
                []
                if len(set(required_values)) == 1
                else [
                    "PDF 파일 128쪽(인쇄 281쪽) '4. 기본이수 학점구조표'의 전공필수 21학점을 확인합니다.",
                    "PDF 파일 129쪽(인쇄 282쪽) '5. 전공심화과정 학점배분구조표'의 전공필수 총계 24학점을 확인합니다.",
                    "PDF 파일 129쪽부터 시작하는 '7. 전공교육과정표'에서 전공필수 3학점 과목 7개와 0학점 과목 2개를 확인합니다. 학점 합계는 21입니다.",
                    "졸업 판정에는 기본이수 학점구조표를 우선 사용하되, 외부 정정 자료가 있으면 보정 파일에 근거와 함께 기록합니다.",
                ]
            ),
            correction_targets=(
                []
                if len(set(required_values)) == 1
                else [
                    f"uid={curriculum.program_requirements[0].uid}, field=major_required_credits",
                    f"uid={required_allocation.uid}, field=term_credits",
                    f"uid={required_allocation.uid}, field=declared_total",
                ]
            ),
        )
    )

    derived_competencies: dict[int, tuple[int, int]] = {}
    for competency_id in range(1, 6):
        related = [
            course
            for course in curriculum.major_courses
            if competency_id in course.competency_ids
        ]
        derived_competencies[competency_id] = (
            len(related),
            sum(course.credits for course in related),
        )
    declared_competencies = {
        item.competency_id: (item.course_count, item.credit_sum)
        for item in curriculum.competency_summary
    }
    checks.append(
        ValidationCheck(
            check_id="competency_summary",
            status=(
                "pass" if derived_competencies == declared_competencies else "warning"
            ),
            message="과목별 전공능력 연결을 128쪽 요약표와 비교",
            expected=declared_competencies,
            actual=derived_competencies,
        )
    )

    def comparable_profile_text(statement: ProfileStatement) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣①②③④⑤]", "", statement.text)

    corrected_profile_uids = {
        correction.target_uid
        for correction in corrections
        if correction.field == "text"
    }
    profile_pairs = list(zip(curriculum.profile_statements, pymupdf_profile))
    mismatched_profile = [
        primary
        for primary, secondary in profile_pairs
        if primary.uid not in corrected_profile_uids
        and comparable_profile_text(primary) != comparable_profile_text(secondary)
    ]
    profile_matches = (
        len(curriculum.profile_statements) == len(pymupdf_profile)
        and not mismatched_profile
    )
    checks.append(
        ValidationCheck(
            check_id="profile_text_cross_parser",
            status="pass" if profile_matches else "warning",
            message="교육목표·인재상·전공능력 일반 문장 교차 검사",
            expected="PyMuPDF와 pdfplumber의 문장 순서가 일치",
            actual={
                "pdfplumber_statement_count": len(curriculum.profile_statements),
                "pymupdf_statement_count": len(pymupdf_profile),
                "mismatched_statement_count": len(mismatched_profile),
            },
            reason=(
                None
                if profile_matches
                else "PDF 글꼴의 저장 순서 때문에 PyMuPDF가 일부 문장의 콜론과 단어 순서를 다르게 읽었습니다. 표 기반 졸업학점 계산에는 사용하지 않지만 문장 의미를 활용할 때 확인이 필요합니다."
            ),
            evidence=[item.evidence for item in mismatched_profile],
            review_steps=(
                []
                if profile_matches
                else [
                    "PDF 파일 128쪽(인쇄 281쪽)의 '2. 전공인재상'과 '3. 전공능력기반' 문장을 확인합니다.",
                    "정규화 JSON의 profile_statements에는 문장 구조를 더 잘 보존한 pdfplumber 결과가 저장되어 있습니다.",
                    "이 문장을 의사결정 규칙에 사용할 계획이 있을 때만 원문과 비교하고, 수정 시 보정 파일에 남깁니다.",
                ]
            ),
            correction_targets=[
                f"uid={item.uid}, field=text" for item in mismatched_profile
            ],
        )
    )

    spelling_candidates: list[tuple[str, str, str, EvidenceRef, str]] = []
    for statement in curriculum.profile_statements:
        if "운리의식" in statement.text:
            spelling_candidates.append(
                (
                    statement.uid,
                    "text",
                    "운리의식",
                    statement.evidence,
                    "윤리의식",
                )
            )
    for course in curriculum.major_courses:
        if course.name_en and "Architechture" in course.name_en:
            spelling_candidates.append(
                (
                    course.uid,
                    "name_en",
                    "Architechture",
                    course.evidence,
                    "Architecture",
                )
            )
    checks.append(
        ValidationCheck(
            check_id="source_spelling_candidates",
            status="warning" if spelling_candidates else "pass",
            message="PDF 텍스트층의 오탈자 후보 검사",
            expected=(
                [
                    {"source_text": source, "candidate": candidate}
                    for _, _, source, _, candidate in spelling_candidates
                ]
                if spelling_candidates
                else "오탈자 후보 없음"
            ),
            actual=[source for _, _, source, _, _ in spelling_candidates],
            reason=(
                "PyMuPDF와 pdfplumber는 같은 PDF 텍스트층을 사용하므로 인쇄 화면과 텍스트층의 글자가 다르면 둘 다 같은 값을 읽습니다. 자동 수정하지 않고 후보만 표시합니다."
                if spelling_candidates
                else None
            ),
            evidence=[evidence for _, _, _, evidence, _ in spelling_candidates],
            review_steps=(
                [
                    "PDF 파일 128쪽(인쇄 281쪽) '2. 전공인재상' 세 번째 문장의 '운리의식'이 화면에서 '윤리의식'인지 확인합니다.",
                    "PDF 파일 129쪽(인쇄 282쪽) CDA0016 영문 과목명의 'Architechture'가 발행기관 기준 오탈자인지 확인합니다.",
                    "원문 표기를 그대로 보존할지 정정 표기를 사용할지 결정하고, 정정할 경우 보정 파일에 이유를 기록합니다.",
                ]
                if spelling_candidates
                else []
            ),
            correction_targets=[
                f"uid={uid}, field={field}" for uid, field, _, _, _ in spelling_candidates
            ],
        )
    )
    return checks


def normalize_curriculum_pdf(
    pdf_path: Path, corrections_path: Path | None = None
) -> NormalizationResult:
    resolved_path = pdf_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")
    source_sha256 = _sha256(resolved_path)

    with pymupdf.open(resolved_path) as document:
        if document.page_count < PDF_PAGES[-1]:
            raise ValueError("PDF가 130쪽보다 짧습니다.")
        pages = [document[pdf_page - 1] for pdf_page in PDF_PAGES]
        printed_pages = {
            pdf_page: _printed_page(page)
            for pdf_page, page in zip(PDF_PAGES, pages)
        }
        pymupdf_table_counts = [len(page.find_tables().tables) for page in pages]
        pymupdf_profile = _profile_statements(pages[0], printed_pages)
        curriculum = CurriculumData(
            uid="curriculum:2022:computer-engineering",
            year=2022,
            college="공과대학",
            department="컴퓨터공학과",
            profile_statements=_pdfplumber_profile_statements(
                resolved_path, printed_pages
            ),
            program_requirements=_program_requirements(pages[0], printed_pages),
            credit_allocations=_credit_allocations(pages[1], printed_pages),
            designated_courses=_designated_courses(pages[1], printed_pages),
            major_courses=_major_courses(pages[1:], printed_pages),
            competency_summary=_competency_summary(pages[0]),
            catalog_summary=_catalog_summary(pages[2], printed_pages),
        )

    corrections = _load_corrections(corrections_path, source_sha256)
    _apply_corrections(curriculum, corrections)
    pdfplumber_credits, pdfplumber_table_counts = _pdfplumber_course_credits(
        resolved_path
    )
    checks = _validation_checks(
        curriculum,
        pdfplumber_credits,
        pymupdf_table_counts,
        pdfplumber_table_counts,
        pymupdf_profile,
        corrections,
    )
    if any(check.status == "fail" for check in checks):
        validation_status = "fail"
    elif any(check.status == "warning" for check in checks):
        validation_status = "warning"
    else:
        validation_status = "pass"

    return NormalizationResult(
        schema_version="1.0",
        source=SourceInfo(
            file_name=resolved_path.name,
            sha256=source_sha256,
            selected_pdf_pages=list(PDF_PAGES),
            printed_pages=printed_pages,
            extractors=[
                f"PyMuPDF {pymupdf.__version__}",
                f"pdfplumber {pdfplumber.__version__}",
            ],
        ),
        curriculum=curriculum,
        validation_status=validation_status,
        validation_checks=checks,
        corrections_applied=corrections,
    )


def write_normalized_json(result: NormalizationResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_review_markdown(result: NormalizationResult, output_path: Path) -> None:
    passed = [check for check in result.validation_checks if check.status == "pass"]
    review_items = [
        check for check in result.validation_checks if check.status != "pass"
    ]
    lines = [
        "# PDF 자동 정규화 검토 보고서",
        "",
        f"- 원본: `{result.source.file_name}`",
        f"- 대상: PDF {', '.join(map(str, result.source.selected_pdf_pages))}쪽",
        f"- 결과: **{result.validation_status.upper()}**",
        f"- 자동 통과: {len(passed)}건",
        f"- 확인 필요: {len(review_items)}건",
        "",
        "`PASS` 항목은 두 추출 엔진, 형식 검사 또는 문서 합계 검사를 통과했다. "
        "`WARNING` 항목은 원문을 임의 수정하지 않았으며 아래 위치만 사람이 확인하면 된다.",
        "",
    ]

    for index, check in enumerate(review_items, start=1):
        lines.extend(
            [
                f"## {index}. [{check.status.upper()}] {check.message}",
                "",
                f"- 검사 ID: `{check.check_id}`",
                f"- 기대값: `{json.dumps(check.expected, ensure_ascii=False)}`",
                f"- 확인값: `{json.dumps(check.actual, ensure_ascii=False)}`",
                f"- 이유: {check.reason or '자동 검사 불일치'}",
                "",
            ]
        )
        if check.evidence:
            lines.extend(
                [
                    "### 확인 위치",
                    "",
                    "| PDF 쪽 | 인쇄 쪽 | 표·구역 | 좌표 | 추출 원문 |",
                    "|---:|---:|---|---|---|",
                ]
            )
            for evidence in check.evidence:
                raw_text = evidence.raw_text.replace("|", "\\|")
                lines.append(
                    f"| {evidence.pdf_page} | {evidence.printed_page} | "
                    f"{evidence.section} | `{list(evidence.bbox)}` | {raw_text} |"
                )
            lines.append("")
        if check.review_steps:
            lines.extend(["### 확인 방법", ""])
            lines.extend(
                f"{step_index}. {step}"
                for step_index, step in enumerate(check.review_steps, start=1)
            )
            lines.append("")
        if check.correction_targets:
            lines.extend(["### 보정 가능한 대상", ""])
            lines.extend(f"- `{target}`" for target in check.correction_targets)
            lines.extend(
                [
                    "",
                    "원본 JSON을 직접 수정하지 말고 `data/corrections/2022_curriculum_p128_130_corrections.json`에 확인한 값과 이유를 기록한다.",
                    "",
                ]
            )

    lines.extend(
        [
            "## 자동 통과 항목",
            "",
            "| 검사 ID | 검사 내용 |",
            "|---|---|",
        ]
    )
    lines.extend(f"| `{check.check_id}` | {check.message} |" for check in passed)
    lines.append("")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
