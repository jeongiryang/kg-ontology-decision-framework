# 0009. PR #13 Text-to-Cypher 안전성 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/text-to-cypher-safety` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | Draft PR #13 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #13 독립 검토에서 확인된 쓰기 Cypher 우회, 필터 항등식, 무관한 Evidence 결합, executor 직접 호출, query 계정 분리, 질문 개인정보 기록과 결과 크기 제한 문제를 수정한다.

## 2. 요청 내용 요약

- 허용 절만 통과시키는 거부 우선 Cypher 검증으로 전환한다.
- QueryPlan 필터를 명세의 라벨·속성에 직접 결합한다.
- fact와 Evidence의 직접 provenance를 검증한다.
- 정적 검증·EXPLAIN 승인 객체와 executor 경계를 보완한다.
- ingestion과 분리된 읽기 전용 query 자격증명 계약을 추가한다.
- 질문 원문 기본 미저장과 결과 직렬화 크기 제한을 적용한다.
- 단위 회귀를 GitHub Actions에서 실행한다.

## 3. 작업 전 상태와 재현 결과

- PR #13 Head는 `38dbf0f549ab6387fe85327a6bf6de5011fe721d`였고 Draft 상태였다.
- GitHub의 review, inline comment, unresolved thread는 없었다.
- `INSERT`가 정적 검증과 Neo4j `EXPLAIN`을 통과했고 실행 계획에 `Create`가 나타났다.
- 필터 항등식과 파라미터 scope 반환이 정적 검증을 통과했다.
- CourseOffering 결과에 무관한 Rule Evidence를 결합한 쿼리가 전체 파이프라인을 통과했다.
- 승인 객체를 직접 생성하면 executor를 우회할 수 있었다.
- 런타임 trace가 질문 원문을 기본 저장했다.

## 4. 수행한 작업과 주요 결정

- `MATCH`, `OPTIONAL MATCH`, 제한된 `WHERE`·`WITH`, 단순 `RETURN`, `ORDER BY`, `SKIP`, `LIMIT`만 허용하고 서브쿼리·함수·집계·map·쓰기 절은 기본 거부한다.
- 동적 필터 이름을 실제 온톨로지 속성과 일치시켰다. 명세에 없는 `department_code`, `credit`을 만들지 않고 `department_id`, `credits`를 사용한다.
- 생성 LLM 스키마에 필터 binding과 `SUPPORTED_BY` provenance 정책을 기록한다.
- 직접 `fact-[:SUPPORTED_BY]->Evidence` 경로, 안정적인 fact ID, fact 상태와 Evidence 필드를 함께 검증한다.
- EXPLAIN 계획에서 Create/Delete/Set/Merge/Procedure/Schema/Administration 계열을 차단한다.
- 승인 객체의 일반 생성자를 막고 executor가 쓰기 절을 다시 검사한다. Python 객체 자체는 완전한 보안 경계가 아니므로 전용 읽기 전용 Neo4j 계정을 최종 방어선으로 문서화했다.
- 기본 trace에는 질문 길이와 SHA-256만 저장한다. 원문 opt-in 시 이메일·학번·전화번호 단순 패턴을 마스킹한다.
- 결과를 스트리밍하면서 최대 100행, 행 64 KiB, 전체 1 MiB를 검사한다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/query/` | 수정 | 제한 문법 검증, provenance, EXPLAIN, executor, trace와 결과 제한 |
| `src/kg_builder/config.py` | 수정 | 분리된 `NEO4J_QUERY_*` 설정 계약 |
| `ontology/llm_query_schema.json` | 갱신 | 필터 binding과 provenance 정책 자동 생성 결과 |
| `tests/test_dynamic_query_safety.py` | 수정 | 보안 우회·개인정보·크기 제한 회귀 테스트 |
| `tests/test_dynamic_query_integration.py` | 수정 | 읽기 전용 query 자격증명 사용 |
| `.github/workflows/query-safety.yml` | 생성 | 비밀값 없는 단위·스키마 CI |
| `.env.example` | 수정 | 읽기 전용 query 계정과 trace 예시 |
| `docs/text-to-cypher-safety.md` | 수정 | 허용 문법과 운영 안전 정책 |
| `README.md` | 수정 | 분리 계정과 안전 문서 안내 |

## 6. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| Python compile | `python -m compileall -q src tests` | 통과 |
| 단위·회귀 테스트 | `python -m unittest discover -s tests -v` | 53개 통과, 자격증명 기반 통합 3개 기본 skip |
| pytest | `uv run pytest -q` | 50개 통과, 3개 skip, 하위 검증 56개 통과 |
| 스키마 동기화 | `python -m kg_builder.query.schema_exporter check` | 통과 |
| EXPLAIN 쓰기 계획 | `INSERT` 후보를 실제 실행하지 않고 `EXPLAIN` | `Create` 계획을 `NEO4J_EXPLAIN_DANGEROUS_PLAN`으로 차단 |
| 새 동적 안전 조회 | 로컬 Neo4j, 읽기 쿼리만 실행 | 3행·Evidence 3개, DB 불변 |
| PR #12 6개 Intent | 로컬 Neo4j 통합 테스트 | 통과 |
| DB 전후 개수 | 읽기 전용 조회 | 1,518 / 3,260 / 511, 변화 없음 |
| diff 형식 | `git diff --check` | 통과 |

## 7. 읽기 전용 권한 확인 여부

로컬 `.env`에는 별도의 `NEO4J_QUERY_*` 자격증명이 없으며 현재 서버에서 실제 계정 권한을 자동 확인하지 못했다. 기존 로컬 연결로 통제된 읽기 쿼리만 실행해 기능과 데이터 불변성을 확인했지만, 이를 읽기 전용 계정 정책 통과로 기록하지 않는다. 계정 생성·권한 변경과 운영 DB 쓰기 공격 테스트는 수행하지 않았다.

## 8. 남은 제한사항

- 제한 문법 검증기는 전체 Cypher parser가 아니며 지원하지 않는 문법을 거부한다.
- Python private API는 같은 프로세스의 악성 코드에 대한 보안 경계가 아니다.
- 단순 개인정보 패턴 마스킹은 모든 개인정보를 탐지하지 못한다.
- trace 30일 보존은 문서 정책이며 자동 삭제 구현은 후속 운영 작업이다.
- 로컬 Neo4j 통합 테스트는 분리된 읽기 전용 query 계정이 설정된 환경에서 재검증해야 한다.

## 9. 다음 작업

PR #13 최신 Head에 대해 독립 읽기 전용 재검토를 수행한다. BLOCKER·MAJOR가 없고 읽기 전용 계정 운영 조건을 확인한 뒤에만 Draft 해제 여부를 판단한다.
