# 결과 기반 Agent와 연속 채팅 GraphRAG

## 목적과 단일 안전 경로

`AgenticCurriculumChatService`는 두 번째 질의 파이프라인이 아니다. LLM은 대화 문맥을
해석하고 필요한 조회를 제안하지만, 학교 규정 조회는 매번 기존
`PersonalizedCurriculumChatService`로 돌아가 동일한 안전·근거 경계를 통과한다.

```text
브라우저 ConversationContext·UserProfile
→ LLM 질문 해석·도구 계획
→ 기존 QueryPlan·schema selection·canonical Cypher
→ 정적 검증·Neo4j EXPLAIN·execute_read
→ ResultValidator·ClaimValidator·CitationRenderer
→ 확보한 근거 평가·필요할 때만 추가 조회
→ 승인된 FactPacket별 자연어 답변 구성
→ Claim·Citation 의미 재검증
→ sealed ChatResponse + 별도 presentation SSE
```

검증 전 Cypher, LLM 원문, hidden reasoning은 공개하지 않는다. KG 내부 label 번역과
그래프 애니메이션도 이 계층의 책임이 아니다. 기존 progress, inspection,
clarification, Citation·PDF 계약은 그대로 유지한다.

## 결과 기반 Agent 정책

기본값은 `KG_AGENT_MODE=agentic`이다.

```dotenv
KG_AGENT_MODE=agentic
```

첫 조회 결과가 복합 질문 전체를 다루지 못하면 LLM이 공개 가능한 승인 Claim 요약과
상태만 보고 `STOP` 또는 `QUERY`를 선택한다. 추가 질문은 기존 planner가 검증한 pending
질문이거나, 현재 질문·대화 주제와 동일하고 중앙 Course identity resolver를 통과한 좁은
질문이어야 한다. 추가 질문도 반드시 처음부터 같은 QueryPlan·SafetyPipeline을 거친다.

`agentic` 정책의 상한은 다음과 같다.

- 계획 도구 최대 6회, KG 조회 최대 6회
- 결과 평가·재계획 최대 3회
- 독립 하위 질문 최대 3개
- 한 턴 최대 180초
- 동일 도구·동일 질문 반복 금지
- FactPacket 문장 부분 재작성 최대 1회

승인된 단순 답변이고 독립 하위 질문이 없으면 추가 평가 호출 없이 즉시 멈춘다. 시간·조회
예산이 끝나면 이미 승인된 근거만 사용한다. 독립 요구가 해결되지 않았으면 부분 사실을
보여 줄 수 있어도 최종 outcome은 `INSUFFICIENT_EVIDENCE`로 유지한다.

프로필 갱신과 답변 작업은 서로 다른 계약이다. 한 턴에서 학과·학년·이수과목을
`USER_ASSERTION`으로 갱신했더라도 조회·계산·추천 같은 요청 항목이 남아 있으면 Agent는
종료할 수 없다. 각 요청 항목은 `list_courses`, `lookup_course`, `lookup_requirement`,
`calculate_remaining`, `check_eligibility`, `recommend_courses` 같은 일반 action과 검증된
filter로 표현되고, 항목별로 `ANSWERED`, `NEEDS_USER_INFO`,
`INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`를 기록한다. 전체 턴은 모든 항목 처리 여부에 따라
`COMPLETE`, `PARTIAL`, `UNRESOLVED`가 된다.

전체 목록의 `모든·전부·모두·다·빠짐없이·몽땅` 범위는 Verified Evidence가 직접 연결된
고유 Course identity 수와 실제 반환 수를 대조한다. 동일 Course의 복수 offering을 과목명
중복으로 세지 않는다. 답하지 못한 목록·계산 항목은 `pending_request`로 유지하며, 다음
사용자의 재요청은 직전 사용자 발화와 이 구조를 함께 사용해 범위와 동작을 복원한다.
이전 assistant 답변은 복원 근거나 Evidence로 사용하지 않는다.

추가 조회가 성공해도 원질문의 의미 경계를 다시 검사한다. 예를 들어 과목 대체 인정의
직접 근거가 없을 때 일반 졸업학점 규칙을 대신 답으로 채택하지 않는다. 후속 조회는 원래
요구의 대체·인정 의미를 유지해야 하며, 그렇지 않으면 근거 부족 상태로 종료한다.

`conservative`와 `expanded`는 회귀 진단을 위해 남아 있다. `expanded`는 단순히 고정
상한만 늘리던 이전 실험 모드이며, 결과를 보고 중단·재계획하지 않으므로 기본값이 아니다.

## 도구 계약

`src/kg_builder/agent/tools.py`의 도구 입력·출력은 모두
`additionalProperties=false` JSON Schema로 제한된다.

| 도구 | LLM이 결정하는 범위 | Python 안전 경계 |
|---|---|---|
| `read_user_profile` | 필요한 프로필 사용 제안 | typed DTO만 읽고 서버에 저장하지 않음 |
| `resolve_course` | 대화 속 과목 참조 제안 | Verified bundle의 stable course code만 허용 |
| `query_curriculum` | 현재·하위 질문 선택 | 기존 planner·canonicalization·SafetyPipeline 재진입 |
| `calculate_remaining_credits` | 계산 필요 여부 제안 | 사용자 진술과 Verified 기준을 Python이 계산 |
| `ask_clarification` | 최소 누락 정보 제안 | 통제된 필드와 기존 choice allowlist 검증 |
| `assess_evidence` | 승인 결과를 보고 종료·추가 조회 선택 | Fact/Cypher/Evidence를 생성하지 못함, 예산·중복 검사 |
| `grounded_narrative` | FactPacket 안의 전체 문장 구성 | Claim 값·숫자 역할·enum·극성·Citation 재검증 |

## FactPacket 전체 답변과 부분 복구

LLM에는 sealed 응답에서 추출한 공개 가능한 FactPacket만 전달한다. Packet에는 승인된
Claim의 유형, subject, 값, 단위, polarity와 canonical 문장만 들어간다. Evidence ID,
private seal·digest, Cypher와 DB 접속 정보는 답변 작성 입력이 아니다.

LLM은 각 `fact:1`~`fact:4` packet에 대응하는 문장과 짧은 도입·마무리를 작성할 수 있다.
Python은 packet별로 다음을 재검증한다.

- subject와 Course identity
- 모든 수치와 수치-단위 역할
- 학년·학기 역할
- completion type·operator·polarity
- 인정·대체·추천 같은 새 판단 표현
- 내부 구현·prompt·Cypher·자격증명 표현

한 packet만 실패하면 그 packet만 근거 범위 안에서 한 번 재작성한다. 다시 실패한 부분만
결정론적 canonical 문장으로 교체하고, 다른 검증된 LLM 문장은 유지한다. sealed
`ChatResponse.answer_text`와 8개 wire 필드는 계속 기존 승인 renderer 결과이고, 자연스러운
표시 문장은 `conversation_update.display_answer`에만 들어간다.

LLM의 사전학습 기억과 이전 assistant 답변은 Evidence가 아니다. 이전 Citation이 있어도
후속 질문의 학교 사실은 현재 KG에서 다시 검증한다.

## LLM과 결정론적 검증의 역할

LLM이 주도한다.

- 대명사·생략과 현재 대화 주제 해석
- 복합 질문의 제한된 하위 문제 분해
- 필요한 도구와 다음 KG 조회 선택
- 확보한 근거의 충분성 평가
- 최소 clarification 제안
- FactPacket 안에서 자연스러운 한국어 전체 답변 구성

Python과 기존 검증기는 다음을 계속 강제한다.

- 사용자 입력·프로필 타입과 범위
- comment-free canonical Cypher, schema whitelist, parameter, limit, timeout
- 동일 candidate의 정적 검증과 Neo4j `EXPLAIN`
- `VERIFIED` Fact와 직접 `SUPPORTED_BY`인 `VERIFIED Evidence`
- Claim 값·단위·operator·polarity와 Citation provenance
- 학점 산술과 졸업요건 계산
- 사용자 진술(`USER_ASSERTION`)과 학교 규정의 분리
- `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`,
  `ADVISORY`의 최종 안전 판정

## 연속 채팅 화면

웹 UI는 질문·진행·결과의 별도 3개 화면을 사용하지 않는다. 같은 채팅방에서 사용자와
assistant 메시지를 계속 쌓고, 하단 입력창을 항상 유지한다.

```text
채팅방 목록
└─ 현재 채팅의 turn 목록
   ├─ 사용자 질문
   ├─ assistant 답변
   │  ├─ 근거·PDF
   │  ├─ 처리 과정
   │  ├─ 승인 Cypher·inspection
   │  └─ Agent 도구 기록
   └─ 다음 turn …
고정 하단 입력창
```

`새 채팅`만 새 `conversation_id`를 만든다. 답변 후 `새 질문하기`를 누를 필요가 없으며,
후속 질문은 같은 conversation의 새 `turn_id`로 전송된다. Enter는 전송, Shift+Enter는
줄바꿈이고, 한글 IME 조합 중 Enter는 전송하지 않는다. 요청 중에는 중복 제출만 막고
입력창은 화면에서 사라지지 않는다.

각 assistant message는 그 turn의 결과와 presentation snapshot을 따로 보관한다. 따라서
새 답변이 이전 Citation, progress, 승인 Cypher, inspection, agent trace를 덮어쓰지 않는다.
PDF modal을 닫으면 열었던 근거 버튼으로 focus가 돌아간다.

## 브라우저 저장 계약

- 프로필: versioned `localStorage`
- 채팅방·메시지: IndexedDB `conversations`, `messages`, schema version 3
- 현재 채팅 ID: `localStorage`의 opaque ID
- 서버 DB 저장: 없음

version 3 message row의 `presentation_snapshot version=1`은 해당 turn의 result, outcome,
clarification, timeline, inspection update 원본 envelope, agent trace,
`request_fulfillment`와 안전 오류만 저장한다. conversation row에는 미완료
`pending_request`가 들어간다. version 1·2 row는 읽을 수 있고, 다시 저장할 때 version 3으로
올린다. 잘못된 row는 사용하지 않는다. 프로필 초기화와 채팅 삭제는 별개다.

채팅방은 생성·최근 업데이트 순 선택·개별 삭제·전체 삭제를 지원한다. 첫 번째 의미 있는
질문으로 제목을 만들고, 새 채팅은 이전 주제를 넘기지 않는다. 프로필만 채팅방 사이에서
공유한다. 질문 전송 때 최신 메시지로 이동하되, 사용자가 과거 turn을 읽는 중이면 강제로
스크롤하지 않고 `최신 메시지 보기` 버튼을 표시한다.

## 대화 문맥과 SSE

브라우저는 각 요청에 `ConversationContext version=1`을 보낸다.

```text
conversation_id, turn_id
최근 메시지 최대 8개
근거로 사용하지 않는 bounded summary
현재 주제
최근 승인 course code·Evidence ID
미해결 clarification
미완료 requested item
```

sealed `ChatResponse` wire 필드는 정확히 다음 8개를 유지한다.

```text
request_id, status, answer_text, citations,
used_fact_ids, used_evidence_ids, clarification, error_code
```

추가 정보는 별도 versioned SSE envelope다.

- 기존 `progress`, `inspection_update`, `clarification_options`, `profile_update`, `outcome`
- `agent_trace version=1`: 실제 도구 이름·순서·상태·시간과 allowlist metadata
- `conversation_update version=1`: conversation/turn ID, 표시 답변, bounded summary와 주제
- `request_fulfillment version=1`: 항목별 처리 상태, 전체 충족도와 미완료 요청

브라우저는 `inspection_update` 전체 envelope를 turn snapshot에 유지하므로 팀원 presentation
확장이 추가 필드를 사용해도 별도 전역 결과 상태를 만들 필요가 없다. `agent_trace`는 실제
도구 기록이지 chain-of-thought가 아니다.

## 평가

- PR #10 질문 50개: 독립 conversation으로 실제 `/api/ask` SSE 실행
- 미공개 단일 턴 50개: 어순·구어체·숫자·과목·부정·복합·범위 밖 변형
- 다중 턴 20개, 65턴: 대명사, 정정, 주제 전환, profile 재사용, clarification, 범위 밖
- 실제 브라우저: 연속 질문, 새로고침 복원, 채팅 전환·삭제, PDF, 390px, 키보드·IME

결과는 [PR #10 평가](evaluations/question-set-v1-agentic-final.md)와
[일반화·다중 턴 평가](evaluations/agentic-graphrag-v1.md),
[자연어 요청 충족도 평가](evaluations/conversational-fulfillment-v1.md)에 기록한다. 상태 일치만으로 의미
정확성을 대신하지 않으며, 모든 `ANSWERED`는 Citation을 가져야 한다.

## 현재 제한

- 브라우저 로컬 저장이므로 다른 기기와 동기화되지 않는다.
- 전체 성적표, 재수강 성적, 실시간 시간표·잔여석은 현재 KG에 없다.
- 작은 로컬 모델의 도구 판단이 유효 JSON을 만들지 못하면 검증된 pending 질문 또는 단일
  안전 조회로 축소한다.
- LLM 표시 문장의 Claim 검증이 반복 실패한 packet은 canonical 문장으로 남는다.
- 서버 인증, 계정별 영구 저장과 다중 사용자 운영 queue는 범위 밖이다.
