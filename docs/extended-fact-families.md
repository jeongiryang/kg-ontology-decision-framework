# 확장 fact family 기반 답변 커버리지

이 문서는 답변 가능한 질문 범위를 넓히기 위해 도입한 **확장 fact family**를 설명한다.
근거 규칙(검증된 사실과 Evidence가 연결된 값만 답변에 쓴다)은 바뀌지 않는다.

- 대상 코드: `src/kg_builder/query/fact_families.py`
- 관련 문서: [Text-to-Cypher 안전 기반](text-to-cypher-safety.md), [Evidence 기반 한국어 답변 계층](evidence-answer-renderer.md)

---

## 1. 왜 필요했나

확장 전 질의 계층은 `CourseOffering`과 `Rule` 두 fact family에만 연결돼 있었다.
`FILTER_BINDINGS`가 가리키는 라벨이 7개뿐이었고, 답변 조립부도 이 두 family만 처리했다.

그 결과 Verified KG에 적재돼 있고 Evidence까지 붙어 있는 사실이 질의로 도달하지 못했다.
`data/verified/2026/2026_curriculum_kg_data.json` 기준으로 Evidence·Document를 제외한
사실 노드 1,006개 중 264개(26%)가 이 상태였다.

| 도달 불가였던 라벨 | 노드 수 |
|---|---|
| CreditAllocation | 117 |
| Alignment | 57 |
| RoadmapEntry | 43 |
| Competency | 10 |
| Condition / ConditionGroup | 10 |
| CurriculumAggregate | 8 |
| EducationGoal | 7 |
| CareerField / CourseRecommendation / TalentProfile | 9 |
| 기타 | 3 |

---

## 2. 확장한 범위

이번 확장은 다음 6개 family를 도달 가능하게 만들었다. 모두 `(fact)-[:SUPPORTED_BY]->(Evidence)`
직접 경로를 100% 보유한 라벨이다.

| SelectionMode | fact label | 노드 수 | 답변 예 |
|---|---|---|---|
| `CREDIT_ALLOCATION_LIST` | CreditAllocation | 117 | 학년·학기별 교양 학점 배분 |
| `ROADMAP_LIST` | RoadmapEntry | 43 | 학년·학기 권장 이수 로드맵 |
| `EDUCATION_GOAL_LIST` | EducationGoal | 7 | 학과 교육목표 |
| `CAREER_FIELD_LIST` | CareerField | 3 | 졸업 후 진출 분야 |
| `TALENT_PROFILE_LIST` | TalentProfile | 3 | 학과 인재상 |
| `COURSE_RECOMMENDATION_LIST` | CourseRecommendation | 3 | 학과 권장 교양 과목 |

학년·학기 필터 이름은 family마다 다르다. `CourseRecommendation`은 자체 속성인
`recommended_grade_year`·`recommended_semester`를 쓰고, `grade_year`·`semester`는 받지 않는다.

합계 176개 노드이며, 도달 불가였던 264개의 67%에 해당한다.

### 범위 밖으로 남긴 것

| 대상 | 남긴 이유 |
|---|---|
| Condition, ConditionGroup | `SUPPORTED_BY`가 없어 직접 근거 경로 요건을 만족하지 못한다 |
| Alignment, Competency | 두 노드 사이의 연결 자체가 사실이라 목록형 렌더링으로 표현되지 않는다 |
| CurriculumAggregate | 값 종류(`aggregate_type`)마다 단위가 달라 별도 렌더 설계가 필요하다 |
| 대학(Institution) 교육목표 | `Department` 경로가 아니라 `Institution` 경로에 붙어 있어 앵커가 다르다 |
| 학생·수강내역 | 온톨로지에 라벨이 없다 (이슈 #17) |

---

## 3. 어떻게 근거 규칙을 유지하는가

확장한 것은 **"무엇을 물어볼 수 있는가"**뿐이고, **"무엇을 근거로 답하는가"**는 그대로다.

1. **등록 조건이 근거를 강제한다.** family로 등록하려면 온톨로지가 선언한 라벨이어야 하고,
   `status`로 `VERIFIED` 판별이 가능해야 하며, Evidence 직접 경로가 있어야 한다.
2. **selection_mode가 fact label을 1:1로 확정한다.** 한 질의의 결과는 항상 한 라벨에서만
   나오므로 `ProvenanceContract`의 단일 fact label 계약이 유지된다.
3. **검증 관문은 그대로 통과한다.** 확장 family의 Cypher도 `CypherValidator` → `EXPLAIN` →
   `ResultValidator`를 예외 없이 지난다. 검증기를 우회하는 경로는 추가하지 않았다.
4. **Claim 값은 승인된 행에서만 온다.** `ClaimValidator`가 항목 속성을 하나씩 원본 행과
   다시 맞춰 본다. 표시용 공백 제거 외의 변형은 모두 불일치로 잡힌다.
5. **집계를 만들지 않는다.** 확장 family는 합계·개수 Claim을 생성하지 않는다. 예를 들어
   학점 배분표는 원문이 합계 행(`is_total`)을 따로 제공하므로, 항목을 더해 만든 값은
   원문에 근거가 없다. 합계가 필요하면 그 합계 행 자체를 조회한다.

### 원문의 빈칸과 합계 행

학점 배분표 117행 중 42행은 원문이 빈칸이라 `allocated_credits`가 `null`이다. 빈칸을 `0`으로
바꾸지 않는 것이 데이터 계약이므로, `ResultValidator`는 요청 필드가 `null`인 행을 거부한다.
그대로 두면 표 전체가 답변 불가가 되므로, `CreditAllocation` family는 다음 두 필터를 기본으로 채운다.

| 필터 | 기본값 | 이유 |
|---|---|---|
| `source_was_blank` | `false` | 원문에 값이 적힌 행만 답변 대상으로 삼는다 |
| `is_total` | `false` | 합계 행은 원문에 학년·학기가 없어 개별 행과 한 결과에 섞이지 않는다 |

두 값은 질문 계획에서 뒤집을 수 있다. 합계를 물으면 `is_total=true`로 조회하고 `grade_year`와
`semester`는 요청하지 않는다. 어느 경우든 값은 원문 행에서 오며, 항목을 더해 만들지 않는다.

### 계획 모델이 빠뜨린 구조적 값의 보강

로컬 LLM은 확장 family의 계약을 자주 빠뜨린다. 실측에서 확인한 것은 두 가지였다.

- 학년도가 질문에 없으면 `academic_year`를 넣지 않는다 → `missing required scope filters`
- 필수 필드(`credit_category`, `is_total` 등)를 요청 목록에서 누락한다

이 둘은 답변 값이 아니라 **조회 범위와 결과 형태**이므로, planner가 질문별 분기 없이 보강한다.

| 보강 대상 | 규칙 |
|---|---|
| `mandatory_fields` | family가 선언한 최소 필드를 요청 목록에 더한다 |
| `default_filters` | family가 선언한 기본 범위 필터를 채운다. 계획에 값이 있으면 그 값이 이긴다 |
| `academic_year` | 적재된 학년도가 **하나뿐일 때만** 그 값으로 채운다. 둘 이상이면 채우지 않고 계약 위반으로 되돌린다 |

학년도 보강은 값을 추정하는 것이 아니라 선택지가 하나뿐인 범위를 닫는 것이다. 다년도
데이터가 들어오면 이 보강은 자동으로 비활성화되고 계획 모델이 학년도를 지정해야 한다.

### 좁히지 못했을 때 거절하지 않고 넓히기

정밀도 실패를 답변 거부로 처리하면, 근거가 있는데도 답하지 못하는 질문이 많아진다.
근거 계약이 요구하는 것은 정밀도가 아니라 Evidence이므로, 다음 경우에는 좁히는 대신
넓혀서 답하고 그 사실을 답변 앞에 밝힌다.

| 상황 | 동작 | 사유 코드 |
|---|---|---|
| 어느 이수요건인지 못 고름 | 질문과 낱말이 겹치는 이수요건만 조회 | `RULE_TOPIC_NARROWED` |
| 겹치는 낱말이 하나도 없음 | 확인된 이수요건 전부를 조회 | `RULE_TOPIC_UNRESOLVED` |
| 적재 후보가 하나뿐인 학년도·학과를 되물음 | 그 값으로 조회 | 없음(확정이므로 안내하지 않음) |
| 모드가 쓸 수 없는 필터가 섞임 | 그 필터를 떨어뜨리고 조회 | 없음(그 모드에서 무의미한 조건) |

넓힌 조회도 근거 요구를 낮추지 않는다. `evidence_required`는 항상 참으로 고정되며,
결과는 종전과 똑같이 `VERIFIED` 사실과 Evidence 검증을 거친다.

확인된 이수요건을 전부 쏟아내면 균형교양을 물었는데 전공 요건까지 답에 섞인다. 질문의
낱말과 규칙 원문이 겹치는 **낱말 수**를 세어 가장 잘 겹치는 것만 남긴다. 한국어는 낱말에
조사가 붙으므로 앞에서부터 잘라 가며 맞춘다(`교양은` → `교양`). 겹친 길이가 아니라 수를
먼저 보는 이유는, 조사가 붙은 낱말이 우연히 길게 겹친 규칙 하나가 이겨 정작 물어본 요건이
빠지기 때문이다.

요청 필드는 고른 규칙이 **모두** 갖고 있는 것만 넣는다. 수치가 아닌 요건은 `value`가
비어 있고, 빈 값을 `0`으로 바꾸지 않는 것이 데이터 계약이므로 한 건만 비어도 결과 검증이
조회 전체를 막는다. 어떤 규칙이 무엇을 갖는지는 적재 데이터에서 계산한다.

넓히는 경로가 다른 질문에 답하는 통로가 되지 않도록 두 가지를 막는다.

- 질문이 적재된 과목을 이름으로 지목했으면 이수요건 전체로 넓히지 않는다.
- 지목된 과목을 가리키는 필터가 계획에 없으면 그 계획으로 답하지 않고 되묻는다.

### 없는 값을 요청하지 않기

수치가 아닌 이수요건은 `value`·`unit`·`operator`가 비어 있다. 원문의 빈 값을 `0`으로
바꾸지 않는 것이 이 저장소의 계약이므로, 그런 규칙이 한 건이라도 섞이면 결과 검증이
조회 전체를 막는다. 없는 값을 채우는 대신, 그 필드를 갖지 않은 규칙을 조회 대상에서
뺀다. 어떤 규칙이 무엇을 갖는지는 적재된 bundle에서 읽으며 질문을 보지 않는다.

### 요청 필드로 모드 교정하기

작은 모델은 물음의 종류를 `SINGLE_COURSE`로 몰아 놓고 요청 필드는 옳은 family의 것을
고르는 일이 잦다. 요청 필드를 모두 담을 수 있는 모드가 **하나뿐일 때만** 그 모드로
고친다. 후보가 둘 이상이면 손대지 않는다. 어느 필드가 어느 family 소유인지는 선언이
정하므로 질문 문자열을 보지 않는다.

### 계획이 왜 막혔는지 남기기

계획 단계에서 멈춘 요청도 `logs/query-runs/<request_id>.json`에 기록된다.
`planning_attempts`에 시도마다 고른 모드, 채운 필터 **이름**, 요청 필드, 부족 코드,
걸린 계약 문구가 남는다. 질문에서 온 **값**은 담지 않는다.

```bash
python3 -c "
import json
d = json.load(open('logs/query-runs/<request_id>.json'))
for a in d.get('planning_attempts', []):
    print(a['attempt'], a['outcome'], a['selection_mode'], a['filter_names'], a['contract_error'])
"
```

### 같은 필터 이름, 다른 바인딩

`grade_year`는 `CourseOffering`에서는 배열 속성(`PARAMETER_IN_PROPERTY`)이지만
`CreditAllocation`에서는 정수 속성(`EQUALS`)이다. `resolve_filter_bindings()`가
selection_mode로 바인딩을 확정하고, `CypherValidator`가 그 바인딩과 다른 형태를 거부한다.

---

## 4. family 하나 추가하기

`fact_families.py`의 `EXTENDED_FAMILIES`에 항목을 추가하면 질의·생성·검증 경로가 따라온다.

1. `SelectionMode`에 값을 추가한다.
2. `_department_scoped(...)`로 family를 선언한다 — fact label, id 속성, 앵커 MATCH,
   노출 필드, 필터 오버라이드, 필수 필드.
3. 새 필터를 쓰면 `BASE_FILTER_BINDINGS`에 추가하고, 통제어휘면 `VOCABULARY_FILTERS`에도 넣는다.
4. `ClaimBuilder._extended_item`과 `EXTENDED_CLAIM_KINDS`에 항목 변환을 추가한다.
5. `ClaimValidator.EXTENDED_ITEM_COLUMNS`에 속성↔컬럼 대응을 추가한다.
6. `KoreanAnswerRenderer`에 렌더 분기를 추가한다.
7. 필터를 추가했으면 생성 스키마를 다시 만든다.

```bash
uv run python -m kg_builder.query.schema_exporter generate
```

---

## 5. 확인 방법

### LLM 없이 확인하기

`kg_builder.answer.plan_cli`는 QueryPlan을 직접 받아 **LLM 두 호출만** 결정론적 대체물로
바꾸고 나머지 경로는 그대로 통과시킨다. 안전 관문 6개와 근거 검증이 모두 실제로 실행된다.

```bash
# 데이터베이스를 건드리지 않고 예시 계획만 출력
uv run python -m kg_builder.answer.plan_cli --print-examples

# 확장 family 6개를 한 번에 실행
uv run python -m kg_builder.answer.plan_cli --all-examples

# 계획 하나를 직접 지정
uv run python -m kg_builder.answer.plan_cli --plan '{
  "selection_mode": "CAREER_FIELD_LIST",
  "filters": {"academic_year": 2026, "department_id": "department:cwnu:cse"},
  "requested_fields": ["name_ko", "field_order"],
  "evidence_required": true
}'
```

`NEO4J_QUERY_*` 읽기 전용 설정이 필요하다. `.env.example`을 참고한다.

### 자연어로 확인하기

로컬 LLM(`KG_LLM_*`)까지 설정한 뒤에는 기존 경로를 그대로 쓴다.

```bash
uv run python -m kg_builder.answer.cli "컴퓨터공학과 졸업 후 진출 분야는?"
uv run python -m evidence_chat.server
```

### 테스트

```bash
uv run pytest -q tests/test_extended_fact_families.py
```

이 테스트는 family 선언이 온톨로지와 맞는지, 생성 Cypher가 검증기를 통과하는지,
그리고 **값을 위조하거나 검증되지 않은 행이 섞이면 거부되는지**를 함께 확인한다.

---

## 6. 알려진 제한

- 확장 family는 모두 학년도와 학과로 범위가 고정된다. 학과 없는 공통 교양 질문은
  기존 `Rule` 경로를 쓴다.
- 학점 배분 질의는 기본적으로 원문에 값이 적힌 개별 행만 본다. 빈칸 행이 어디인지
  묻는 질문(예: "2학년에는 기초교양이 배정돼 있지 않나?")은 `source_was_blank=true`로
  조회해야 하며, 이때 `allocated_credits`는 요청할 수 없다.
- `EDUCATION_GOAL_LIST`는 학과 교육목표만 반환한다. 대학 교육목표 3건은 `Institution`
  경로에 있어 이 family로 조회되지 않는다.
- `RoadmapEntry`는 원문 표기(`raw_label`)를 그대로 반환한다. 36건은 `MAPS_COURSE`로
  `Course`에 연결돼 있으나, 이번 확장은 로드맵 항목 자체만 답변에 사용한다.
- 자연어 질문이 어느 selection_mode로 계획되는지는 로컬 LLM 품질에 달려 있다.
  계획을 직접 지정하면 확장 family 6종이 모두 답변된다. 자연어 경로는 실행마다 표본이
  달라 문항별 결과가 흔들린다.
- 넓힌 답변의 이수요건 선별은 낱말 겹침에 기댄다. `졸업하려면 총 몇 학점이 필요해?`에서는
  `필요해`가 `필요한 잔여학점`과 겹쳐, 정작 `졸업학점 기준은 130학점` 규칙이 빠진다.
  좁히지 못했다는 안내를 답변 앞에 붙여 밝히지만 선별이 항상 옳지는 않다.
- 응답 스키마의 `filters`는 `required`가 비어 있어 빈 객체도 유효하다. 모드별 필수
  필터를 스키마로 강제하는 방안은 아직 검증하지 않았다.
