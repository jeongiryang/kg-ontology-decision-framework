"""문서 프로파일 — 코드에 하드코딩하지 않고 여기 모아두는 도메인 용어 사전.

다른 도메인(의료·법률 등) 문서를 처리할 때는 이 파일(또는 외부 JSON)만 바꾼다.
조사 결과 이 문서 하나에서만 표 헤더 시그니처가 27종이므로,
컬럼은 위치(인덱스)가 아니라 **헤더 텍스트 정규화 매칭**으로만 식별한다.
"""

from __future__ import annotations

import re

# 학수번호 패턴 (예: CDA0155, GEA7004). 접두어는 실측 67종이므로 일반형으로 둔다.
COURSE_CODE = re.compile(r"\b[A-Z]{2,4}\d{4}\b")

# 합계행 라벨 — 첫 두 칸 안에서 이 패턴이 나오면 합계행으로 본다
TOTAL_ROW = re.compile(r"^(계|합\s*계|총\s*계|소\s*계)$")

# "43과목" 형태의 개수 표기
COUNT_PATTERN = re.compile(r"(\d+)\s*과\s*목")

# 이수구분(카테고리) 어휘 — pdfplumber 보충 및 열 식별에 사용
CATEGORY_TERMS = [
    "전공필수", "전공선택", "전공기초", "전공심화",
    "교양필수", "교양선택", "기초교양", "균형교양", "일반교양",
    "학과지정", "자유선택", "부전공",
]

# 열 의미 식별용 헤더 어휘 (정규화: 공백·점·괄호 제거 후 부분일치)
HEADER_LEXICON: dict[str, list[str]] = {
    "category": ["이수구분", "구분"],
    "code": ["학수번호", "과목번호", "교과목번호"],
    "name": ["과목명", "교과목명", "과목명칭"],
    "credit": ["학점"],
    "semester": ["개설학년학기", "개설학기", "학년학기", "이수학기"],
}


def normalize_header(s: str) -> str:
    """헤더 비교용 정규화 — 공백·점·괄호·개행 제거."""
    return re.sub(r"[\s.·()\[\]]+", "", s)


def classify_header(header_text: str) -> str | None:
    """헤더 텍스트가 어떤 의미 열인지 판별. 못 찾으면 None."""
    h = normalize_header(header_text)
    if not h:
        return None
    for role, terms in HEADER_LEXICON.items():
        for t in terms:
            if t in h:
                return role
    return None


def normalize_category(s: str) -> str | None:
    """셀 값에서 이수구분 용어를 찾아 표준형으로 돌려준다."""
    h = normalize_header(s)
    for term in CATEGORY_TERMS:
        if term in h:
            return term
    return None
