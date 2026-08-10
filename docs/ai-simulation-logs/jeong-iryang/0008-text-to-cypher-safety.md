# 0008. Text-to-Cypher 스키마·검증·실행 안전 기반

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/text-to-cypher-safety` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | PR #12 병합, 이번 작업 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #12의 결정론적 6개 Intent 계층을 기준선으로 보존하면서, 향후 로컬 LLM이 생성할 QueryPlan과 Cypher를 검증 없이 실행하지 않도록 스키마 생성·정적 검증·EXPLAIN·읽기 실행·결과 검증·추적 파이프라인을 구현한다.

## 2. 요청 내용 요약

- `ontology_spec.json`을 유일한 원본으로 LLM용 스키마를 자동 생성한다.
- 외부 후보 Cypher의 명세 적합성과 읽기 전용 정책을 정적으로 검사한다.
- 정적 검증을 통과한 쿼리만 Neo4j `EXPLAIN`과 `execute_read`로 전달한다.
- 결과의 요청 범위와 VERIFIED Evidence를 검증한다.
- 단계별 PASS·FAIL·SKIPPED를 런타임 로그로 분리한다.
- 실제 LLM, Text-to-Cypher 생성과 자연어 답변은 구현하지 않는다.

## 3. 작업 전 상태

- PR #12는 이미 Ready 상태로 병합되어 있었고 merge commit은 `eb0409a309226842ee1d7e135df87483e4677a93`이었다.
- 최신 `main`을 fast-forward 동기화한 뒤 작업 브랜치를 생성했다.
- 로컬 Neo4지는 노드 1,518개, 관계 3,260개, Evidence 511개 상태였다.

## 4. 수행한 작업

- 26개 노드, 31개 관계, 노드 속성 선언 147개와 관계 속성 선언 3개를 포함한 LLM 질의 스키마를 자동 생성했다.
- 생성 파일에 원본 온톨로지 SHA-256과 버전, controlled vocabulary 및 안전 정책을 기록했다.
- QueryPlan의 질문·범위·필터·요청 필드·Evidence 요구 계약을 구현했다.
- 주석·문자열을 구분하는 보수적 Cypher 정적 검증기를 구현했다.
- 관계 방향과 endpoint, 라벨별 속성과 파라미터·LIMIT·Evidence 경로를 검사한다.
- Neo4j 6.x managed read transaction timeout을 적용한 EXPLAIN과 실행기를 구현했다.
- 결과 범위, 필수 필드, 중복 행과 VERIFIED Evidence를 검증한다.
- 단계별 런타임 trace를 작성하고 `logs/query-runs/`를 Git에서 제외했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `ontology/llm_query_schema.json` | 생성 | 온톨로지에서 자동 생성한 LLM 질의 스키마 |
| `src/kg_builder/query/` | 생성 | 스키마·계획·검증·EXPLAIN·실행·결과·추적 파이프라인 |
| `tests/test_dynamic_query_safety.py` | 생성 | 생성 재현성, 명세 SHA, 계획·Cypher·결과·추적 단위 테스트 |
| `tests/test_dynamic_query_integration.py` | 생성 | 실제 Neo4j EXPLAIN·읽기 실행·실패 차단·DB 불변 테스트 |
| `docs/text-to-cypher-safety.md` | 생성 | 설계, 정책, 오류 코드와 다음 LLM 계약 |
| `README.md` | 수정 | 문서 링크와 현재 구현 범위 |
| `.gitignore` | 수정 | 런타임 질문 로그 제외 |
| 정이량 로그 README | 수정 | 다음 로그 번호와 목록 갱신 |

## 6. 주요 결정과 이유

- LLM용 스키마 allowlist를 코드에 복사하지 않고 원본 명세에서 생성한다.
- 검증 결과를 `ValidatedCypher`, EXPLAIN 결과를 `ExplainedCypher`로 분리해 미검증 문자열이 실행기에 직접 전달되지 않게 한다.
- 정적 검증기는 전체 Cypher 문법을 부분 허용하지 않고 검증 가능한 패턴만 보수적으로 허용한다.
- `VERIFIED`만 정책 상수 리터럴로 허용하고 질문 필터 값은 모두 파라미터화한다.
- 공개 임의 Cypher CLI를 만들지 않았다.
- 런타임 질문 trace와 AI 작업 인수인계 로그를 분리했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 스키마 생성·동일성 | `python -m kg_builder.query.schema_exporter generate/check` | 통과 |
| JSON 파싱 | `python -m json.tool ontology/llm_query_schema.json` | 통과 |
| 단위·회귀 테스트 | `uv run pytest -q` | 43개 통과, 통합 테스트 3개 기본 skip, 하위 검증 31개 통과 |
| 동적 Neo4j 통합 테스트 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q tests/test_dynamic_query_integration.py` | 2개 통과 |
| 안전 쿼리 EXPLAIN | 실제 Neo4j | `NodeIndexSeek` 포함, 위험 연산자 없음 |
| 안전 쿼리 결과 | 실제 Neo4j | 3행, Evidence 3개 |
| EXPLAIN 문법 실패 | 잘못된 Cypher | 실행 전 `NEO4J_EXPLAIN_FAILED`로 차단 |
| DB 사후 개수 | 읽기 전용 조회 | 1,518 / 3,260 / 511, 변화 없음 |

## 8. 발견된 문제와 위험

- 최초 통합 시도에서 Neo4j `ManagedTransaction.run()`이 timeout `Query` 객체를 지원하지 않아 실패했다. `unit_of_work(timeout=5)` managed transaction 설정으로 교정했다.
- Neo4j 2026.06의 EXPLAIN plan은 객체가 아닌 dict를 반환하므로 두 표현을 모두 처리하도록 보완했다.
- 정적 검증기는 전체 Cypher parser가 아니므로 복잡한 서브쿼리와 UNION 등은 지원하지 않고 거부한다.
- 정적·EXPLAIN 검증은 질문 의미의 정답성을 보장하지 않으며 결과 범위와 Evidence 검증을 반드시 거쳐야 한다.

## 9. 남은 작업

- 로컬 LLM 런타임과 모델 선택
- 자연어 질문에서 QueryPlan을 생성하는 planner
- QueryPlan과 관련 스키마에서 후보 Cypher를 생성하는 generator
- 검증 실패 재시도 계약
- 검증 결과를 근거 포함 한국어 답변으로 변환하는 renderer
- 학생 질문 평가셋과 정확도 측정

## 10. 다음 작업 제안

로컬 LLM 후보의 구조화 출력·컨텍스트 길이·한국어 이해도·하드웨어 요구사항을 비교한 뒤, 모델을 현재 QueryPlan과 CypherValidator 사이에만 연결한다. 검증 실패 시 미검증 쿼리를 실행하는 fallback은 두지 않는다.
