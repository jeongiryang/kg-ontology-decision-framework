# Verified KG 읽기 전용 질의·Evidence 응답 계층

## 1. 목적

이 계층은 2026학년도 공통 교양 이수요건과 컴퓨터공학과 교육과정 Verified KG를 사전 정의된 Intent로 조회하고, 답과 함께 검증된 PDF 근거를 구조화해 반환한다.

```text
구조화 요청
→ Intent·파라미터 검증
→ 사전 정의된 읽기 전용 Cypher
→ Neo4j 조회
→ VERIFIED 상태·Evidence 확인
→ 구조화 응답
```

자유 형식 자연어 분석, Text-to-Cypher, LLM 답변 생성, HTTP API와 UI는 현재 범위가 아니다.

## 2. 지원 Intent

| Intent | 필수 파라미터 | 선택 파라미터 | 반환 내용 |
|---|---|---|---|
| `GET_GENERAL_EDUCATION_MIN_CREDITS` | `academic_year` | `department`, `major_type`, `admission_type` | 일반 적용 대상의 교양 최소 이수학점 |
| `GET_BALANCED_GENERAL_REQUIREMENT` | `academic_year` | 없음 | 균형교양 최소 학점과 영역별 과목 요건 |
| `GET_TRANSFER_GENERAL_EXEMPTION` | `academic_year`, `admission_type=TRANSFER` | 없음 | 편입생 교양 이수 의무 면제 여부 |
| `GET_COURSE_OFFERING` | `academic_year`, `department`, `course_code` 또는 `course_name` 중 하나 | 없음 | VERIFIED 과목 편성의 학년·학기·학점·시수 |
| `GET_MAJOR_REQUIRED_COURSES` | `academic_year`, `department` | `major_type` | 컴퓨터공학과 전공필수 과목 목록과 학점 합계 |
| `GET_COURSE_COMPLETION_TYPE` | `academic_year`, `department`, `course_code` 또는 `course_name` 중 하나 | 없음 | 과목의 VERIFIED 이수구분 |

현재 데이터 범위는 `academic_year=2026`과 학과별 조회의 `컴퓨터공학과`다. 학과는 `컴퓨터공학과`, `CSE`, `cse`, `department:cwnu:cse`를 같은 대상으로 해석한다. 이 범위를 벗어나면 상식으로 답하지 않고 `OUT_OF_SCOPE`를 반환한다.

## 3. 요청 계약

```json
{
  "intent": "GET_COURSE_OFFERING",
  "parameters": {
    "academic_year": 2026,
    "department": "컴퓨터공학과",
    "course_name": "자료구조"
  }
}
```

- 지원하지 않는 Intent, 알 수 없는 필드와 빈 문자열을 거부한다.
- `academic_year`는 4자리 정수여야 한다.
- `major_type`과 `admission_type`은 온톨로지 통제어휘만 허용한다.
- 과목 조회에는 `course_code`와 `course_name` 중 정확히 하나만 사용한다.
- 입력값은 Cypher 값 파라미터로만 전달하며 라벨·관계·속성명으로 삽입하지 않는다.

## 4. 응답 계약

```json
{
  "intent": "GET_COURSE_OFFERING",
  "answerability": "ANSWERABLE",
  "answer": {
    "course": {
      "course_id": "course:cwnu:CDA0008",
      "course_code": "CDA0008",
      "course_name": "자료구조"
    },
    "offerings": [
      {
        "offering_id": "offering:cwnu:2026:cse:CDA0008:g2:FIRST:MAJOR_ELECTIVE",
        "grade_year": [2],
        "semester": "FIRST",
        "credits": 3,
        "completion_type": "MAJOR_ELECTIVE"
      }
    ]
  },
  "scope": {
    "academic_year": 2026,
    "department": "컴퓨터공학과",
    "course_name": "자료구조"
  },
  "evidence": [
    {
      "evidence_id": "evidence:...",
      "excerpt_page": 17,
      "source_pdf_page": 262,
      "printed_page": 254,
      "source_text": "PDF의 짧은 원문"
    }
  ],
  "warnings": []
}
```

`answerability`는 다음과 같다.

| 값 | 의미 |
|---|---|
| `ANSWERABLE` | VERIFIED 사실과 하나 이상의 VERIFIED Evidence가 있음 |
| `CLARIFICATION_REQUIRED` | 동명 과목 등 복수 후보를 하나로 결정할 조건이 부족함 |
| `UNRESOLVED` | 보류 데이터이거나 VERIFIED Evidence가 없어 확정할 수 없음 |
| `OUT_OF_SCOPE` | 현재 학년도·학과·문서 범위 밖임 |
| `NOT_FOUND` | 지원 범위 안에서 일치하는 VERIFIED 사실이 없음 |

## 5. Evidence 및 검증 상태 정책

- 모든 `ANSWERABLE` 응답은 최소 하나의 `VERIFIED Evidence`를 포함한다.
- `Rule.status`, `CourseOffering.status`, `Evidence.verification_status`가 모두 `VERIFIED`인 경로만 확정 답변에 사용한다.
- `REVIEW_REQUIRED` 데이터는 확정 답변에서 제외한다.
- DB에 편성이 없더라도 Verified unresolved 목록에 해당 과목이 있으면 `NOT_FOUND` 대신 `UNRESOLVED`를 반환한다.
- 발췌 PDF, 원본 PDF, 인쇄 페이지와 `raw_text`를 각각 보존한다.
- 동명 과목이 여러 학수번호로 확인되면 후보를 반환하고 학수번호를 요청한다.

## 6. 읽기 전용 보안 정책

- 각 Intent는 코드에 등록된 Cypher 템플릿 하나만 사용한다.
- 모든 값은 Neo4j 파라미터로 전달한다.
- 임의 Cypher 입력 인터페이스는 제공하지 않는다.
- 템플릿 검사에서 `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH`, `REMOVE`, `DROP`, `CALL`, `LOAD CSV`, `FOREACH`를 거부한다.
- Neo4j 드라이버의 `execute_read` 트랜잭션만 사용한다.
- 연결 설정은 기존 로컬 URI 제한과 비밀번호 비노출 정책을 그대로 따른다.

## 7. CLI 실행

로컬 `.env`와 이미 적재된 Neo4j가 필요하다. 실제 비밀번호는 명령이나 문서에 기록하지 않는다.

```bash
uv run python -m kg_builder.query_cli \
  --request '{"intent":"GET_GENERAL_EDUCATION_MIN_CREDITS","parameters":{"academic_year":2026}}'
```

```bash
uv run python -m kg_builder.query_cli \
  --request '{"intent":"GET_COURSE_OFFERING","parameters":{"academic_year":2026,"department":"컴퓨터공학과","course_code":"CDA0008"}}'
```

## 8. 테스트

연결 없는 단위 테스트:

```bash
uv run pytest
```

기존 로컬 DB에 대한 읽기 전용 통합 테스트:

```bash
KG_NEO4J_INTEGRATION=1 uv run pytest tests/test_query_integration.py
```

통합 테스트는 실행 전후 노드 `1,518`, 관계 `3,260`, Evidence `511`이 동일한지 검사하며 데이터를 적재·수정·삭제하지 않는다.

## 9. 현재 제한과 다음 단계

- 공통 교양은 현재 Verified 공통 기본 규칙을 반환한다. 특수 학생군의 모든 조합을 자동 판정하지 않는다.
- `major_type`은 전공필수 편성 목록을 바꾸는 필터가 아니라 응답 범위 메타데이터로만 보존한다.
- 개설학기 `이룸`, 주 단위 현장실습, 경과조치 연도 범위 등 unresolved 항목은 추정하지 않는다.
- 다음 단계는 학생 예상 질문을 Intent와 파라미터로 매핑하는 자연어 질의 분석 및 평가 계약이다. 그 전까지 질문 문장별 정답이나 Cypher를 하드코딩하지 않는다.
