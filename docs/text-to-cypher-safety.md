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
    "department_id": "department:cwnu:cse",
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

- `academic_year`와 `department_id`는 현재 필수 범위다.
- 동적 필터 이름은 원본 온톨로지 속성과 일치한다. 현재 명세에는 `department_code`나 단수형 `credit` 속성이 없으므로 각각 `department_id`, `credits`를 사용한다.
- 지원 필터만 허용하고 타입과 통제어휘를 검증한다.
- `requested_fields`는 온톨로지에 실제 선언된 속성명만 허용한다.
- `intent`는 Cypher 선택 키가 아니라 선택적 설명·추적 메타데이터다.
- 필터 값은 후보 Cypher에서 동일한 이름의 파라미터로 사용해야 한다.
- 현재 동적 V1 계획은 `evidence_required=true`만 허용한다. Evidence 없는 결과를 확정 답변으로 승격하는 모드는 제공하지 않는다.

## 5. 정적 Cypher 검증

검증기는 주석과 문자열 리터럴을 구분해 토큰을 검사하고, 다음 최소 문법만 허용한다.

```text
MATCH | OPTIONAL MATCH
WHERE (AND로 연결된 제한된 속성-파라미터 조건, 각 조건 자체를 괄호로 감싸지 않음)
WITH (기존 그래프 변수의 단순 전달만 허용)
RETURN [DISTINCT] graphVariable.property AS alias
ORDER BY returnedAlias [ASC|DESC]
SKIP nonNegativeInteger
LIMIT 1..100
```

허용 목록 밖의 절과 표현은 기본적으로 거부한다. 특히 다음을 거부한다.

- `CREATE`, `INSERT`, `MERGE`, `DELETE`, `SET`, `REMOVE` 등 쓰기 절
- DDL·관리 명령, `CALL`·APOC·프로시저, `LOAD CSV`, `FOREACH`, `UNION`, `UNWIND`
- 모든 서브쿼리와 함수 호출·집계(`collect()` 포함), map 표현식
- 다중 statement와 세미콜론
- backtick 동적 식별자
- 원본 명세에 없는 라벨·관계·속성
- 관계 방향 또는 endpoint 불일치
- QueryPlan 필터의 문자열 직접 삽입, 파라미터 누락 또는 항등식
- 필터가 허용된 라벨·속성에 직접 결합되지 않은 쿼리
- scope를 그래프 속성이 아닌 파라미터에서 직접 반환하는 쿼리
- QueryPlan에 없는 추가 파라미터
- 요청 필드·범위 필드가 `RETURN`되지 않은 쿼리
- `LIMIT` 누락, 복수 `LIMIT`, 100을 초과한 제한
- 제한 없는 가변 길이 관계 탐색
- 라벨 없는 전체 노드 조회
- 직접 `fact-[:SUPPORTED_BY]->Evidence` 경로가 없거나 무관한 사실의 Evidence를 붙인 쿼리
- 안정적인 `fact_id`와 직접 연결된 Evidence 필드가 반환되지 않은 쿼리
- 사실·Evidence의 VERIFIED 필터 및 반환 상태 누락

`VERIFIED`는 데이터 값 하드코딩이 아니라 확정 답변 안전 정책 상수이므로 유일하게 허용되는 문자열 리터럴이다. 질문의 연도·학과·과목·학점 값은 반드시 파라미터로 전달한다.

### 검증 한계

이 검증기는 전체 Cypher parser가 아니라 제한된 문법을 lexer와 구조 검사로 거부 우선 검증한다. 지원하지 않는 서브쿼리·함수·표현식은 안전성을 추정하지 않고 거부한다. Python 객체나 정규식 검사는 독립적인 보안 경계가 아니므로 Neo4j EXPLAIN, 결과 검증과 읽기 전용 계정이 모두 필요하다.

### 필터 결합 정책

각 필터는 생성 스키마의 `query_policy.filter_bindings`에 기록된 라벨·속성과 직접 비교돼야 한다. 파라미터가 단순히 쿼리에 등장하는 것만으로는 통과하지 않는다. 같은 그래프 속성을 scope alias로 반환해야 하며 `$academic_year = $academic_year` 같은 항등식과 `$academic_year AS academic_year` 같은 위조 scope는 거부한다.

### fact–Evidence provenance 정책

Evidence 필수 조회는 원본 명세의 `SUPPORTED_BY` endpoint에서 파생한 직접 경로 하나를 사용한다. 검증기는 Evidence를 가진 시작 노드를 fact로 정하고, 해당 fact의 ID·상태와 직접 연결된 Evidence ID·페이지·원문만 반환하도록 검사한다. 요청 필드는 fact 또는 그 직접 이웃에서만 가져올 수 있다. `DISTINCT`는 잘못된 provenance를 정당화하지 않는다.

## 6. Neo4j EXPLAIN

정적 검증 결과인 `ValidatedCypher`만 `QueryExplainer`에 전달할 수 있다. 실행 전 `EXPLAIN`으로 다음을 검사한다.

- Cypher 문법과 변수 선언
- 누락 파라미터
- Neo4j의 알 수 없는 라벨·관계·속성 알림
- Cartesian Product와 제한 없는 탐색 알림
- `AllNodesScan`, 전체 관계 scan 등 위험 계획 연산자
- `Create`, `Delete`, `SetProperty`, `Merge`, `ProcedureCall`, schema·administration 등 모든 쓰기 계획 연산자

EXPLAIN 자체와 실제 조회에는 각각 5초의 managed transaction timeout을 적용한다. EXPLAIN 실패 시 실행 단계와 결과 검증 단계는 `SKIPPED`로 기록된다.

## 7. 읽기 전용 계정과 실행

- 동적 질의는 ingestion 계정과 분리된 `NEO4J_QUERY_*` 자격증명만 사용한다.
- query 계정은 Neo4j에서 대상 graph/database에 대한 읽기 권한만 가져야 한다.
- 현재 서버 edition 또는 권한 때문에 애플리케이션이 역할을 자동 확인하지 못하면 읽기 전용 상태를 통과로 간주하지 않는다.
- 실제 권한 검증용 쓰기 공격은 운영 DB가 아니라 별도 안전 DB 또는 격리 환경에서만 수행한다.
- 패키지의 공식 실행 진입점은 `SafetyPipeline`이며 executor는 내부 구현이다.
- validator·EXPLAIN 승인 객체는 직접 생성할 수 없고 executor도 실행 직전에 쓰기 절을 재검사한다.
- `session.execute_read` managed transaction만 사용한다.
- 최대 100행, 행당 64 KiB, 전체 직렬화 결과 1 MiB를 제한한다.
- 함수·집계를 금지해 무제한 `collect()`나 대형 map/list 결과를 차단한다.
- 데이터 적재·삭제·DDL·프로시저 인터페이스는 없다.
- query URI도 `localhost:7687`만 허용하며 비밀번호를 출력하거나 trace에 기록하지 않는다.

환경변수:

```dotenv
NEO4J_QUERY_URI=neo4j://localhost:7687
NEO4J_QUERY_USER=your-read-only-neo4j-user
NEO4J_QUERY_PASSWORD=
NEO4J_QUERY_DATABASE=neo4j
```

Python의 private 명명과 승인 객체는 실수 방지 장치일 뿐 같은 프로세스의 악성 코드에 대한 완전한 보안 경계가 아니다. 읽기 전용 Neo4j 권한이 최종 방어선이다.

## 8. 결과·Evidence 검증

결과의 모든 행에 다음을 요구한다.

- QueryPlan의 요청 필드와 범위 필드
- 안정적인 `fact_id`와 검증된 fact label
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

런타임 파일은 `logs/query-runs/`에 기록되며 Git에서 제외한다. 이 로그는 실행 관찰 기록이고 `docs/ai-simulation-logs/`의 팀 작업 인수인계 로그와 별개다.

기본 trace는 질문 원문과 fingerprint를 모두 저장하지 않고 질문 길이만 기록한다. 중복 질문 식별이 필요할 때만 `KG_QUERY_TRACE_FINGERPRINT=true`와 `KG_QUERY_TRACE_HMAC_KEY`를 함께 설정해 HMAC-SHA256 fingerprint를 opt-in할 수 있다. HMAC 키가 없으면 설정 오류로 중단하며 키와 질문은 trace에 기록하지 않는다. 원문 저장도 애플리케이션이 별도로 opt-in한 경우에만 허용하며 이메일·학번·전화번호의 단순 패턴을 마스킹한다. 패턴 탐지는 완전한 개인정보 탐지 수단이 아니므로 원문 저장은 운영상 최소화해야 한다. 기본 보존 정책은 30일이며 trace 접근은 애플리케이션 운영자로 제한한다. 실제 삭제 자동화는 아직 구현하지 않았으므로 배포 환경의 로그 수명주기 정책으로 강제해야 한다.

`llm_query_schema.json`의 `query_policy.provenance.fact_labels`는 `SUPPORTED_BY`의 출발 라벨 중 상속 속성을 포함해 `status`가 선언된 라벨만 원본 명세에서 결정론적으로 파생한다. 따라서 과목 정체성인 `Course` 자체는 확정 편성 fact가 아니며, 학년·학기·학점·이수구분 질문에는 Evidence가 직접 연결된 `CourseOffering`을 fact로 사용해야 한다.

현재 허용 WHERE 문법은 `alias.property = $parameter`, `$parameter IN alias.property`, 그리고 fact/Evidence의 VERIFIED 검사뿐이다. LLM 생성기는 `WHERE (cv.academic_year = $academic_year)`처럼 개별 조건을 불필요한 괄호로 감싸지 않아야 한다. 이 제약은 생성 프롬프트와 자동 생성 스키마에도 함께 제공하며, 이를 이유로 검증기의 허용 범위를 넓히지 않는다.

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

통합 테스트는 별도의 `NEO4J_QUERY_*` 읽기 전용 자격증명이 있을 때만 안전 쿼리의 EXPLAIN·실행·Evidence 검증을 수행한다. 실행 전후 노드 1,518개, 관계 3,260개, Evidence 511개가 동일해야 한다. GitHub Actions는 비밀값 없는 단위 테스트와 스키마 검사를 수행하며 로컬 Neo4j 통합 테스트를 통과한 것으로 가장하지 않는다.

## 11. 로컬 LLM 연결 상태

RTX 4070 Ti 로컬 PoC에서 다음 두 인터페이스를 구현했다.

1. 자연어 질문과 온톨로지·Verified KG에서 파생한 범위 컨텍스트를 받아 `QueryPlan`을 반환하는 planner
2. 검증된 QueryPlan과 관련 스키마 부분집합만 받아 후보 Cypher를 반환하는 generator

선정 모델은 Ollama `qwen2.5-coder:14b` Q4_K_M이며 두 출력은 현재 계약과 검증기를 반드시 통과한다. 검증 실패 시 모델에 오류 코드만 제공해 한 번 재시도하고, 미검증 Cypher를 실행하는 fallback은 없다. 자세한 환경·benchmark·CLI는 [로컬 LLM PoC 문서](local-llm-query-pipeline.md)를 참고한다.

아직 구현하지 않은 항목:

- 검증된 행을 한국어 근거 답변으로 변환하는 renderer
- 질문 평가셋과 정확도 기준
- 프론트엔드 응답 계약 연결
