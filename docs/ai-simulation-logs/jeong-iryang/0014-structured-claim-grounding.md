# 0014. 구조화 Claim 기반 답변 Grounding 보안 수정

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/evidence-answer-renderer` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | Draft PR #27 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #27 독립 검토에서 재현된 의미 반전 Grounding 취약점을 수정한다. 자유 형식 LLM 답변을 단어·숫자 집합으로 사후 검사하던 경로를 제거하고, Verified 조회 행에서 Python이 구조화 Claim을 만들고 검증한 뒤 결정론적으로 한국어 답변을 조립한다.

## 2. 요청 내용 요약

- `MAJOR_ELECTIVE`를 전공필수로 바꾸는 의미 반전 차단
- 과목 수와 학점 합계의 숫자 역할 교환 차단
- 편입생 면제를 의무 적용으로 바꾸는 Boolean 극성 반전 차단
- 안정적인 Fact–Evidence provenance를 포함한 GroundedClaim 계약
- 결정론적 한국어 renderer와 Citation 고정 정렬
- ChatResponse 상태별 불변조건
- 최종 사실 답변 LLM 호출 제거
- 정상 6문항, 공격 회귀, Neo4j 불변성 검증

## 3. 작업 전 상태

- 브랜치와 원격 Draft PR #27 Head는 `98bab78af67a732148f9931f3788f1f02dd5d8dc`로 일치했고 작업 트리는 clean이었다.
- PR #27에는 리뷰·일반 댓글·unresolved thread가 없었고 단위 테스트 check는 성공 상태였다.
- 다음 세 초안이 기존 `AnswerValidator`를 통과하는 문제를 재현했다.
  - `MAJOR_ELECTIVE` 행에 “전공필수” 문장
  - `fact_count=2`, `credits_sum=6` 행에 “6개·2학점” 문장
  - 편입생 면제 Rule에 “교양 이수 의무가 있다” 문장
- `DELETE 자료구조`, `CREATE 자료구조`, `Cypher 자료구조`도 기존 자유 텍스트 경로에서 승인되는 것을 확인했다.
- 로컬 Neo4j 기준은 노드 1,518개, 관계 3,260개, Evidence 511개였다.

## 4. 수행한 작업

1. `GroundedClaim`, `FactEvidenceLink`, 과목 item, Claim 유형·극성 계약을 추가했다.
2. `ClaimBuilder`가 ResultValidator 승인 행과 QueryPlan에서만 필드 값, 단위, operator, polarity, 목록·집계를 생성하도록 했다.
3. `ClaimValidator`가 직접 provenance, VERIFIED 상태, enum, 수치 역할, 면제 극성, 목록 커버리지와 집계를 원본 행에서 다시 계산한다.
4. `KoreanAnswerRenderer`가 질문 원문이나 모델 응답 없이 Claim 유형과 필드에 따라 한국어 문장을 생성한다.
5. 자유 형식 `EvidenceAnswerGenerator`, `AnswerDraft`, `AnswerValidator`와 답변 재시도 경로를 제거했다.
6. 숫자 Rule에서 모델이 `value`만 요청하는 실제 사례를 확인하고, planner가 정답값 없이 `rule_type/operator/unit/description_ko`를 구조적으로 보강하도록 했다.
7. Citation을 페이지 세 종류, Evidence ID, Fact ID 순으로 고정 정렬하고 Evidence별 중복 제거·Fact ID 정렬을 적용했다.
8. `ChatResponse`가 상태와 Grounding 필드의 모순된 조합을 생성하지 못하게 불변조건을 강화했다.
9. 내부 구문·비밀 표현 검사는 Claim Grounding 이후 추가 방어선으로 유지했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/answer/contracts.py` | 수정 | GroundedClaim·provenance·응답 불변조건 |
| `src/kg_builder/answer/claim_builder.py` | 생성 | 검증 행 기반 Claim 생성 |
| `src/kg_builder/answer/claim_validator.py` | 생성 | Claim 값·역할·provenance 재검증 |
| `src/kg_builder/answer/korean_renderer.py` | 생성 | 결정론적 한국어 문장 조립 |
| `src/kg_builder/answer/renderer.py` | 수정 | Citation 고정 정렬·조립 |
| `src/kg_builder/answer/service.py` | 수정 | Claim 기반 공식 Chat 경로와 안전 실패 |
| `src/kg_builder/answer/cli.py` | 수정 | 최종 답변 모델 생성기 제거 |
| `src/kg_builder/answer/generator.py` | 삭제 | 자유 형식 최종 답변 LLM 제거 |
| `src/kg_builder/answer/validator.py` | 삭제 | 취약한 자유 문장 사후 검사 제거 |
| `src/kg_builder/llm/planner.py` | 수정 | 수치 Rule 의미 필드 결과 계약 보강 |
| `tests/test_answer_renderer.py` | 수정 | 의미 반전·역할 교환·provenance·정렬 회귀 |
| `tests/test_answer_integration.py` | 수정 | 결정론적 6문항·호출 수·성능·DB 불변 |
| `tests/test_local_llm_pipeline.py` | 수정 | 수치 Rule 계획 보강 회귀 |
| `docs/evidence-answer-renderer.md` | 수정 | 구조화 Claim 설계와 확장 정책 |
| `docs/local-llm-query-pipeline.md` | 수정 | 후속 결정론적 답변 계층 현황 |
| `README.md` | 수정 | Claim 기반 CLI 설명 |

## 6. 주요 결정과 이유

- **자유 형식 최종 답변 LLM 제거**: 어휘·숫자 포함 검사는 값의 의미 역할과 극성을 증명하지 못하므로 최종 사실 문장을 모델에 맡기지 않는다.
- **직접 provenance 쌍**: Fact ID와 Evidence ID의 독립 목록 대신 `(fact_id, evidence_id)` 쌍을 Claim에 저장해 무관한 Evidence 결합을 구조적으로 막는다.
- **역할별 Claim**: `fact_count/COURSE`와 `credits_sum/CREDIT`을 별도 Claim으로 만들고 원본 행에서 재계산한다.
- **검증 문구 보존**: Rule 설명은 Verified 속성을 그대로 렌더링해 면제·최소요건의 의미를 바꾸지 않는다.
- **내부 Claim 비노출**: UI용 `ChatResponse` JSON의 기존 필드를 유지하고 Claim은 서버 내부 감사·불변조건에 사용한다.
- **거부 우선 확장**: 새 조합은 질문 문장 분기가 아니라 ClaimBuilder/Validator/renderer 조합과 fixture를 추가하며, 미지원 조합은 안전 실패한다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 의존성 | `uv sync --locked` | PASS |
| lock | `uv lock --check` | PASS |
| unittest | `uv run python -m unittest discover -s tests -v` | 98개 중 93 PASS, 환경 통합 5 skip |
| pytest | `uv run pytest -q` | 93 PASS, 5 skip, 86 subtests PASS |
| Neo4j 통합 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q` | 96 PASS, 2 skip, 92 subtests PASS |
| 실제 답변 6문항 | `KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_answer_integration.py -s` | 1 test / 6 subtests PASS |
| 스키마 stale | `uv run python -m kg_builder.query.schema_exporter check` | PASS |
| 의미 공격 | 구조화 Claim 단위 회귀 | 의미·수치·극성 반전 모두 차단 |
| DB 불변 | 통합 테스트 전후 조회 | 1,518 / 3,260 / 511 유지 |

실제 최종 6문항 결과와 시간은 다음과 같다.

| 문항 | 결과 | Citation | 전체 시간 | 모델 호출 |
|---|---|---:|---:|---:|
| 교양 최소 | 최소 34학점 | 1 | 10.919초 | 2 |
| 균형교양 | 4개 영역·영역별 1과목·최소 12학점 | 2 | 12.229초 | 2 |
| 편입생 | 교양 이수 의무 없음 | 1 | 11.616초 | 2 |
| 자료구조 개설 | 2학년 1학기 | 1 | 18.045초 | 3(Planner 재확인 포함) |
| 전공필수 | 9과목·21학점 | 9 | 15.254초 | 2 |
| 자료구조 이수구분 | 전공선택 | 1 | 14.835초 | 2 |

평균 전체 시간은 약 13.82초이며 `query_seconds`와 최종 시간이 밀리초 수준에서 같았다. 이전 PR #27 기준 응답시간은 문서에 수치가 없어 직접 비교하지 않았다. 다만 런타임 구조상 각 질문의 최종 답변 모델 호출 1회가 제거되어, 같은 planner/Cypher 재시도 경로 대비 질문당 모델 호출이 정확히 1회 감소한다. Ollama 모델은 planner와 Cypher 생성에 계속 사용되며 VRAM 설정은 변경하지 않았다.

## 8. 발견된 문제와 위험

- 첫 실제 실행에서 교양 최소 질문의 LLM QueryPlan이 `value`만 요청해 unit/operator가 행에 없었다. 이를 안전 실패로 숨기지 않고 구조적 Rule 필드 자동 보강으로 수정한 뒤 34학점 Claim과 Citation을 실제 확인했다.
- Claim renderer가 아직 지원하지 않는 새 fact label·필드·복합 문장 조합은 `ANSWER_RENDERING_UNSUPPORTED`가 된다.
- enum 한국어 표시는 중앙 renderer 매핑을 사용하고 원본 enum 값은 온톨로지 catalog로 검증한다. 새 enum 추가 시 표시 매핑과 회귀 검증이 필요하다.
- 이전 자유 형식 답변보다 표현 다양성은 줄지만 사실 의미 보존을 우선한 의도된 제한이다.
- 실제 연구실 vLLM은 planner/Cypher 계층에서 아직 통합 검증하지 않았다. 최종 답변 renderer에는 provider 의존성이 없다.

## 9. 남은 작업

- 최신 Head에 대한 별도 독립 재검토를 수행한다.
- 재검토 통과 후 PR #27을 병합하고 PR #14 프론트엔드에 기존 `ChatResponse` JSON을 연결한다.
- 새 질의 유형은 Claim 유형·값 역할·renderer 문법·공격 fixture를 함께 추가한다.

## 10. 다음 작업 제안

독립 검토에서는 세 재현 오답이 구조상 생성 불가능한지, Claim provenance 변조가 차단되는지, 실제 6문항과 Citation 순서가 반복 실행에서도 동일한지 확인한다.
