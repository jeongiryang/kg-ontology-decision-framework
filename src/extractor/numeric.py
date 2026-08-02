"""숫자 셀 구조 분해 — 조용한 오염(silent coercion) 방지.

기존 docling 브랜치의 safe_int()가 '4주'→4, 빈칸→0, '12 3'→12 로
경고 없이 오염시킨 것에 대한 직접적인 교정이다. 여기서는:
  빈칸        → value=None, kind="empty"      (0이 아님)
  "3"         → value=3,    kind="number"
  "3(3)"      → value=3, secondary=3, kind="dual"   (학점(시간) 형식)
  "4주"       → value=4, unit="주", kind="unit"     (기간이지 시수가 아님 — 소비자가 판단)
  "12 3"      → value=None, kind="ambiguous"        (해석 불가 — 사람에게)
  "전공필수"  → kind="text"
"""

from __future__ import annotations

import re

from src.extractor.models import NumericValue

_NUM = r"\d+(?:\.\d+)?"
_RE_NUMBER = re.compile(rf"^({_NUM})$")
_RE_DUAL = re.compile(rf"^({_NUM})\s*\(\s*({_NUM})\s*\)$")  # 3(3), 26(78)
_RE_UNIT = re.compile(rf"^({_NUM})\s*([가-힣A-Za-z]{{1,4}})$")  # 4주, 15시간
_RE_ANY_NUM = re.compile(_NUM)


def parse_numeric(raw: str) -> NumericValue:
    s = raw.strip()
    if not s:
        return NumericValue(raw=raw, kind="empty")

    m = _RE_NUMBER.match(s)
    if m:
        return NumericValue(raw=raw, kind="number", value=float(m.group(1)))

    m = _RE_DUAL.match(s)
    if m:
        return NumericValue(
            raw=raw, kind="dual", value=float(m.group(1)), secondary=float(m.group(2))
        )

    m = _RE_UNIT.match(s)
    if m:
        return NumericValue(raw=raw, kind="unit", value=float(m.group(1)), unit=m.group(2))

    nums = _RE_ANY_NUM.findall(s)
    if len(nums) >= 2:
        # "12 3", "3, 4" 등 — 어느 숫자가 맞는지 코드가 정할 수 없다
        return NumericValue(raw=raw, kind="ambiguous")
    if len(nums) == 1 and len(s) <= 12:
        # "제3장" 같은 짧은 혼합 문자열 — 숫자 하나가 섞였지만 단위 형식도 아님
        return NumericValue(raw=raw, kind="ambiguous")
    return NumericValue(raw=raw, kind="text")


def looks_numeric_column(values: list[str], threshold: float = 0.5) -> bool:
    """열의 값 과반이 숫자 계열(number/dual/unit/empty)이면 숫자 열로 본다."""
    if not values:
        return False
    kinds = [parse_numeric(v).kind for v in values if v.strip()]
    if not kinds:
        return False
    numeric_like = sum(1 for k in kinds if k in ("number", "dual", "unit"))
    return numeric_like / len(kinds) >= threshold
