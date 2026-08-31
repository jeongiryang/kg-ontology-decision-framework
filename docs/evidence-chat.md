# CurriculumChatService 기반 학사규정 근거 챗봇

## 목적과 호출 흐름

`evidence_chat`은 PR #14의 Starlette·vanilla HTML/CSS/JavaScript 화면과 PDF 강조 기능을 유지하면서 최신 승인 응답 계층을 직접 연결한 로컬 PoC다.

```text
브라우저
→ Starlette POST /api/ask
→ 브라우저 UserProfile 검증·현재 메시지 정보 추출
→ AgenticCurriculumChatService
→ 대화 문맥 해석·도구 계획·결과 기반 재탐색
→ PersonalizedCurriculumChatService → CurriculumChatService
→ 자연어 QueryPlan·동적 Cypher·SafetyPipeline
→ ResultValidator
→ 구조화 Claim·결정론적 승인 답변
→ 승인 ChatResponse
→ FactPacket별 Claim 의미를 재검증한 자연어 표시 문장 또는 부분 canonical fallback
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
KG_AGENT_MODE=agentic

KG_CHAT_PDF_PATH=/local/ignored/path/2026_curriculum_excerpt.pdf
KG_CHAT_SHOW_QUERY_DETAILS=off
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
- `PersonalizedCurriculumChatService` (request-local 사용자 진술과 다섯 outcome)
- `AgenticCurriculumChatService` (bounded 대화 문맥·도구 계획·grounded narrative)
- 동시 실행 제한기

요청마다 driver나 모델 client를 다시 만들지 않는다. 종료 시 Neo4j driver만 닫으며 Ollama 프로세스는 종료하지 않는다. 기본 동시 LLM 요청은 1개다.

| 변수 | 기본값 | 역할 |
|---|---:|---|
| `KG_CHAT_MAX_CONCURRENT` | `1` | 동시에 실행할 전체 chat 요청 수(1~4) |
| `KG_AGENT_MODE` | `agentic` | 승인 결과를 보고 중단·재탐색하는 bounded Agent 정책 |
| `KG_CHAT_CLIENT_TIMEOUT_SECONDS` | `180` | 브라우저 대기 제한(60~900초) |
| `KG_CHAT_DEBUG` | `false` | 정제된 request ID/error code 표시 |
| `KG_CHAT_SHOW_QUERY_DETAILS` | `off` | `full`에서만 승인 Cypher·실제 traversal 상세 표시 (`summary`는 단계 요약만) |
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

일반 화면에는 `answer_text`, 상태, clarification과 Citation만 표시한다. 내부 Fact/Evidence ID, request ID와 error code는 숨긴다. `KG_CHAT_DEBUG=true`일 때만 정제된 request ID와 allowlist 오류 코드를 개발 정보로 표시한다. `내가`, `학생`, `졸업` 같은 단어만으로 개인 이력 질문을 차단하지 않으며, 일반 규정 조회·단일 조건 비교·전체 개인 이력 판정을 구분한다. 브라우저 프로필에 검증된 영역별 학점이 있으면 Verified 기준과의 차이는 Python이 계산하고, 필요한 이수과목·학점이 없으면 `NEEDS_USER_INFO`, 성적·재수강처럼 현재 KG에 규정 근거가 없으면 `INSUFFICIENT_EVIDENCE`로 구분한다. 사용자 진술만으로 전체 졸업 가능 여부를 확정하지 않는다.

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

sealed 응답과 별도로 `profile_update version=1`과 `outcome version=1` SSE envelope를
보낸다. outcome은 `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`,
`OUT_OF_SCOPE`, `ADVISORY` 중 하나다. Fact·Citation을 다시 조립하지 않으며 기존
8필드 소비자도 그대로 동작한다. 자세한 계약은
[질의 정확도와 개인화](query-personalization.md)를 참고한다.

다중 턴에서는 브라우저가 `ConversationContext version=1`을 함께 보내고 서버가
`agent_trace version=1`, `conversation_update version=1`을 별도 envelope로 반환한다.
프로필은 기존 `localStorage`, 채팅방과 메시지는 schema version 3 `IndexedDB`에만
저장된다. 각 assistant message의 presentation snapshot이 그 turn의 Citation, progress,
inspection, Agent trace와 요청 충족도를 함께 보존하므로 다음 답변이 이전 근거를
덮어쓰지 않는다. 미완료 목록·계산·판단 요청은 conversation의 `pending_request`에 남아
짧은 재요청에서 복원되고, profile update만으로 답변 작업을 완료 처리하지 않는다.
새 채팅은 이전 채팅의 주제를 사용하지 않지만 프로필은 유지된다. 서버는 채팅 내용을
영구 저장하지 않으며 이전 assistant 문장을 학교 규정 Evidence로 사용하지 않는다.
도구·문맥·자연어 초안 검증의 상세 계약은
[LLM 도구 호출형 다중 턴 GraphRAG](agentic-multiturn-graphrag.md)를 참고한다.

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

`POST /api/ask`는 `progress`, 선택적 `clarification_options`, 선택적
`inspection_update`, `profile_update`, `outcome`, `request_fulfillment`, `result`, `error`
SSE 이벤트를 보낸 뒤 스트림을 종료한다. `result.response`는 승인된 8개 wire 필드이고
`result.presentation`은 상태 라벨, PDF page group, 공개 PDF 상태와 선택적 debug
metadata다. `request_fulfillment version=1`은 요청 항목별 결과와 전체
`COMPLETE/PARTIAL/UNRESOLVED` 상태만 전달하며 응답 사실을 재구성하지 않는다.
`inspection_update`는 단계별 allowlist 요약만 담으며 실제 확정 시점에 `result`보다 먼저
전송될 수 있다.

각 실제 단계 행은 기본적으로 접힌 disclosure button이다. 키보드로 열고 닫을 수 있으며
`aria-expanded`와 `aria-controls`가 연결된다. 완료된 행은 해당 assistant message에도
남는다. 상세
정보가 없는 단계에는 disclosure를 만들거나 임의 설명을 생성하지 않고 상태·실제
소요시간만 표시한다.

`KG_CHAT_SHOW_QUERY_DETAILS=full`일 때만 단계 disclosure에 정제된 QueryPlan, 선택
스키마, 승인된 읽기 전용 Cypher, 정제된 파라미터, EXPLAIN 연산자,
행·Fact·VERIFIED Evidence·Claim·Citation 수와 단계 시간을 추가한다. 승인 Cypher는
`NEO4J_EXPLAIN`, 그래프 조회 결과는 `GRAPH_EXECUTION` 단계 안에 배치한다. 공개
Cypher는 lexer가 실제 주석을 제거한 comment-free canonical 문자열이다. Cypher는
동일 생성 attempt의 `STATIC_VALIDATION`과 `NEO4J_EXPLAIN`이 모두 완료된 뒤에만
`inspection_update`로 승인한다. 정적 검증 직후에는 후보 생성·검증 중이라는 고정
문구만 표시한다. EXPLAIN 실패 후보는 공개하지 않고, 후속 단계 실패로 재생성을
시작하면 이전 승인 후보도 UI에서 철회한다. 재시도 성공 시 최종 승인 후보만 남으며
모든 후보가 실패하면 Cypher 영역을 표시하지 않는다.

승인된 Cypher는 가로 스크롤, 접기·펼치기와 키보드 접근 가능한 복사 버튼을 제공한다. 검증 전·실패 후보 Cypher, 접속 URI·계정, 로컬 경로, 비밀번호·토큰·API key, system prompt, 모델 원문, traceback, 내부 승인 seal·digest는 상세 모드에서도 포함하지 않는다. sealed `ChatResponse` 8필드는 변경하지 않는다.

상세 모드는 각 assistant message 아래에 `그래프 탐색`과 `Cypher 보기`를 독립된
disclosure로 제공한다. 새 질문이 와도 이전 turn의 snapshot을 다시 읽으므로 그래프,
Cypher, 처리 과정이 덮어써지지 않는다. `그래프 탐색`은 실행이 끝난 뒤 승인된 실제
traversal 순서를 재생할 수 있으며 Neo4j 실행 중계를 가장하지 않는다. 재시도에서 실패한
후보는 projection을 만들지 않으며 모든 후보가 실패하면 관련 disclosure를 표시하지 않는다.

## inspection traversal projection

상세 모드의 그래프는 Neo4j 전체 데이터를 탐색하지 않고 이미 승인된 런타임 산출물을
표시용으로 축소한다. 별도 DB 조회와 쓰기 쿼리는 없다. 내부 Label·Relationship Type과
Cypher는 영어 계약을 유지하며 노드·관계·실행 단계의 사용자 표기만 generated schema
catalog의 한국어 이름을 사용한다.

- 질의 구조 그래프는 EXPLAIN까지 통과한 canonical Cypher에서 validator가 추출한
  순서 있는 MATCH hop과 그 양끝 노드만 사용한다. 다중 노드인데 승인 hop이 없거나
  hop이 계약을 벗어나면 그래프를 만들지 않는다. 단일 라벨의 zero-hop 조회만 노드 하나로
  표시한다. 관계 간선이나 중간 노드를 추정하지 않는다.
- 결과 traversal 그래프(`RESULT_TRAVERSAL`)는 승인 hop, ResultValidator를 통과한
  `VERIFIED` 행과 ClaimValidator가 승인한 `(fact_id, evidence_id)` 집합이 정확히
  일치할 때만 만든다. PROFILE operator는 이 그래프의 KG 노드가 아니다.
- 결과 edge는 직접 `SUPPORTED_BY`만 허용한다. 승인 전 행, 사용하지 않은 Evidence,
  `REVIEW_REQUIRED` 항목은 projection에 들어가지 않는다.
- 브라우저 node ID는 요청마다 새 HMAC key로 만든 opaque `ui:*` 값이다. raw Neo4j
  element ID, Fact/Evidence ID, 승인 seal·digest는 envelope에 포함하지 않는다.
- 노출 필드는 표시명, 한국어 node/relationship type, 검증 상태와 Evidence의 발췌
  페이지만이다. 명세에 한국어 표기가 없을 때도 내부 영문 label/relationship을 일반 UI에
  대체 표시하지 않고 안전한 한국어 fallback을 쓴다.

versioned `inspection_update` 하위의 `query_graph`, `traversal_graph`와
`provenance_graph`는 서로 의미가 다른 presentation
전용이다. 답변이나 Citation을 변경할 권한이 없으며
sealed `ChatResponse`에는 추가되지 않는다. 브라우저는 외부 라이브러리 없이 반응형 SVG
`viewBox`, 실제 edge 방향으로 depth를 계산하는 결정론적 layered DAG 레이아웃과
`ResizeObserver`를 사용한다. 같은 depth의 노드는 나란히 두고, 기본 화면은 컨테이너
너비에 맞추며 읽기 어려워질 때는 graph 내부 가로 스크롤을 허용한다. 긴 node 이름은 두 줄
뒤 ellipsis와 SVG title로 보존하고 관계 label은 배경 상자와 offset으로 edge와 분리한다.
확대·축소·화면 맞춤·초기화, 키보드 node 선택과 관계 목록 fallback을 제공하며 좁은
화면에서는 위에서 아래로 배치한다. `NEO4J_EXPLAIN` 뒤에는 승인 논리 경로를 회색으로
표시하며 방문 완료로 표시하지 않는다. `GRAPH_EXECUTION` 뒤에는 PROFILE step이 정확히
한 graph edge에만 대응할 때 그 edge만 순차 강조하고, Filter·Projection·Limit 또는
모호한 관계 반복은 별도 PROFILE 단계 목록에 남긴다. 최종 turn에서는 승인 논리 경로,
PROFILE 대응, VERIFIED Fact·Evidence 결과를 제목과 설명이 다른 영역으로 함께 유지한다.
노드·간선은 `UNVISITED`(회색), `ACTIVE`
(accent), `VISITED`(완료 색)를 실제 순서에 따라 구분한다. `prefers-reduced-motion:
reduce`에서는 이동 효과 없이 최종 상태와 순서 목록을 즉시 표시한다. 전체 GRAPH_EXECUTION
실측 시간만 보이고 operator별 시간을 배분·추정하지 않는다. Neo4j가 hop별 물리 노드 방문
실시간 이벤트를 제공하는 것처럼 표현하거나 임의 지연·가짜 경로를 만들지 않는다.

## Citation과 PDF 표시

Citation은 다음 검증 값을 그대로 사용한다.

- `evidence_id`, 직접 연결된 `fact_ids`
- `source_text`
- `excerpt_page`, `source_pdf_page`, `printed_page`

화면은 일반 사용자에게 내부 ID를 표시하지 않고 다음처럼 세 페이지를 구분한다.

```text
발췌 PDF 17쪽 · 원본 PDF 262쪽 · 인쇄 페이지 254쪽
```

동일 Evidence는 한 번만 표시한다. 각 turn의 `근거 N개` disclosure를 열 때
`이 답변에 사용된 VERIFIED 근거` 카드로 원문·발췌/원본/인쇄 페이지·원문에서 보기 버튼을
지연 생성하며, 긴 원문은 세 줄 뒤 펼칠 수 있다. 페이지 이미지와 강조 영역은 그 안의 보조
disclosure를 열 때 다시 지연 생성한다. PDF 이미지가 실패해도 Evidence 원문과 페이지는
유지된다. backend에서는 Claim과 Citation provenance를 검증하지만 문장 위치별 인라인
anchor는 wire 계약에 없으므로 UI가 문장별 연결을 추정하지 않는다.

발췌 PDF는 커밋하지 않는다.

```dotenv
KG_CHAT_PDF_PATH=/local/ignored/path/2026_curriculum_excerpt.pdf
```

지정하지 않으면 Git 제외된 기본 경로 `data/raw/2026_curriculum_excerpt.pdf`를 확인한다. 파일은 PDF로 열려야 하고 Verified source metadata의 19쪽 기준과 일치해야 탑재된다. 로컬 절대 경로와 문서 해시는 health·브라우저 응답에 노출하지 않는다. health는 `pdf_mounted=true/false`만 제공한다. 현재 bbox가 없으므로 Evidence 원문 조각을 PyMuPDF로 실제 검색하며 임의 좌표를 만들지 않는다. Citation의 “원문에서 보기”는 해당 발췌 페이지 modal, 확대·축소와 이전·다음 페이지를 제공한다. 검색에 실패해도 페이지 이미지와 Evidence 원문을 유지하고 강조 실패만 알린다.

## 입력과 브라우저 보안

- 빈 질문과 2,000자 초과 질문을 서버에서 거부한다.
- 질문 JSON은 `question`, 서버가 발급한 clarification 선택을 되돌려 보내는 선택적
  `resolved`, version 1 브라우저 `profile`과 bounded `conversation`만 허용한다.
- 프로필은 localStorage에만 보존하며 서버 DB나 Neo4j에 영구 저장하지 않는다. 손상된
  저장값은 빈 프로필로 fallback하고 서버는 타입·범위·controlled vocabulary를 재검증한다.
- 채팅에서 추출한 학적·이수 정보는 `USER_ASSERTION`으로 표시하고 KG 사실과 합치지 않는다.
- UI는 질문·답변·Evidence를 `textContent`로만 삽입한다.
- PDF route는 Starlette 정수 path converter를 사용한다.
- 서버 오류, traceback, 로컬 경로, 비밀번호, 토큰을 반환하지 않는다.
- 외부 URL 자동 이동과 임의 Cypher 입력 경로가 없다.
- 전송 중 `inFlight`와 비활성 버튼으로 중복 제출을 막는다.
- `AbortController`와 최소 60초 client timeout을 사용한다.
- 질문 입력창은 한 줄에서 시작해 최대 5줄까지 자동 확장하고 그 이후에만 내부
  스크롤을 사용한다. Enter 전송·Shift+Enter 줄바꿈을 유지하며 한글 IME 조합 중 Enter는
  전송하지 않는다. 입력창은 첫 질문 전부터 오류·답변 완료 후까지 계속 화면에 남는다.

## 연속 채팅 화면

UI는 질문 입력·진행·결과의 별도 3단계 화면과 `새 질문하기` 버튼을 사용하지 않는다.
사용자 질문은 전송 즉시 현재 채팅에 추가되고, assistant placeholder에 실제 progress가
누적된 뒤 같은 turn의 최종 답변으로 교체된다. 하단 입력창에서 바로 다음 질문을 보내며
동일한 `conversation_id`를 유지한다. 새 대화 문맥은 `새 채팅` 버튼으로만 만든다.

각 assistant turn 아래에는 그 turn의 `근거 N개`, `처리 과정`,
`그래프 탐색`, `Cypher 보기`, Agent 도구 기록을 독립적으로 열 수 있다. live assistant는
실제 `inspection_update`가 도착한 시점에만 같은 graph renderer로 승인 경로·PROFILE 대응
경로·최종 traversal graph를 차례로 갱신한다. 처리 과정은 실제 단계 데이터가
있는 경우에만 펼치며 대상·작업·시점·위치·목적·방법을 고정된 안전 문구와 공개 측정값으로
요약한다. LLM의 원문이나 hidden reasoning은 사용하지 않는다. PDF modal과 상세 패널을 열어도 입력창과 대화
위치를 잃지 않는다. 사용자가 과거 메시지를 읽고 있으면 스트리밍 중 강제로 아래로
이동하지 않고 최신 메시지 이동 버튼을 표시한다. 채팅방 생성·최근순 선택·개별/전체 삭제,
새로고침 복원과 모바일 레이아웃을 제공하며 프로필 초기화는 채팅 삭제와 분리한다.

입력창 위에 고정된 추천 질문이나 카테고리 chip은 만들지 않는다. desktop에서는 앱 셸이
가용 폭을 최대 1,440px까지 사용하되 일반 답변 문장은 약 78ch로 제한한다. 전체 과목
목록·그래프·Cypher 같은 넓은 내용만 assistant turn 전체 폭을 사용한다. transcript를 주
스크롤 영역으로 삼고 composer는 항상 그 아래에 고정해 페이지와 채팅의 이중 스크롤을
피한다.

`COURSE_LIST`는 일반 질의의 100행 제한과 분리된 250행 hard limit을 사용한다. 이 예외는
전체 목록 요청에만 적용하며 정적 검증·EXPLAIN·ResultValidator를 우회하지 않는다. 긴
목록은 stable Course identity로 중복을 제거하고 Python이 VERIFIED 결과 전체를 영역별로
렌더링한다. LLM에는 영역·고유 과목 개수 같은 bounded 요약만 보내므로 원시 189행을 다시
생성하거나 생략할 권한이 없다. Citation은 응답 계약에 모두 보존하되 사용자가 disclosure를
열 때 렌더링한다. 100개가 넘는 traversal은 먼저 실제 영역별 요약만 그리고, 사용자가
요청할 때 승인된 전체 node·edge를 그린다. 추가 Neo4j 조회나 가짜 경로는 만들지 않는다.

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
