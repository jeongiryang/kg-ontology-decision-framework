# Text-to-Cypher 스키마·검증·실행 안전 기반

## 1. 목적과 PR #12 기준선

PR #12의 결정론적 질의 계층은 6개 고정 Intent와 검토된 Cypher 템플릿으로 현재 핵심 질문을 안전하게 처리한다. 이 문서의 동적 질의 계층은 그 기준선을 대체하지 않으며, 다음 단계에서 로컬 LLM이 생성할 `QueryPlan`과 Cypher를 검증하기 위한 별도 내부 파이프라인이다.

```text
자연어 질문
→ [다음 작업: 로컬 LLM]
→ QueryPlan + 후보 Cypher
→ 관련 온톨로지 스키마 선택
→ Cypher 정적 검증
→ Neo4j EXPLAIN
→ execute_read
→ 결과·Evidence 검증
→ 단계별 추적
→ [다음 작업: 근거 포함 답변 생성]
```

이번 구현에는 자연어 분석, Cypher 생성, LLM 호출과 최종 답변 문장 생성이 포함되지 않는다. 외부 Cypher를 직접 입력하는 공개 CLI나 API도 제공하지 않는다.

## 2. 구성 요소

| 파일 | 책임 |
|---|---|
| `ontology/llm_query_schema.json` | LLM 질의 컨텍스트용 기계 판독 스키마 |
| `query/schema_catalog.py` | 원본 명세와 생성 스키마 SHA 검증, 라벨·관계·속성 조회 |
| `query/schema_exporter.py` | `ontology_spec.json`에서 LLM용 스키마를 결정론적으로 생성 |
| `query/query_plan.py` | 자연어 분석 단계가 반환할 구조화 계획 계약 |
| `query/cypher_validator.py` | 명세 기반 정적 Cypher 검증 |
| `query/query_explainer.py` | 검증된 Cypher에 대한 Neo4j `EXPLAIN` 안전 검사 |
| `query/query_executor.py` | EXPLAIN을 통과한 객체만 실행하는 읽기 전용 실행기 |
| `query/result_validator.py` | 범위·필드·VERIFIED Evidence·중복 행 검증 |
| `query/query_trace.py` | 요청별 단계 상태와 오류 코드 기록 |
| `query/safety_pipeline.py` | 전체 단계를 순서대로 연결하고 실패 후 단계를 차단 |

기존 `query_contracts.py`, `cypher_queries.py`, `query_service.py`, `query_cli.py`는 PR #12의 결정론적 6개 Intent를 계속 담당한다.

## 3. LLM 질의용 스키마 자동 생성

유일한 온톨로지 원본은 `ontology/ontology_spec.json`이다. `llm_query_schema.json`은 사람이 별도로 관리하는 스키마가 아니라 다음 명령의 생성물이다.

```bash
uv run python -m kg_builder.query.schema_exporter generate
```

생성 결과가 원본과 일치하는지 확인한다.

```bash
uv run python -m kg_builder.query.schema_exporter check
```

생성 파일에는 다음이 포함된다.

- 원본 온톨로지 이름·버전·SHA-256
- 26개 노드 라벨과 라벨별 ID·속성·필수 속성
- 31개 관계 타입과 방향·endpoint·관계 속성
- 모든 controlled vocabulary
- VERIFIED 사실과 Evidence 정책
- 읽기 전용 및 최대 100행 정책

동일 원본에서 반복 생성한 내용은 바이트 단위로 동일하다. 원본 SHA-256 또는 버전이 달라지면 카탈로그 로딩과 테스트가 실패한다.

## 4. QueryPlan 계약

다음 작업의 자연어 분석기는 질문에 대한 답이나 Cypher를 하드코딩하는 대신 구조화된 계획을 반환해야 한다.

```json
{
  "question": "2026년 컴공 3학년 2학기 전공선택 과목은?",
  "filters": {
    "academic_year": 2026,
    "department_code": "컴퓨터공학과",
    "grade_year": 3,
    "semester": "SECOND",
    "completion_type": "MAJOR_ELECTIVE"
  },
  "requested_fields": [
    "course_code",
    "name_ko",
    "credits"
  ],
  "evidence_required": true,
  "intent": "선택적인 설명·로깅 메타데이터"
}
```

- `academic_year`와 `department_code`는 현재 필수 범위다.
- 지원 필터만 허용하고 타입과 통제어휘를 검증한다.
- `requested_fields`는 온톨로지에 실제 선언된 속성명만 허용한다.
- `intent`는 Cypher 선택 키가 아니라 선택적 설명·추적 메타데이터다.
- 필터 값은 후보 Cypher에서 동일한 이름의 파라미터로 사용해야 한다.

## 5. 정적 Cypher 검증

검증기는 주석과 문자열 리터럴을 구분해 토큰을 검사하고 다음을 거부한다.

- 쓰기·삭제·DDL·프로시저·관리 명령
- 다중 statement와 `UNION`
- backtick 동적 식별자
- 원본 명세에 없는 라벨·관계·속성
- 관계 방향 또는 endpoint 불일치
- QueryPlan 필터의 문자열 직접 삽입 또는 파라미터 누락
- QueryPlan에 없는 추가 파라미터
- 요청 필드·범위 필드가 `RETURN`되지 않은 쿼리
- `LIMIT` 누락, 복수 `LIMIT`, 100을 초과한 제한
- 제한 없는 가변 길이 관계 탐색
- 라벨 없는 전체 노드 조회
- Evidence 필수 요청에서 `SUPPORTED_BY → Evidence` 경로 누락
- 사실·Evidence의 VERIFIED 필터 및 반환 상태 누락

`VERIFIED`는 데이터 값 하드코딩이 아니라 확정 답변 안전 정책 상수이므로 유일하게 허용되는 문자열 리터럴이다. 질문의 연도·학과·과목·학점 값은 반드시 파라미터로 전달한다.

### 검증 한계

이 검증기는 전체 Cypher 문법을 구현한 파서가 아니라 현재 허용할 읽기 패턴을 보수적으로 검사하는 정적 검증기다. 복잡한 서브쿼리, 동적 식별자, UNION, 프로시저와 가변 길이 탐색은 안전성을 증명하려 하지 않고 거부한다. 정적 검증 통과는 의미적 정답을 보장하지 않으므로 Neo4j EXPLAIN과 결과 검증을 추가로 수행한다.

## 6. Neo4j EXPLAIN

정적 검증 결과인 `ValidatedCypher`만 `QueryExplainer`에 전달할 수 있다. 실행 전 `EXPLAIN`으로 다음을 검사한다.

- Cypher 문법과 변수 선언
- 누락 파라미터
- Neo4j의 알 수 없는 라벨·관계·속성 알림
- Cartesian Product와 제한 없는 탐색 알림
- `AllNodesScan`, 전체 관계 scan 등 위험 계획 연산자

EXPLAIN 자체와 실제 조회에는 각각 5초의 managed transaction timeout을 적용한다. EXPLAIN 실패 시 실행 단계와 결과 검증 단계는 `SKIPPED`로 기록된다.

## 7. 읽기 전용 실행

- `ExplainedCypher` 객체만 실행기가 받는다.
- `session.execute_read` managed transaction만 사용한다.
- 최대 결과 행은 정적 검증된 `LIMIT`과 전역 100행 정책으로 이중 제한한다.
- 데이터 적재·삭제·DDL·프로시저 인터페이스는 없다.
- 현재 `.env`의 localhost 안전 제한과 비밀번호 비노출 정책을 재사용한다.

## 8. 결과·Evidence 검증

결과의 모든 행에 다음을 요구한다.

- QueryPlan의 요청 필드와 범위 필드
- 요청 범위와 실제 반환 범위의 일치
- 중복되지 않은 행
- `fact_status=VERIFIED`
- `evidence_verification_status=VERIFIED`
- Evidence ID, 발췌·원본·인쇄 페이지와 비어 있지 않은 원문

결과가 0행이면 안전한 미발견 결과로 유지하며, 다음 답변 계층이 `NOT_FOUND`로 해석할 수 있다. 결과에 REVIEW_REQUIRED가 포함되거나 Evidence가 불완전하면 확정 답변용 결과로 승격하지 않는다.

## 9. 단계별 추적과 오류 코드

각 내부 실행에는 UUID `request_id`가 부여된다.

| 단계 | 역할 |
|---|---|
| `PLAN_VALIDATION` | QueryPlan 구조·범위·필드 검증 |
| `SCHEMA_SELECTION` | 생성 스키마 SHA 검증과 관련 라벨 선택 |
| `CYPHER_VALIDATION` | 정적 읽기 안전성·명세 적합성 검증 |
| `NEO4J_EXPLAIN` | DB 문법·알림·계획 검사 |
| `EXECUTION` | 읽기 트랜잭션 실행 |
| `RESULT_VALIDATION` | 범위·필드·Evidence 검증 |

각 단계는 `PASS`, `FAIL`, `SKIPPED`, 오류 코드, 실행 시간과 결과 행 수를 기록한다. 주요 오류 코드 예시는 다음과 같다.

| 분류 | 예시 오류 코드 |
|---|---|
| 계획 | `QueryPlanError` |
| 스키마 | `SchemaCatalogError` |
| Cypher | `CYPHER_FORBIDDEN_KEYWORD`, `CYPHER_UNKNOWN_LABEL`, `CYPHER_RELATIONSHIP_ENDPOINT`, `CYPHER_LIMIT_EXCEEDED` |
| EXPLAIN | `NEO4J_EXPLAIN_FAILED`, `NEO4J_EXPLAIN_DANGEROUS_PLAN` |
| 실행 | `NEO4J_READ_FAILED`, `RESULT_LIMIT_EXCEEDED` |
| 결과 | `RESULT_FIELD_MISSING`, `RESULT_SCOPE_MISMATCH`, `RESULT_EVIDENCE_NOT_VERIFIED` |

런타임 파일은 `logs/query-runs/`에 기록되며 Git에서 제외한다. 이 로그는 실행 관찰 기록이고 `docs/ai-simulation-logs/`의 팀 작업 인수인계 로그와 별개다. 비밀번호·토큰·secret 계열 파라미터는 추적 전에 마스킹한다.

## 10. 검증 명령

```bash
uv run python -m kg_builder.query.schema_exporter check
```

```bash
uv run pytest
```

```bash
KG_NEO4J_INTEGRATION=1 uv run pytest tests/test_dynamic_query_integration.py
```

통합 테스트는 안전 쿼리의 EXPLAIN·실행·Evidence 검증, 잘못된 Cypher의 EXPLAIN 실패를 검사한다. 실행 전후 노드 1,518개, 관계 3,260개, Evidence 511개가 동일해야 한다.

## 11. 다음 로컬 LLM 연결 계약

다음 작업에서는 모델과 실행 방식을 먼저 결정한 뒤 다음 두 인터페이스를 구현한다.

1. 자연어 질문과 `llm_query_schema.json`을 받아 `QueryPlan`을 반환하는 planner
2. 검증된 QueryPlan과 관련 스키마만 받아 후보 Cypher를 반환하는 generator

두 출력은 현재 계약과 검증기를 반드시 통과해야 한다. 검증 실패 시 모델에 오류 코드와 제한된 스키마를 제공해 재시도할 수 있지만, 미검증 Cypher를 실행하는 fallback은 만들지 않는다.

아직 결정하지 않은 항목:

- 로컬 LLM 런타임과 모델
- planner와 generator를 한 모델로 구성할지 여부
- 재시도 횟수와 시간 제한
- 검증된 행을 한국어 근거 답변으로 변환하는 renderer
- 질문 평가셋과 정확도 기준
