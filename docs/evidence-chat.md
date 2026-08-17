# CurriculumChatService 기반 학사규정 근거 챗봇

## 목적과 호출 흐름

`evidence_chat`은 PR #14의 Starlette·vanilla HTML/CSS/JavaScript 화면과 PDF 강조 기능을 유지하면서 최신 승인 응답 계층을 직접 연결한 로컬 PoC다.

```text
브라우저
→ Starlette POST /api/ask
→ CurriculumChatService
→ 자연어 QueryPlan·동적 Cypher·SafetyPipeline
→ ResultValidator
→ 구조화 Claim·결정론적 한국어 답변
→ 승인 ChatResponse
→ 표시 전용 adapter·SSE
→ 상태·Citation·PDF 근거 UI
```

별도 FastAPI/API 프로세스, 고정 6 Intent 플래너, 프론트 전용 답변 조립은 없다. `ChatResponse`는 백엔드가 발급한 값을 읽고 직렬화할 뿐이며 adapter는 `answer_text`, Citation, Fact/Evidence ID를 변경하지 않는다.

## 실행 조건

```dotenv
NEO4J_QUERY_URI=neo4j://localhost:7687
NEO4J_QUERY_USER=your-read-only-neo4j-user
NEO4J_QUERY_PASSWORD=
NEO4J_QUERY_DATABASE=neo4j

KG_LLM_PROVIDER=ollama
KG_LLM_BASE_URL=http://127.0.0.1:11434
KG_LLM_MODEL=qwen2.5-coder:14b
KG_LLM_TIMEOUT_SECONDS=180
KG_LLM_MAX_RETRIES=1
KG_LLM_CONTEXT_LENGTH=8192
KG_LLM_MAX_OUTPUT_TOKENS=2048

KG_CHAT_PDF_PATH=/local/ignored/path/2026_curriculum_excerpt.pdf
KG_CHAT_SHOW_QUERY_DETAILS=false
```

질의 자격증명은 ingestion 자격증명으로 fallback하지 않는다. 동적 Cypher 운영에는 별도 읽기 전용 계정이 최종 방어선이다. Community Edition 등으로 권한 분리가 보장되지 않는 환경은 PoC의 잔여 운영 위험이다.

```bash
uv sync --locked
uv run python -m evidence_chat.server
```

기본 주소는 `http://127.0.0.1:8501`이다. 인증이 없는 로컬 개발 서버이므로 외부 인터페이스에 바인딩하지 않는다.

## 애플리케이션 수명주기

Starlette lifespan에서 다음 객체를 한 번 구성하고 `app.state`에 보관한다.

- query 전용 Neo4j driver
- provider-neutral `StructuredLLMClient`
- `NaturalLanguageQueryService`
- `CurriculumChatService`
- 동시 실행 제한기

요청마다 driver나 모델 client를 다시 만들지 않는다. 종료 시 Neo4j driver만 닫으며 Ollama 프로세스는 종료하지 않는다. 기본 동시 LLM 요청은 1개다.

| 변수 | 기본값 | 역할 |
|---|---:|---|
| `KG_CHAT_MAX_CONCURRENT` | `1` | 동시에 실행할 전체 chat 요청 수(1~4) |
| `KG_CHAT_CLIENT_TIMEOUT_SECONDS` | `180` | 브라우저 대기 제한(60~900초) |
| `KG_CHAT_DEBUG` | `false` | 정제된 request ID/error code 표시 |
| `KG_CHAT_SHOW_QUERY_DETAILS` | `false` | 승인된 Cypher와 정제된 탐색 정보의 실시간 표시 |
| `KG_CHAT_PDF_PATH` | 빈 값 | Git 제외된 19쪽 발췌 PDF 경로 |
| `CHATBOT_HOST` | `127.0.0.1` | 로컬 bind 주소 |
| `CHATBOT_PORT` | `8501` | UI 포트 |

브라우저 취소·연결 종료는 UI 대기를 중단하지만 이미 워커에서 시작된 Ollama 호출은 즉시 끝나지 않을 수 있다. 서버는 단일 GPU를 위해 새 요청을 동시성 제한기에 대기시킨다.

## ChatResponse와 상태별 화면

프론트 adapter는 `ChatResponse.to_dict()`의 8개 필드 계약을 검사한다.

```text
request_id, status, answer_text, citations,
used_fact_ids, used_evidence_ids, clarification, error_code
```

일반 화면에는 `answer_text`, 상태, clarification과 Citation만 표시한다. 내부 Fact/Evidence ID, request ID와 error code는 숨긴다. `KG_CHAT_DEBUG=true`일 때만 정제된 request ID와 allowlist 오류 코드를 개발 정보로 표시한다. 개인 수강 이력·개인별 졸업 판정은 `SAFE_FAILURE`가 아니라 결정론적 `UNSUPPORTED` 안내로 처리한다. `내가`, `학생`, `졸업` 같은 단어만으로 개인 이력 질문으로 판정하지 않으며, 일반 규정 조회·단일 조건 비교·전체 개인 이력 판정을 구분한다. 단일 점수와 규정의 자동 비교가 미지원이면 개인 수강 이력 부재가 아닌 별도의 고정 한국어 안내를 사용한다.

화면의 기본 검색 범위는 `2026`, `department:cwnu:cse`다. 사용자가 연도·학과를 생략한 과목 질문에만 기본값을 채우며, 사용자가 명시한 다른 범위를 덮어쓰지 않고 `OUT_OF_SCOPE`로 판정한다. planner 계약은 검색에 이미 주어진 `filters`와 사용자가 답으로 요구한 `requested_fields`를 분리한다. 따라서 “몇 학년·몇 학기”는 누락 필터가 아니라 조회 필드다. 동명 여부는 질문 문자열이 아니라 ResultValidator가 반환된 stable `course_identity` 수로 판단한다.

| 상태 | 화면 처리 |
|---|---|
| `ANSWERABLE` | 결정론적 한국어 답변과 VERIFIED Citation |
| `CLARIFICATION_REQUIRED` | 오류가 아닌 추가 정보 요청, Citation 없음 |
| `OUT_OF_SCOPE` | 2026·공통 교양·컴퓨터공학과 범위 안내 |
| `UNSUPPORTED` | 현재 지원하지 않는 질문 유형 안내 |
| `UNRESOLVED` | 원문·정책 미확정 안내, 추정 금지 |
| `NOT_FOUND` | 검증된 결과 0건 안내, 확정 부정으로 표현하지 않음 |
| `SAFE_FAILURE` | 중앙에서 정한 일반 안전 문구, Citation 없음 |

`ANSWER_VALIDATION_FAILED`는 status가 아니라 `SAFE_FAILURE`의 내부 오류 코드다.

되묻기 선택지는 sealed `ChatResponse`에 필드를 추가하지 않는다. 질문 분석이 실제로
`CLARIFICATION_REQUIRED`로 끝났을 때만 별도 versioned SSE envelope를 보낸다.

```text
type=clarification_options, version=1, missing, options
```

각 option은 안정적인 `choice_id`, 적재 데이터에서 가져온 한국어 `label`, 서버가 다시
검증할 `filter`와 `value`를 분리한다. 브라우저는 선택한 `filter`·`value`를 다음
`POST /api/ask`의 `resolved` 객체로 보내며, planner는 같은 질문의 선택지를 적재
데이터에서 다시 만들어 allowlist 대조를 통과한 값만 사용한다. 일반 질문에는 이
envelope가 없고, `result.response`는 계속 위의 8개 필드만 갖는다.

백엔드 공식 호출 계약은 다음 keyword-only 형태다.

```python
ask(question, *, resolved=None, progress_callback=None)
```

따라서 되묻기 선택과 progress callback을 함께 사용할 수 있지만 두 번째 위치 인자로
서로 오바인딩할 수는 없다. 선택값은 Cypher 문자열에 직접 삽입되지 않고 QueryPlan의
검증된 parameter 경로로만 전달된다.

## SSE와 진행 표시

백엔드는 실제 서비스 경계에서 다음 단계의 시작·완료·실패 이벤트만 전송한다.

```text
QUESTION_ANALYSIS → SCHEMA_SELECTION → CYPHER_GENERATION
→ STATIC_VALIDATION → NEO4J_EXPLAIN → GRAPH_EXECUTION
→ RESULT_VALIDATION → CLAIM_BUILDING → ANSWER_RENDERING → COMPLETED
```

실행하지 않은 단계를 완료로 만들지 않으며 가짜 퍼센트, hidden chain-of-thought, system prompt와 모델 원문은 보내지 않는다. 화면은 callback이 실제 도착한 단계 행만 그때 생성해 진행 중·완료·실패로 누적하고, 아직 발생하지 않은 미래 단계를 `WAITING`으로 선생성하지 않는다. 완료된 행과 서버가 보낸 실제 소요시간은 답변 화면의 `처리 과정 보기`에도 유지한다. 연결을 취소하면 이미 완료된 행은 유지하고 당시 진행 중이던 행만 취소 상태로 표시한다. 브라우저에 해당 단계의 신뢰 가능한 시작 시각이 있으면 취소 시점까지 계산하고, 없으면 가짜 `0ms` 대신 시간을 생략한다. 안전 파이프라인이 후보를 재생성할 때는 실패 오류 코드와 `안전한 질의를 다시 생성하는 중` 이벤트를 남기되 실패 후보 원문은 보내지 않는다.

`POST /api/ask`는 `progress`, 선택적 `clarification_options`, 선택적 `inspection_update`, `result`, `error`, `end` SSE 이벤트를 보낸다. `result.response`는 승인된 8개 wire 필드이고 `result.presentation`은 상태 라벨, PDF page group, 공개 PDF 상태와 선택적 debug metadata다. `inspection_update`는 단계별 allowlist 요약만 담으며 실제 확정 시점에 `result`보다 먼저 전송될 수 있다.

`KG_CHAT_SHOW_QUERY_DETAILS=true`일 때만 처리 중·결과 화면의 `Cypher 및 지식그래프 탐색 정보 보기`에 정제된 QueryPlan, 선택 스키마, 승인된 읽기 전용 Cypher, 정제된 파라미터, EXPLAIN 연산자, 행·Fact·VERIFIED Evidence·Claim·Citation 수와 단계 시간을 누적한다. 공개 Cypher는 lexer가 실제 주석을 제거한 comment-free canonical 문자열이다. Cypher는 동일 생성 attempt의 `STATIC_VALIDATION`과 `NEO4J_EXPLAIN`이 모두 완료된 뒤에만 `inspection_update`로 승인한다. 정적 검증 직후에는 후보 생성·검증 중이라는 고정 문구만 표시한다. EXPLAIN 실패 후보는 공개하지 않고, 후속 단계 실패로 재생성을 시작하면 이전 승인 후보도 UI에서 철회한다. 재시도 성공 시 최종 승인 후보만 남으며 모든 후보가 실패하면 Cypher 영역을 표시하지 않는다.

승인된 Cypher는 가로 스크롤, 접기·펼치기와 키보드 접근 가능한 복사 버튼을 제공한다. 검증 전·실패 후보 Cypher, 접속 URI·계정, 로컬 경로, 비밀번호·토큰·API key, system prompt, 모델 원문, traceback, 내부 승인 seal·digest는 상세 모드에서도 포함하지 않는다. sealed `ChatResponse` 8필드는 변경하지 않는다.

## Citation과 PDF 표시

Citation은 다음 검증 값을 그대로 사용한다.

- `evidence_id`, 직접 연결된 `fact_ids`
- `source_text`
- `excerpt_page`, `source_pdf_page`, `printed_page`

화면은 일반 사용자에게 내부 ID를 표시하지 않고 다음처럼 세 페이지를 구분한다.

```text
발췌 PDF 17쪽 · 원본 PDF 262쪽 · 인쇄 페이지 254쪽
```

동일 Evidence는 한 번만 표시하고 발췌 페이지 기준으로 그룹화한다. 근거가 4건 이상이면 기본으로 접어 `근거 N건 보기`로 표시한다. PDF 이미지가 실패해도 Evidence 원문과 페이지는 유지된다.

발췌 PDF는 커밋하지 않는다.

```dotenv
KG_CHAT_PDF_PATH=/local/ignored/path/2026_curriculum_excerpt.pdf
```

지정하지 않으면 Git 제외된 기본 경로 `data/raw/2026_curriculum_excerpt.pdf`를 확인한다. 파일은 PDF로 열려야 하고 Verified source metadata의 19쪽 기준과 일치해야 탑재된다. 로컬 절대 경로와 문서 해시는 health·브라우저 응답에 노출하지 않는다. health는 `pdf_mounted=true/false`만 제공한다. 현재 bbox가 없으므로 Evidence 원문 조각을 PyMuPDF로 실제 검색하며 임의 좌표를 만들지 않는다. Citation의 “원문에서 보기”는 해당 발췌 페이지 modal, 확대·축소와 이전·다음 페이지를 제공한다. 검색에 실패해도 페이지 이미지와 Evidence 원문을 유지하고 강조 실패만 알린다.

## 입력과 브라우저 보안

- 빈 질문과 2,000자 초과 질문을 서버에서 거부한다.
- 질문 JSON은 `question` 하나만 허용한다.
- UI는 질문·답변·Evidence를 `textContent`로만 삽입한다.
- PDF route는 Starlette 정수 path converter를 사용한다.
- 서버 오류, traceback, 로컬 경로, 비밀번호, 토큰을 반환하지 않는다.
- 외부 URL 자동 이동과 임의 Cypher 입력 경로가 없다.
- 전송 중 `inFlight`와 비활성 버튼으로 중복 제출을 막는다.
- `AbortController`와 최소 60초 client timeout을 사용한다.

## 테스트

실제 Neo4j/Ollama 없이 fake `CurriculumChatService`를 lifespan에 주입한다.

```bash
uv run pytest -q tests/test_evidence_chat.py
```

Starlette 1.6 ASGI 테스트는 dev dependency의 `httpx2.AsyncClient`와 `ASGITransport`를 사용한다. 단위 테스트는 전체 status, 8필드 drift, Citation 1/9건, 페이지 grouping, 실제 단계·inspection 분리, 안전한 DOM API, 입력 길이, traversal, PDF fallback을 검사한다.

실제 서비스 통합은 로컬 Neo4j와 Ollama가 준비됐을 때만 실행한다.

```bash
KG_NEO4J_INTEGRATION=1 uv run pytest -q
KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_answer_integration.py -s
```

실행하지 않은 실제 브라우저 시연이나 모델 통합은 통과로 기록하지 않는다.

## 제한사항

- 현재 데이터 범위는 2026학년도 공통 교양과 컴퓨터공학과 교육과정이다.
- 요청 취소가 이미 시작된 로컬 모델 계산을 강제 중단하지는 않는다.
- 단일 프로세스·단일 GPU PoC이며 다중 사용자 queue, 인증, CSRF 정책은 후속 범위다.
- PyMuPDF 기반 강조는 텍스트 레이어가 있는 PDF에 한정된다.
