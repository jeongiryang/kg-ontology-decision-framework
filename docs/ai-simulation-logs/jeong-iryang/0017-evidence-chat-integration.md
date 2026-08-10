# 0017. PR #14 최신 백엔드·프론트 통합

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/hwangdaegyeom/evidence-chat` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | Draft PR #14, 병합된 PR #27 |
| 작업 상태 | 완료 |

## 1. 작업 목적

황대겸의 기존 Starlette·vanilla HTML/CSS/JavaScript UI와 PDF 강조 기능을 보존하면서,
PR #27로 병합된 `CurriculumChatService`를 같은 Python 프로세스의 `/api/ask`에 직접
연결한다. 별도 FastAPI/API 프로세스와 고정 Intent 파이프라인은 만들지 않는다.

## 2. 작업 전 상태와 리뷰

- PR #27은 merge commit `5ada4dd526d503ed1f3197ce1c82c9bcf2e0f2ab`로
  `main`에 병합돼 있었다.
- PR #14 Head는 `fc903c8c15f7d6f9356f4ffd9494026d5d86a872`, Draft,
  checks 없음, 최신 main과 충돌 상태였다.
- 기존 `CHANGES_REQUESTED`는 `.gitignore`의 `AGENTS.md` 제외 회귀, TestClient
  의존성 누락, 최신 main 병합 필요를 지적했다.
- 병합 충돌은 `.gitignore`와 README에서 발생했다. 최신 main의 비밀·runtime log
  제외 정책과 문서 내용을 보존하고 PR #14의 UI 문서 링크를 함께 유지했다.
- Raw·Verified KG와 `ontology/ontology_spec.json`은 수정하지 않았다.

## 3. 수행한 작업

1. PR #14 브랜치에 최신 `origin/main`을 merge 방식으로 반영했다.
2. `RuleBasedPlanner`, 고정 6 Intent, Intent별 프론트 답변 조립과 상세 Cypher 표시
   런타임을 제거했다.
3. Starlette lifespan에서 query 전용 Neo4j driver, provider-neutral LLM client,
   `NaturalLanguageQueryService`, `CurriculumChatService`를 한 번 구성했다.
4. `ChatResponse.to_dict()` 8필드 drift를 검사하고 표시 형식만 파생하는
   `ChatResponseAdapter`를 추가했다.
5. 모든 `ChatStatus`와 clarification, 지원 범위 안내, SAFE_FAILURE를 UI에 연결했다.
6. Citation을 발췌 페이지별로 묶고 동일 Evidence를 제거했다. 내부 ID는 기본 UI에서
   숨기고 세 페이지와 Evidence 원문, PDF 강조를 유지했다.
7. 실제 내부 단계 callback이 없으므로 진행 표시는 질문 전송·답변 확인·완료로
   단순화하고 QueryPlan/Cypher/파라미터 노출을 제거했다.
8. `AbortController`, 60초 이상 timeout, 경과시간, 중복 제출 방지와 기본 동시 요청
   1개 제한을 추가했다.
9. 건강 상태와 PDF 오류에서 Neo4j endpoint, 계정, 로컬 경로, 문서 해시와 예외
   원문을 제거했다.
10. Starlette 1.6 `TestClient`의 공식 현재 계약에 맞춰 `httpx2`를 dev dependency로
    선언하고 lockfile을 동기화했다.

## 4. 변경 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/evidence_chat/server.py` | 재구성 | lifespan, 최신 chat composition root, SSE, 동시성·안전 오류 |
| `src/evidence_chat/chat_adapter.py` | 추가 | 승인 응답 8필드 검사와 표시 전용 page grouping |
| `src/evidence_chat/pdf_evidence.py` | 수정 | Evidence 중복 제거, 공개 PDF 상태와 경로 비노출 |
| `src/evidence_chat/static/index.html` | 수정 | 상태·clarification·Citation 중심 화면 |
| `src/evidence_chat/static/app.js` | 수정 | 안전 DOM, SSE, 취소·timeout, 상태별 화면 |
| `src/evidence_chat/static/app.css` | 수정 | 상태·접이식 근거·경과시간 스타일 |
| `src/evidence_chat/answer.py` | 삭제 | 구형 Intent별 답변 조립 제거 |
| `src/evidence_chat/planner.py` | 삭제 | 구형 rule-based planner 제거 |
| `src/evidence_chat/pipeline.py` | 삭제 | 구형 병렬 실행 경로 제거 |
| `tests/test_evidence_chat.py` | 재작성 | fake service 기반 UI·adapter·PDF·보안 계약 테스트 |
| `tests/test_evidence_chat_integration.py` | 추가 | 실제 Starlette/Ollama/Neo4j 6문항 SSE smoke |
| `docs/evidence-chat.md` | 재작성 | 최신 호출·상태·근거·운영 계약 |
| `README.md`, `.env.example` | 수정 | 실행 링크와 UI 환경변수 |
| `pyproject.toml`, `uv.lock` | 수정 | Starlette TestClient용 `httpx2` dev 계약 |

황대겸의 기존 AI 작업 로그는 수정하지 않았다.

## 5. 주요 결정과 이유

- 프론트 adapter는 `ChatResponse`를 만들지 않고 승인 응답을 읽기만 한다.
- answer text와 Citation wire 값은 변경하지 않고 PDF 표시용 page group만 파생한다.
- 브라우저에는 일반화된 진행 정보만 보내고 Cypher·QueryPlan·DB 파라미터를 보내지
  않는다.
- `KG_CHAT_DEBUG=false`가 기본이며, 활성화해도 request ID와 정제된 error code만
  화면에 제공한다.
- 클라이언트 취소는 UI 대기만 중단할 수 있으며 이미 실행 중인 Ollama 계산은 즉시
  취소되지 않는다는 제한을 문서화했다.
- TestClient 의존성은 추측한 `httpx`가 아니라 현재 사용 중인 Starlette 1.6 계약의
  `httpx2`를 사용한다.

## 6. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| UI 단위·계약 테스트 | `uv run pytest -q tests/test_evidence_chat.py` | 16 PASS, 6 subtests PASS |
| 전체 unittest | `uv run python -m unittest discover -s tests -v` | 125개 중 119 PASS, 외부 통합 6 skip |
| 전체 pytest | `uv run pytest -q` | 119 PASS, 6 skip, 119 subtests PASS |
| Neo4j 통합 | `KG_NEO4J_INTEGRATION=1 uv run pytest -q` | 122 PASS, 3 skip, 125 subtests PASS |
| schema stale | `uv run python -m kg_builder.query.schema_exporter check` | PASS |
| lock | `uv lock --check` | PASS |
| Python compile | `uv run python -m compileall src` | PASS |
| whitespace | `git diff --check` | PASS |
| Starlette 실제 6문항 | `KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_evidence_chat_integration.py -s` | 1 PASS, 6 subtests PASS |
| 격리 환경 프론트 테스트 | `uv run --isolated --locked pytest -q tests/test_evidence_chat.py` | 16 PASS, 6 subtests PASS |
| 실제 HTTP 페이지·health | 로컬 Uvicorn 실행 후 `/`, `/api/health` 조회 | HTTP 200, service ready, debug off |

첫 실제 smoke에서 편입생 정답 문구를 오직 “면제” 토큰으로 검사해 “교양 이수
의무가 없다”라는 의미적으로 동일한 결정론적 답변을 테스트가 오판했다. 런타임 코드는
수정하지 않고 기대 의미를 교정한 뒤 재실행해 통과했다.

실제 `/api/ask` 결과는 다음과 같다.

| 질문 | 상태 | 핵심 결과 | Citation | 시간 |
|---|---|---|---:|---:|
| 교양 최소 | ANSWERABLE | 최소 34학점 | 1 | 12.797초 |
| 균형교양 | ANSWERABLE | 4개 영역·영역별 1과목·최소 12학점 | 2 | 11.969초 |
| 편입생 | ANSWERABLE | 교양 이수 의무 없음 | 1 | 11.673초 |
| 자료구조 개설 | ANSWERABLE | 2학년 1학기 | 1 | 17.843초 |
| 전공필수 | ANSWERABLE | 9과목·21학점, 0학점 과목 포함 | 9 | 12.588초 |
| 자료구조 이수구분 | ANSWERABLE | 전공선택 | 1 | 13.092초 |

총 측정시간은 약 79.962초, 평균은 약 13.327초였다. 최종 사실 답변 LLM 호출은
0회이며 모델은 planner와 Cypher 생성에만 사용된다.

## 7. 데이터 불변성

- 테스트 전후 Neo4j 노드 1,518개, 관계 3,260개, Evidence 511개를 유지했다.
- 통합 테스트 trace는 `TemporaryDirectory`에 주입하고 종료 후 제거했다.
- 저장소 runtime log, Raw·Verified KG, 원본 온톨로지, `.env`, `AGENTS.md`, PDF와
  모델 파일은 수정하지 않았다.

## 8. 남은 제한사항

- 자동 브라우저 도구를 사용한 시각·모바일 검증은 아직 수행하지 않았다. HTTP/SSE와
  정적 DOM 계약 및 실제 Uvicorn의 페이지·health HTTP 200 확인으로 대체 검증했다.
- PDF 원본이 현재 탑재되지 않아 실제 페이지 이미지·강조 시연은 미실행이다. 합성 PDF
  단위 테스트는 통과했다.
- UI 요청 취소가 이미 시작된 Ollama 요청을 서버 측에서 강제 중단하지는 않는다.
- 로컬 단일 사용자 PoC로서 인증, CSRF, 다중 사용자 queue는 후속 범위다.

## 9. 다음 작업

- 최신 PR #14 Head를 독립 검토한다.
- 브라우저에서 실제 PDF를 탑재한 수동 시연을 수행한다.
- 차단 문제가 없으면 사용자가 Draft 해제와 병합 여부를 결정한다.
