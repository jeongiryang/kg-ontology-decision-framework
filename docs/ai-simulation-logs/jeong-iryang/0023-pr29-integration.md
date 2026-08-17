# 0023. PR #29 clarification·progress 통합

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-18 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/hwang-daegyeom/answer-coverage` |
| 관련 커밋 | 본 작업 merge commit |
| 관련 Issue/PR | PR #29 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #28의 canonical Cypher·실시간 progress 기반을 PR #29의 확장 fact family와
데이터 기반 clarification에 통합한다. 기존 sealed `ChatResponse` 8필드를 유지하면서
선택지는 별도 presentation SSE 계약으로 전달한다.

## 2. 작업 전 상태

- PR #29는 PR #28 병합 전 `main`에서 갈라져 핵심 런타임과 테스트 10개 파일에
  content conflict가 있었다.
- PR #29는 `missing`, `options`를 `ChatResponse` wire에 직접 추가해 기존 8필드
  소비자와 비호환이었다.
- `ask(question, resolved=None)`와 `ask(question, progress_callback=None)`가 서로
  다른 두 번째 위치 인자를 사용해 병합 시 오바인딩될 수 있었다.

## 3. 핵심 설계 결정

1. `ChatResponse` wire는 `request_id`, `status`, `answer_text`, `citations`,
   `used_fact_ids`, `used_evidence_ids`, `clarification`, `error_code`의 8필드를 유지한다.
2. clarification 선택지는 `type=clarification_options`, `version=1`의 별도 SSE
   envelope로 보낸다. stable choice ID와 한국어 label을 분리하고 선택값은 planner가
   같은 적재 데이터에서 다시 생성해 검증한다.
3. 공식 API는 `ask(question, *, resolved=None, progress_callback=None)`로 합쳐
   선택 후 재질의에서도 progress를 유지하고 위치 인자 오바인딩을 막는다.
4. PR #28의 comment-free canonical Cypher, 동일 candidate의 정적 검증+EXPLAIN
   승인, 실패 후보 비노출과 단일 SafetyPipeline 경로를 그대로 보존한다.
5. `filters`는 사용자가 이미 준 검색 조건, `requested_fields`는 답으로 원하는 값으로
   분리한다. 질문이 출력 필드를 명시하면 묻지 않은 과목 속성을 추가하지 않는다.

## 4. 충돌 해결

- Starlette `/api/ask`는 `resolved`와 progress callback을 함께 전달하고, 선택지는
  QUESTION_ANALYSIS 완료 event에서만 별도 envelope로 변환한다.
- 프론트는 `result.response`를 재생성하지 않고 별도 선택지 envelope만 UI state에
  보관한다.
- planner는 확장 fact family, 데이터 기반 선택지, 졸업질문 분류, PoC 기본 범위와
  requested-field 보정을 함께 유지한다.
- 자연어 서비스와 답변 서비스는 keyword-only 계약을 공유하며 선택지와 progress를
  같은 요청에서 전달한다.

## 5. 보안·Grounding

- 임의 `resolved` 값은 적재 데이터에서 다시 만든 선택지 allowlist와 일치할 때만
  QueryPlan에 반영된다.
- 선택값은 Cypher 문자열에 삽입하지 않고 검증된 parameter 경로로 전달한다.
- 검증 전·EXPLAIN 실패 Cypher는 progress·inspection·DOM에 공개하지 않는다.
- 일반 UI에는 내부 Fact/Evidence ID, 자격증명, URI, prompt, 모델 원문과 traceback을
  노출하지 않는다.
- 확정 답변은 계속 VERIFIED Fact와 직접 연결된 VERIFIED Evidence를 요구하며 최종
  한국어 문장은 결정론적 Claim renderer가 만든다.

## 6. 주요 변경 파일

| 경로 | 내용 |
|---|---|
| `src/kg_builder/llm/planner.py` | 확장 coverage·선택지·기본 범위·요청 필드 통합 |
| `src/kg_builder/query/natural_language_service.py` | resolved와 progress의 keyword-only 결합 |
| `src/kg_builder/answer/contracts.py` | sealed 8필드 wire 계약 복구 |
| `src/kg_builder/answer/service.py` | clarification·progress 결합 composition root |
| `src/evidence_chat/server.py` | 별도 versioned clarification SSE envelope |
| `src/evidence_chat/static/app.js` | 별도 선택지 event 소비와 기존 timeline 유지 |
| `tests/test_clarification_flow.py` | 데이터 기반 선택값 검증과 8필드 계약 |
| `tests/test_evidence_chat.py` | 별도 envelope·동시 resolved/progress 계약 |
| `tests/test_local_llm_pipeline.py` | keyword-only API와 planner 회귀 |

## 7. 검증

사용자가 지정한 관련 테스트 파일만 실행했다.

- `tests/test_clarification_flow.py`
- `tests/test_extended_fact_families.py`
- `tests/test_failure_reporting.py`
- `tests/test_grounded_coverage.py`
- `tests/test_planner_coverage.py`
- `tests/test_dynamic_query_safety.py`
- `tests/test_evidence_chat.py`
- `tests/test_local_llm_pipeline.py`
- `tests/test_answer_renderer.py`
- schema exporter stale check
- `git diff --check`

최종 결과:

- 관련 pytest: **218 passed, 314 subtests passed**
- Python compile: 통과
- schema exporter stale check: `generated query schema matches ontology_spec.json`
- staged diff whitespace: 통과

통합 도중에는 다음 회귀를 먼저 발견해 수정했다.

- CurriculumChatService test double이 새 keyword-only 계약을 받지 못함
- 과목코드·학년·학기를 requested field가 아닌 filter로 남기거나 불필요한 다른 과목
  속성을 덧붙임
- 완전한 계획을 반환한 모델의 영문 clarification을 그대로 보존함
- 실제로 `QUESTION_INTENT`가 비어 있는 질문까지 READY로 올림

각 실패 원인을 일반 계약으로 수정한 뒤 지정 묶음을 처음부터 다시 실행했다.

## 8. 실행하지 않은 검증

- 전체 unittest와 전체 pytest
- 전체 Neo4j 통합 테스트
- 전체 Ollama 질문 세트
- 전체 PDF 검사와 clean reinstall

## 9. 남은 제한사항

- browser가 연결을 취소해도 이미 시작된 로컬 모델 호출은 즉시 중단되지 않을 수 있다.
- 선택지는 현재 적재된 2026/CSE 범위 안에서 생성된다. 데이터 범위 확장 시 같은
  선택지·planner 회귀를 다시 검증해야 한다.
