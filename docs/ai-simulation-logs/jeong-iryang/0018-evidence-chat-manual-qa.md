# 0018. Evidence chat 수동 QA 핵심 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `fix/jeongiryang/evidence-chat-manual-qa` |
| 관련 커밋 | 이번 작업 커밋 |
| 관련 Issue/PR | 병합된 PR #14 후속 Draft bug PR |
| 작업 상태 | 부분 완료: 구현·단위 검증 완료, 실제 LLM 질의 미실행 |

## 1. 작업 목적

병합된 PR #14의 수동 확인에서 드러난 불필요한 clarification, 영문 메시지, 개인
졸업상담 오분류, 로컬 PDF 미탑재와 가상 진행 표시를 최신 안전 파이프라인을 완화하지
않고 보완한다.

## 2. 작업 전 증상과 일반화된 원인

- 과목 질문에서 생략된 2026/CSE 화면 기본 범위를 planner가 받지 못했다.
- planner 지침이 검색 조건 `filters`와 사용자가 알고 싶은 `requested_fields`를 충분히
  분리하지 않아 “몇 학년·몇 학기”를 누락 입력으로 오해할 수 있었다.
- 모델의 clarification 원문을 상태 메시지로 사용할 수 있었다.
- 개인 수강 이력과 개인별 졸업 판정을 결정론적으로 `UNSUPPORTED`로 분류하는 정책이
  없었다.
- `course_code`는 조회 경로에는 있었지만 단일 과목 Claim과 한국어 renderer가 지원하지
  않았다.
- UI는 실제 pipeline callback 없이 `SUBMITTED/CHECKING/COMPLETED`만 표시했다.
- 로컬에는 정확한 19쪽 PDF가 있었지만 화면용 환경변수가 설정되지 않았다.

수정 전 실제 planner 재현은 로컬 Ollama endpoint가 응답하지 않아 네 질문 모두
`LLM_UNAVAILABLE`이었다. 이를 정상 질의 결과로 기록하지 않았다.

## 3. 수행한 작업

1. Verified bundle의 유일한 학년도·학과에서 PoC 기본 scope를 파생하고 생략된 값에만
   적용했다. 명시된 다른 범위는 덮어쓰지 않는다.
2. 일반적인 한국어 요청 패턴으로 “몇 학년”, “몇 학기”, “학수번호/과목코드”를
   `requested_fields`로 정규화했다. 특정 과목명이나 정답값 분기는 없다.
3. stable Course identity가 하나면 DB 검증으로 진행하고 여러 identity일 때만 기존
   `RESULT_COURSE_AMBIGUOUS` clarification을 사용하도록 했다.
4. `course_code`를 Verified CourseOffering row → canonical Claim → validator → 결정론적
   한국어 renderer로 연결했다.
5. 개인 이력과 졸업 판정 신호가 함께 있는 질문을 모델 호출 전에 일반 정책으로
   `UNSUPPORTED` 처리하고 한국어 대체 질문 범위를 제공했다.
6. 실제 서비스 경계에 10개 progress phase callback을 추가하고 Starlette가 발생 시점에
   SSE로 전달하도록 했다.
7. 정적 검증 완료 후의 Cypher와 정제된 scope만 선택적 inspection envelope로 보낸다.
8. `KG_CHAT_PDF_PATH`를 추가하고 실제 로컬 PDF를 Git 제외 `.env`에서만 지정했다.
   Verified metadata와 PDF 형식·19쪽·SHA 일치를 확인했다.
9. Citation modal에 실제 PyMuPDF text search 강조, 확대·축소, 이전·다음 페이지와 실패
   fallback을 추가했다.

## 4. 변경 파일

| 경로 | 내용 |
|---|---|
| `src/kg_builder/llm/planner.py`, `prompts.py` | 기본 범위, 검색조건/요청필드, UNSUPPORTED 정책 |
| `src/kg_builder/query/progress.py` | 실제 단계 callback 계약 |
| `natural_language_service.py`, `safety_pipeline.py` | planner부터 ResultValidator까지 단계 이벤트·검증 inspection |
| `src/kg_builder/answer/*` | course_code Claim·한국어 답변, Claim/answer 단계 이벤트 |
| `src/evidence_chat/server.py` | 실시간 SSE, 선택적 inspection, single-GPU worker 제한 |
| `pdf_evidence.py`, `static/*` | 19쪽 PDF 검증, modal, 실제 검색 강조, 진행·탐색 UI |
| `tests/test_local_llm_pipeline.py` | 기본 범위·필드 분리·개인 이력·progress 회귀 |
| `tests/test_answer_renderer.py` | course_code Claim·Evidence 회귀 |
| `tests/test_evidence_chat.py` | PDF·SSE·inspection·DOM 계약 회귀 |
| `.env.example`, `README.md`, `docs/evidence-chat.md` | 운영 설정과 최신 동작 |

로컬 `.env`는 PDF 경로만 갱신했으며 Git 추적·staging 대상이 아니다.

## 5. 안전성 결정

- `inspection`은 기본 비활성이며 sealed `ChatResponse` 8필드 밖의 별도 SSE 이벤트다.
- 검증 전 Cypher, 모델 응답, system prompt, 접속 URI, 비밀번호·토큰, traceback은
  수집하거나 브라우저에 보내지 않는다.
- 사용자 질문·과목명별 정답 분기를 만들지 않았다. 기본 범위는 데이터 scope,
  개인 이력 분류는 질문 유형 정책, requested field 정규화는 일반 언어 패턴이다.
- PDF 강조는 text search 결과만 사용하고 임의 bbox를 추정하지 않는다.
- 브라우저 연결이 끊겨도 이미 시작된 모델 호출은 끝날 때까지 서버 동시성 permit을
  유지한다.

## 6. 검증

| 검증 | 결과 |
|---|---|
| 지정된 관련 테스트 최종 | `67 passed, 44 subtests passed` |
| 실제 PDF | PDF 형식, 19쪽, Verified SHA 일치 |
| PDF 1·17·18쪽 PNG | 모두 렌더 성공 |
| 실제 text search | 1쪽 18건 중 4건, 17쪽 23/23건, 18쪽 20/21건 강조 발견 |
| Python compile | 변경 Python 모듈 통과 |
| whitespace | `git diff --check` PASS |
| 네 질문 `/api/ask` | 서비스 startup 실패로 모두 HTTP 503, 답변 결과 미생성 |

## 7. 실행하지 못했거나 의도적으로 실행하지 않은 검증

- planner 직접 호출에서 Ollama가 `LLM_UNAVAILABLE`이었고 실제 `/api/ask`도 로컬
  query service가 `CHAT_STARTUP_ERROR`로 준비되지 않아 네 질문 모두 HTTP 503이었다.
  요청은 실행했지만 기능 결과 검증은 미실행으로 기록한다.
- 전체 unittest, 전체 pytest, 전체 Neo4j 통합, 기존 6문항 전체 회귀, 전체 JSON·Markdown
  검사는 작업 지시에 따라 실행하지 않았다.
- 브라우저 수동 시각 확인은 미실행이며 DOM/ASGI/PDF 렌더 계약으로만 확인했다.

## 8. 남은 제한사항

- 페이지 1의 넓은 표 원문 일부는 PDF text layer 분할 때문에 자동 강조 검색률이 낮다.
  강조 실패 시 페이지와 Citation 원문을 유지한다.
- 로컬 모델 서버가 준비된 뒤 지정된 네 질문만 실제 `/api/ask`로 확인해야 한다.
- 개인 수강 이력 데이터와 개인별 졸업 판정은 여전히 지원 범위 밖이다.

## 9. 다음 작업

Draft bug PR 최신 Head에서 GitHub Actions와 독립 검토를 수행하고, Ollama가 준비되면 네
질문과 PDF modal을 수동 확인한다. Draft 해제·승인·merge는 사용자가 결정한다.
