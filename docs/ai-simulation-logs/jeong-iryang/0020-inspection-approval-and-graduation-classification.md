# PR #28 inspection 승인 경계와 졸업질문 분류 보완

## 작업 목적

PR #28의 탐색 과정에서 EXPLAIN을 통과하지 못한 Cypher가 표시될 수 있는 문제와, 일반 TOEIC 규정 질문이 개인 이력 기반 졸업판정으로 분류되는 문제를 수정했다.

## 재현 결과

- 정적 검증 완료 후 EXPLAIN이 실패해도 inspection payload에 후보 Cypher와 파라미터가 남았다.
- `내가`, `학생`, `졸업`과 일반 기준 표현이 함께 있는 TOEIC 질문은 LLM을 호출하지 않고 `UNSUPPORTED`로 종료됐다.
- 첫 후보 실패 후 재생성 시 후보별 승인 identity가 없어 inspection 정보의 동일 attempt 보장이 명시되지 않았다.

## 구현 결정

- Cypher 생성 attempt를 내부 progress metadata로 전달한다.
- 정적 검증 결과는 후보별 임시 상태에만 보관하고, 동일 attempt의 Neo4j EXPLAIN 완료 후에만 inspection 승인 상태로 이동한다.
- 정적 검증 또는 EXPLAIN 실패와 새 후보 시작 시 이전 후보 상태를 폐기한다.
- 졸업 질문을 일반 규정, 단일 조건 비교, 전체 개인 이력 판정으로 분류한다.
- 전체 개인 이력과 졸업 가능 판정이 함께 요구될 때만 LLM 호출 없이 `UNSUPPORTED` 처리한다.
- Verified bundle에서 `REVIEW_REQUIRED` Rule의 값은 제외하고 의미 힌트와 Condition subject field만 파생한다. 일반 TOEIC 기준 질문은 현재 상위 Rule 상태에 따라 `UNRESOLVED`로 처리하며 점수를 반환하지 않는다.
- 단일 조건 비교 미지원 안내와 개인 이력 미지원 안내를 서로 다른 고정 한국어 문구로 관리한다.

## 변경 파일

- `src/evidence_chat/server.py`: candidate별 inspection 임시·승인 상태
- `src/kg_builder/query/natural_language_service.py`, `safety_pipeline.py`: candidate attempt 전달
- `src/kg_builder/llm/models.py`, `planner.py`, `prompts.py`: 3단계 졸업질문 분류와 미확정 Rule 상태
- `src/kg_builder/answer/contracts.py`, `service.py`: 미지원 사유별 고정 한국어 안내
- `tests/test_local_llm_pipeline.py`, `tests/test_evidence_chat.py`: 분류·inspection 회귀 테스트
- `docs/evidence-chat.md`, `docs/local-llm-query-pipeline.md`: 승인 경계와 분류 계약

## 검증

- `uv run --no-sync pytest -q tests/test_local_llm_pipeline.py tests/test_evidence_chat.py`
  - 45 passed, 6 subtests passed
- 실제 Starlette `/api/ask` 두 질문
  - 일반 TOEIC 기준 질문: `UNRESOLVED`, Citation 0, 임계값 추측 없음
  - 전체 수강내역 기반 졸업 질문: `UNSUPPORTED`, Citation 0, 고정 한국어 개인 이력 안내
- 수정 전 격리 재현
  - EXPLAIN 실패 후 Cypher inspection 노출 확인
  - 일반 TOEIC 규정 질문의 LLM 호출 전 `UNSUPPORTED` 확인

## 미실행 검증

- 전체 unittest
- 전체 pytest
- 전체 Neo4j 통합 테스트
- 기존 6문항 전체 회귀
- 전체 Evidence PDF 검사

## 남은 제한사항

- TOEIC 조건 값 노드는 존재하지만 상위 영어 면제 Rule이 `REVIEW_REQUIRED`이므로 현재 확정 답변에 사용할 수 없다.
- 일반 규정의 의미 분류는 정답값을 제공하지 않으며, 향후 Verified Rule이 추가되면 기존 안전 QueryPlan 경로로 다시 검증해야 한다.
- 같은 Python 프로세스에서 임의 코드 실행권을 가진 공격자는 애플리케이션 내부 신뢰 경계 밖이다.

## 다음 작업

- 최신 PR #28 Head에서 inspection 실패·재시도 경계와 졸업질문 상태를 독립 재검토한다.
