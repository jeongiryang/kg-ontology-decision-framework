# 0013. VERIFIED Evidence 기반 한국어 답변 계층

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/evidence-answer-renderer` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | PR #15 병합 후속 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #15의 자연어 Text-to-Cypher 파이프라인이 반환한 `VERIFIED` Fact/Evidence 결과를 근거가 포함된 한국어 답변 JSON으로 변환한다. 모델이 페이지·근거·검증 상태를 만들지 못하게 출력 범위를 제한하고 Python이 답변 주장과 Citation을 검증·조립한다.

## 2. 요청 내용 요약

- provider-neutral `StructuredLLMClient`를 사용하는 제한 답변 초안 생성
- Fact/Evidence ID, 검증 상태, 직접 provenance, 숫자·과목명 grounding 검사
- LLM이 아닌 Python이 세 종류 페이지와 Evidence 원문으로 Citation 조립
- 비확정 상태의 결정론적 응답과 최대 1회 답변 재생성
- 공식 `CurriculumChatService`와 최종 JSON CLI
- 프롬프트 인젝션 및 실제 대표 6문항 검증
- Raw·Verified·온톨로지·Neo4j 데이터 불변 유지

## 3. 작업 전 상태

- PR #15가 merge commit `c987e73629ce32b9d13c43f224515d9d33507150`으로 `main`에 병합된 것을 확인했다.
- 기준 `main`은 `c987e73`이었고 작업 트리는 clean이었다.
- 로컬 Neo4j는 노드 1,518개, 관계 3,260개, Evidence 511개였다.
- Ollama 0.32.5와 `qwen2.5-coder:14b`가 사용 가능했다.
- 팀원 Draft PR #14는 기존 교차 검토와 동일 Head `fc903c8`이어서 중복 리뷰를 제출하지 않았다.

## 4. 수행한 작업

1. `AnswerDraft`, `Citation`, `ChatResponse`, 상태 통제값을 정의했다.
2. LLM 출력 필드를 `answer_text`, `used_fact_ids`, `used_evidence_ids`로 제한했다.
3. 모든 scoped Fact 커버리지, 명명 Course 커버리지, 직접 Fact–Evidence 연결과 `VERIFIED` 상태를 검사했다.
4. 조회 행에 없는 숫자와 한국어 엔터티, 모델이 만든 페이지, 내부 Cypher·프롬프트·비밀값 노출 표현을 차단했다.
5. 여러 과목의 Fact 수와 학점 합계는 Python이 계산한 `derived_facts`로 모델에 제공했다.
6. Citation은 검증된 행의 Evidence ID, 세 페이지 번호, 원문을 사용해 중복 제거 후 조립했다.
7. 비ANSWERABLE 상태는 모델을 호출하지 않고 결정론적으로 처리했다.
8. 답변 검증 실패 시 오류 코드만 전달해 1회 재생성하고 재실패 시 일반화된 `SAFE_FAILURE`를 반환했다.
9. 기존 구조화 CLI를 유지하면서 최종 답변용 `kg_builder.answer.cli`를 추가했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/answer/__init__.py` | 생성 | 답변 계층 공개 계약 |
| `src/kg_builder/answer/contracts.py` | 생성 | 답변 초안·Citation·최종 응답 타입 |
| `src/kg_builder/answer/generator.py` | 생성 | provider-neutral 제한 답변 생성 |
| `src/kg_builder/answer/validator.py` | 생성 | Fact/Evidence·숫자·엔터티 검증 |
| `src/kg_builder/answer/renderer.py` | 생성 | 검증 행 기반 Citation 조립 |
| `src/kg_builder/answer/service.py` | 생성 | 최종 Chat Service와 실패 처리 |
| `src/kg_builder/answer/cli.py` | 생성 | 최종 한국어 답변 JSON CLI |
| `tests/test_answer_renderer.py` | 생성 | 계약·검증·인젝션 단위 테스트 |
| `tests/test_answer_integration.py` | 생성 | 실제 6문항·DB 불변 통합 테스트 |
| `docs/evidence-answer-renderer.md` | 생성 | 응답 계약과 팀원 실행 가이드 |
| `README.md` | 수정 | 최종 답변 CLI와 문서 링크 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 다음 로그 번호와 0013 링크 |

## 6. 주요 결정과 이유

- **모델 출력 최소화**: 페이지·Evidence 원문·검증 상태는 모델이 만들지 않고 Python이 원본 행에서 복사한다.
- **전체 Fact 커버리지**: 목록 질문에서 일부 Fact만 골라 답하는 누락을 막기 위해 scoped 결과의 모든 Fact ID와 명명 과목을 답변에 요구한다.
- **거부 우선 grounding**: 한국어 표현 검사는 전체 형태소 분석기가 아니므로 일부 자연스러운 표현을 안전 실패로 거부할 수 있다. 근거 없는 엔터티를 허용하는 것보다 재생성·실패를 우선한다.
- **Provider 독립성**: 답변 생성기는 `StructuredLLMClient`만 사용하며 Ollama/vLLM 분기를 추가하지 않았다.
- **비확정 상태 무모델 처리**: clarification, 범위 밖, unresolved, not found에는 생성 모델을 호출하지 않는다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 의존성·lock | `uv sync --locked`, `uv lock --check` | PASS |
| Python compile | `uv run python -m compileall -q src tests` | PASS |
| unittest | `uv run python -m unittest discover -s tests -v` | 96개 중 91 PASS, 환경 통합 5 skip |
| pytest | `uv run pytest -q` | 91 PASS, 5 skip, 95 subtests PASS |
| Neo4j 통합 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q` | 94 PASS, 2 skip, 101 subtests PASS |
| 실제 답변 6문항 | `KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_answer_integration.py -s` | 1 test / 6 subtests PASS |
| LLM 스키마 stale | `uv run python -m kg_builder.query.schema_exporter check` | PASS |
| DB 불변 | 실제 답변 통합 테스트 전후 조회 | 1,518 / 3,260 / 511 유지 |

실제 6문항은 교양 34학점, 균형교양 4개 영역·영역별 1과목·12학점, 편입생 교양 의무 면제, 자료구조 2학년 1학기, 전공필수 9과목·21학점, 자료구조 전공선택을 한국어 답변과 직접 Citation으로 반환했다.

## 8. 발견된 문제와 위험

- Ollama 0.32.5는 답변 JSON Schema의 문자열 `maxLength`를 HTTP 400으로 거부했다. 모델 스키마에서는 해당 키워드를 제거하고 Python `AnswerValidator`의 2,000자 상한은 유지했다.
- 초기 한국어 grounding은 조사·어미와 목록 설명을 엔터티로 오인했다. 검증된 행 밖의 과목명을 허용하지 않으면서 일반 문법 표면형과 질문에 명시된 검증 학과 scope만 제한적으로 허용했다.
- 한국어 grounding은 완전한 의미 검증기가 아니며 향후 평가셋 기반 개선이 필요하다.
- OpenAI-compatible adapter 계약은 단위 테스트했지만 실제 연구실 vLLM 답변 생성 통합은 미실행이다.
- Neo4j Community Edition PoC의 계정 권한은 최종 읽기 전용 보안 경계를 보장하지 못할 수 있다.

## 9. 남은 작업

- 황대겸 프론트엔드의 상태·Citation UI 계약을 최신 `ChatResponse`와 연결한다.
- 실제 vLLM/OpenAI-compatible 모델로 동일 6문항 및 안전 실패 회귀를 수행한다.
- 질문 평가셋을 확장해 한국어 grounding의 오탐·누락률을 계량한다.

## 10. 다음 작업 제안

Draft PR 교차 검토 후 프론트엔드 PR의 API 가정과 `ChatResponse` 계약을 비교하고, 백엔드 HTTP 경계 및 Citation 표시를 구현한다.
