# LLM 도구 호출형 다중 턴 GraphRAG

## 목적과 기존 경로 재사용

`AgenticCurriculumChatService`는 별도의 두 번째 질의 파이프라인이 아니다. 대화 문맥을
해석하고 필요한 작업을 고르는 얇은 오케스트레이터이며, 학교 규정을 조회하는 모든
경로는 기존 `PersonalizedCurriculumChatService`와 단일 `SafetyPipeline`을 그대로
통과한다.

```text
브라우저 대화 문맥·UserProfile
→ 제한된 LLM 도구 계획
→ PersonalizedCurriculumChatService
→ QueryPlan·schema selection·canonical Cypher
→ 정적 검증·Neo4j EXPLAIN·execute_read
→ ResultValidator·ClaimValidator·CitationRenderer
→ sealed ChatResponse
→ 보수적으로 검증한 LLM 표시 문장 또는 결정론적 fallback
```

이 계층은 KG 내부 label·relationship 번역이나 그래프 애니메이션을 담당하지 않는다.
기존 progress, inspection, clarification, Citation·PDF presentation 계약을 유지하고
`agent_trace`와 `conversation_update`만 별도 versioned SSE envelope로 추가한다.

## 도구 계약

`src/kg_builder/agent/tools.py`의 도구는 모두 `additionalProperties=false`인 JSON 입력·출력
스키마를 가진다.

| 도구 | 역할 | 안전 경계 |
|---|---|---|
| `read_user_profile` | 브라우저가 보낸 typed profile 확인 | 값을 로그·서버 DB에 저장하지 않음 |
| `resolve_course` | 최근 대화의 stable course code 확인 | Verified bundle에 등록된 code만 허용 |
| `query_curriculum` | 현재 질문 또는 제한된 하위 질문 조회 | 기존 planner와 SafetyPipeline만 호출 |
| `calculate_remaining_credits` | 사용자 진술과 Verified 기준 비교 | Python 계산, null을 0으로 보지 않음 |
| `ask_clarification` | 필요한 최소 사용자 정보 확인 | 통제된 필드 이름과 기존 clarification 계약 사용 |
| `grounded_narrative` | 승인 사실의 대화형 표시 문장 구성 | Claim 값·숫자 역할·enum·극성을 재검증 |

기본 `KG_AGENT_MODE=conservative`에서는 한 턴의 계획 도구 최대 4개, KG 조회 최대 4회,
하위 질문 최대 3개, 시간 예산 120초를 적용한다. 실험용 `expanded`는 각각 6개·6회·5개와
150초로 제한한다. 두 모드 모두 대화에서 전달하는 course code는 최대 20개다. 동일 질문의
재조회는 금지하며, 첫 조회가 `NOT_FOUND` 또는 `UNRESOLVED`인 경우에만 같은 주제의 좁은
후속 조회 한 번을 허용한다. LLM이 만든 도구 인자는 직접 실행하지 않고 오케스트레이터가
검증된 question/context/profile에서 다시 구성한다.

```dotenv
KG_AGENT_MODE=conservative
```

확대 모드는 SafetyPipeline이나 Evidence 승인 범위를 넓히지 않는다. 더 많은 독립 하위
질문과 최대 20개 항목의 승인된 과목 목록 재서술을 허용할 뿐이며, 숫자·과목명·학수번호·
학점·enum의 multiset과 역할을 Python이 다시 증명하지 못하면 canonical 답변으로 돌아간다.
실측 A/B에서 정확도 향상이 입증되지 않아 기본값은 보수적 모드다.

## LLM과 Python의 역할

LLM은 다음을 담당한다.

- 최근 대화의 대명사·생략 해석
- 현재 질문과 독립 하위 질문의 제한된 분해
- 필요한 도구와 추가 조회 선택
- 승인된 Claim 조합의 자연스러운 한국어 초안(기본은 작은 조합만, 확대 모드는 검증 가능한
  과목 목록까지)

Python과 기존 검증 계층은 다음을 계속 강제한다.

- 최근 대화에 없는 course code를 모델이 새로 도입하지 못함
- comment-free canonical Cypher, 허용 schema, parameter, limit, timeout
- 동일 candidate의 정적 검증과 `EXPLAIN` 승인
- `VERIFIED` Fact와 직접 `SUPPORTED_BY`로 연결된 `VERIFIED Evidence`
- Claim 값·단위·operator·polarity와 Citation provenance
- 학점 부족분과 충족 여부의 결정론적 계산
- 사용자 진술(`USER_ASSERTION`)과 학교 근거의 분리

### 자연어 사실 초안 승인

sealed `ChatResponse.answer_text`는 기존 결정론적 Claim renderer의 승인 문장으로 유지한다.
표시용 `conversation_update.display_answer`에서만 LLM 초안을 사용할 수 있다. 단일 과목
field, 단일 수치 규칙, 단일 Boolean 정책, 단순 count/credit aggregate처럼 Python이
의미 보존을 다시 증명할 수 있는 조합만 대상이다.

검증기는 원문과 초안의 숫자 multiset, 숫자-단위 역할, 학년-학기 역할, course code,
subject 표시명, completion enum, 최소·최대 operator, 면제·의무 극성을 비교한다. 숫자
교환, enum 반전, 새 사실, 내부 구문이 하나라도 있으면 초안을 폐기한다. 과목 목록,
Verified 규칙 원문, narrative/recommendation 목록과 복잡한 Claim 조합은 항상 결정론적
문장을 사용한다. 따라서 자연스러운 표현 실패는 Grounding 실패가 아니라 안전한
canonical fallback으로 끝난다.

이 제한은 모델의 사전학습 기억을 학교 규정 근거로 허용하지 않는다. 이전 assistant
답변도 Evidence가 아니며, 요약 요청이나 후속 질문에서 필요한 사실은 KG를 다시 조회한다.

## 대화 문맥 계약

브라우저가 각 요청에 `ConversationContext version=1`을 보낸다.

```text
conversation_id, turn_id
최근 메시지 최대 8개(메시지당 최대 4,000자)
근거로 사용하지 않는 요약 최대 1,200자
현재 주제
최근 승인 course code·Evidence ID
미해결 clarification
```

서버는 대화·프로필을 영구 저장하지 않는다. 새 과목을 명시하면 이전 과목 identity를
상속하지 않고, `그 과목`, `그거`, `학수번호도`처럼 실제 생략 표현일 때만 최근 승인
course code를 사용할 수 있다. 새 채팅은 이전 채팅 주제를 전달하지 않지만 프로필은
브라우저 공통으로 유지된다. clarification 값은 기존 데이터 기반 choice allowlist를
다시 통과한다.

## 브라우저 저장

- 프로필: 기존 versioned `localStorage`
- 채팅방·메시지: `IndexedDB`의 `conversations`, `messages` store, schema version 1
- 현재 채팅 ID: `localStorage`의 opaque ID

채팅방은 생성·선택·개별 삭제·전체 삭제할 수 있다. 프로필 초기화와 채팅 삭제는 서로
독립적이다. 메시지에는 `conversation_id`, `turn_id`, role, timestamp, content,
response status와 Citation/Evidence ID를 저장한다. 동적 내용은 `textContent`로만
표시한다. 저장소를 열 수 없거나 잘못된 version의 row가 있으면 해당 row를 사용하지
않고 현재 화면의 안전한 fallback 안내를 표시한다.

## 사용자 진술과 정정

사용자 정보는 기존 `UserProfile` typed contract와 `ProfileExtractor`가 검증한다. 학년도,
학과, 이수 과목, category별 학점, 공인영어 점수, 진로 목표를 학교 사실과 분리한다.
하나의 명확한 자격시험만 저장된 경우 `점수는 720점으로 정정` 같은 후속 정정은 그
시험에만 적용한다. 중복 과목은 stable course code로 합치고, 중앙 alias resolver가
`데이터베이스개론`과 `데이타베이스개론` 같은 적재 표기를 하나의 Course identity로
해석한다. 모호하거나 범위를 벗어난 값은 임의 선택하지 않는다.

## SSE 하위 호환

sealed `ChatResponse`의 wire 필드는 계속 정확히 8개다.

```text
request_id, status, answer_text, citations,
used_fact_ids, used_evidence_ids, clarification, error_code
```

추가 presentation 정보는 다음 envelope로만 전달한다.

- 기존 `progress`, `inspection_update`, `clarification_options`, `profile_update`, `outcome`
- 신규 `agent_trace version=1`: 실행한 도구 이름·순서·상태·시간의 안전 요약
- 신규 `conversation_update version=1`: 대화 ID, turn ID, 표시 문장, bounded summary와 주제

`agent_trace`는 실제 도구 호출 기록이지 hidden reasoning이나 LLM 원문이 아니다. system
prompt, 모델 원문, raw 질문 trace, 자격증명, URI, 로컬 경로와 미승인 Cypher를 포함하지
않는다. 팀원의 기존 graph/inspection UI는 이 추가 envelope를 무시해도 정상 동작한다.

## 평가

- PR #10 평가 질문 50개: 독립 conversation으로 `/api/ask` SSE 실행
- 미공개 단일 턴 변형 50개: 어순·구어체·숫자·과목·부정·복합·범위 밖 변형
- 다중 턴 20개, 총 65턴: 대명사, 정정, 주제 전환, profile 재사용, 범위 밖, prompt
  injection, 긴 대화 요약

결과표는 [PR #10 50문항 최종 평가](evaluations/question-set-v1-agentic-final.md)와
[agentic 일반화·다중 턴 평가](evaluations/agentic-graphrag-v1.md)에 있다. 상태 일치만으로
의미 정확성을 대신하지 않으며, `ANSWERED`는 Citation이 있을 때만 통과로 본다.

## 현재 제한

- 대화·프로필은 브라우저 로컬 저장소이므로 다른 브라우저나 기기와 동기화되지 않는다.
- 전체 성적표, 재수강 성적, 실시간 시간표·잔여석은 현재 KG에 없다.
- 보수적 모드에서는 복잡한 목록과 원문 규칙을 재작성하지 않는다. 확대 모드도 각 과목명,
  학수번호, 학점, 집계값을 모두 증명할 수 있는 과목 목록 외 복잡한 원문 규칙·추천은
  canonical 문장을 유지한다.
- 작은 로컬 모델의 도구 계획이 실패하면 단일 안전 조회 fallback을 사용한다.
- 서버 인증·다중 사용자 영구 저장·운영 queue는 범위 밖이다.
