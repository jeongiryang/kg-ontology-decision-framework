# 0007. Verified KG 읽기 전용 질의·Evidence 응답 계층

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/query-evidence-api` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | 이번 작업 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

온톨로지 V0.2와 Verified KG를 재설계하지 않고, 이미 적재된 로컬 Neo4j에서 확정 사실과 VERIFIED Evidence를 함께 반환하는 결정론적 읽기 전용 질의 계층을 구현한다.

## 2. 요청 내용 요약

- 구조화된 Intent와 파라미터를 검증한다.
- 사전 정의되고 파라미터화된 읽기 전용 Cypher만 실행한다.
- 정상 답변에는 하나 이상의 VERIFIED Evidence를 강제한다.
- 모호성, unresolved, 범위 밖, 조회 실패를 구분한다.
- 자유 형식 자연어 분석, LLM, Text-to-Cypher와 UI는 구현하지 않는다.

## 3. 작업 전 상태

- PR #11 병합 커밋 `5cbde0af5443d6404f3985e04acc1d2664b9fc10` 기준 `main`이 원격과 동기화되어 있었다.
- 로컬 Neo4j에는 노드 1,518개, 관계 3,260개, Evidence 511개가 적재되어 있었다.
- 온톨로지 V0.2와 Verified 데이터는 조회 기준으로 사용하고 수정하지 않았다.
- 황대겸 질문 문서는 QA 후보로만 읽었으며 질문 문장에 맞춘 하드코딩 기준으로 사용하지 않았다.

## 4. 수행한 작업

- 다음 6개 Intent의 요청 검증, Cypher, 결과 정규화를 구현했다.
  - `GET_GENERAL_EDUCATION_MIN_CREDITS`
  - `GET_BALANCED_GENERAL_REQUIREMENT`
  - `GET_TRANSFER_GENERAL_EXEMPTION`
  - `GET_COURSE_OFFERING`
  - `GET_MAJOR_REQUIRED_COURSES`
  - `GET_COURSE_COMPLETION_TYPE`
- 응답 상태를 `ANSWERABLE`, `CLARIFICATION_REQUIRED`, `UNRESOLVED`, `OUT_OF_SCOPE`, `NOT_FOUND`로 구분했다.
- 보류 과목은 Verified unresolved 목록과 대조하여 단순 미발견과 구분했다.
- 단위 테스트와 로컬 Neo4j 읽기 전용 통합 테스트를 추가했다.
- 팀원용 실행·계약 문서와 README 진입점을 추가했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/query_contracts.py` | 생성 | Intent별 요청 검증과 응답·Evidence 타입 |
| `src/kg_builder/cypher_queries.py` | 생성 | allowlist 기반 파라미터화 Cypher와 쓰기 키워드 차단 |
| `src/kg_builder/query_service.py` | 생성 | 읽기 트랜잭션, 결과 정규화, Evidence·모호성·unresolved 정책 |
| `src/kg_builder/query_cli.py` | 생성 | 구조화 JSON 요청 CLI |
| `tests/test_query_service.py` | 생성 | 계약·보안·응답 정책 단위 테스트 |
| `tests/test_query_integration.py` | 생성 | 로컬 Neo4j 6개 Intent와 DB 불변성 통합 테스트 |
| `docs/query-evidence-api.md` | 생성 | 지원 범위, 계약, 보안, 실행법 |
| `README.md` | 수정 | 상세 문서 링크와 최소 실행 예시 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 목록과 다음 번호 갱신 |

## 6. 주요 결정과 이유

- 사용자 입력을 Cypher 식별자로 사용하지 않고 값 파라미터로만 전달한다.
- 공통 교양 규칙은 `COMMON` 교육과정의 VERIFIED 규칙만 사용하여 CSE의 REVIEW_REQUIRED 중복 규칙을 확정 답변에서 제외한다.
- 학과가 컴퓨터공학과일 때 과목 조회는 CSE 전공 편성과 공통 교양 편성을 함께 탐색한다.
- 동명 과목이 복수 학수번호로 대응하면 하나를 고르지 않고 clarification 후보를 반환한다.
- `ANSWERABLE` 응답 생성 시 Evidence가 비어 있으면 예외가 발생하도록 계약 수준에서 강제한다.
- 임의 Cypher 입력 인터페이스를 만들지 않았다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 전체 단위 테스트 | `uv run pytest -q` | 29개 통과, 통합 테스트 1개 기본 skip, 하위 검증 23개 통과 |
| Neo4j 통합 테스트 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q tests/test_query_integration.py` | 1개 통과, 6개 Intent 하위 검증 통과 |
| Python 컴파일 | `python3 -m compileall -q src/kg_builder` | 통과 |
| 실제 DB 사전 개수 | 읽기 전용 조회 | 노드 1,518 / 관계 3,260 / Evidence 511 |
| 6개 Intent | 로컬 Neo4j 읽기 트랜잭션 | 모두 `ANSWERABLE`, Evidence 포함 |
| 교양 최소 학점 | `GET_GENERAL_EDUCATION_MIN_CREDITS` | 34학점, Evidence 1개 |
| 균형교양 요건 | `GET_BALANCED_GENERAL_REQUIREMENT` | 12학점 및 4개 영역별 1과목, Evidence 2개 |
| 편입생 면제 | `GET_TRANSFER_GENERAL_EXEMPTION` | 면제 `true`, Evidence 1개 |
| 자료구조 편성 | `GET_COURSE_OFFERING` | 2학년 1학기, 3학점, Evidence 1개 |
| 전공필수 | `GET_MAJOR_REQUIRED_COURSES` | 9과목·21학점, Evidence 9개 |
| 자료구조 이수구분 | `GET_COURSE_COMPLETION_TYPE` | `MAJOR_ELECTIVE`, Evidence 1개 |
| 실제 DB 사후 개수 | 읽기 전용 조회 | 노드 1,518 / 관계 3,260 / Evidence 511, 변화 없음 |

## 8. 발견된 문제와 위험

- 현재 지원 학년도는 2026년, 학과별 범위는 컴퓨터공학과로 제한된다.
- `major_type`별 전공필수 과목 편성 차이는 현재 데이터에 별도 편성으로 모델링되지 않아 목록 필터로 사용하지 않는다.
- 공통 기본 규칙과 편입생 면제 같은 예외를 하나의 Intent에서 자동 합성하지 않는다.
- unresolved 항목은 확정 답변에 사용하지 않으며 별도 결정이 필요하다.

## 9. 남은 작업

- 황대겸 학생 예상 질문을 Intent·파라미터·비교 방식으로 정리한다.
- 자연어 질문을 지원 Intent로 안전하게 라우팅하는 계층을 설계한다.
- 답안지와 읽기 전용 질의 회귀 평가를 함께 만든다.

## 10. 다음 작업 제안

질문-only 자료를 먼저 품질 검토한 뒤, 지원 가능한 질문을 구조화 평가 계약으로 승격하고 현재 질의 서비스의 실제 결과와 비교한다. 자유 Text-to-Cypher는 도입하지 않는다.
