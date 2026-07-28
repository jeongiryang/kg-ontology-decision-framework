from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceRef(StrictModel):
    pdf_page: int = Field(ge=128, le=130)
    printed_page: int = Field(ge=1)
    section: str
    bbox: tuple[float, float, float, float]
    raw_text: str
    extraction_method: str


class ProfileStatement(StrictModel):
    uid: str
    kind: Literal["educational_goal", "talent_profile", "competency_definition"]
    order: int = Field(ge=1)
    text: str
    evidence: EvidenceRef


class ProgramRequirement(StrictModel):
    uid: str
    track: str
    basic_general_credits: int
    balanced_general_credits: int
    general_remaining_credits: int
    general_total_credits: int
    major_foundation_credits: int | None
    major_required_credits: int
    major_elective_credits: int
    major_total_credits: int
    graduation_remaining_credits: int
    graduation_total_credits: int
    minimum_major_recognition_enabled: bool | None
    evidence: EvidenceRef


class CreditAllocation(StrictModel):
    uid: str
    section: str
    category: str
    term_credits: list[int] = Field(min_length=8, max_length=8)
    declared_total: int | None
    evidence: EvidenceRef


class Course(StrictModel):
    uid: str
    code: str = Field(pattern=r"^(CDA|GEA)\d{4}$")
    course_type: Literal["designated_general", "major_required", "major_elective"]
    category: str
    area: str | None = None
    name_ko: str
    name_en: str | None = None
    credits: int = Field(ge=0)
    total_hours: int | None = Field(default=None, ge=0)
    lecture_hours: int | None = Field(default=None, ge=0)
    practice_hours: int | None = Field(default=None, ge=0)
    practice_weeks: int | None = Field(default=None, ge=0)
    offering: str
    competency_ids: list[int] = Field(default_factory=list)
    minor_required: bool = False
    source_order: int = Field(ge=1)
    evidence: EvidenceRef


class CompetencySummaryItem(StrictModel):
    competency_id: int = Field(ge=1, le=5)
    course_count: int = Field(ge=0)
    credit_sum: int = Field(ge=0)


class CatalogSummary(StrictModel):
    declared_course_count: int
    declared_credit_sum: int
    evidence: EvidenceRef


class CurriculumData(StrictModel):
    uid: str
    year: int
    college: str
    department: str
    profile_statements: list[ProfileStatement]
    program_requirements: list[ProgramRequirement]
    credit_allocations: list[CreditAllocation]
    designated_courses: list[Course]
    major_courses: list[Course]
    competency_summary: list[CompetencySummaryItem]
    catalog_summary: CatalogSummary


class SourceInfo(StrictModel):
    file_name: str
    sha256: str
    selected_pdf_pages: list[int]
    printed_pages: dict[int, int]
    extractors: list[str]


class ValidationCheck(StrictModel):
    check_id: str
    status: Literal["pass", "warning", "fail"]
    message: str
    expected: object | None = None
    actual: object | None = None
    reason: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    review_steps: list[str] = Field(default_factory=list)
    correction_targets: list[str] = Field(default_factory=list)


class Correction(StrictModel):
    check_id: str
    target_uid: str
    field: str
    value: Any
    reason: str
    reviewer: str


class CorrectionSet(StrictModel):
    schema_version: str
    source_sha256: str
    description: str | None = None
    corrections: list[Correction]


class NormalizationResult(StrictModel):
    schema_version: str
    source: SourceInfo
    curriculum: CurriculumData
    validation_status: Literal["pass", "warning", "fail"]
    validation_checks: list[ValidationCheck]
    corrections_applied: list[Correction] = Field(default_factory=list)
