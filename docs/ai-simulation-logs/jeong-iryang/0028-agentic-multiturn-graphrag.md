# 0028. LLM 도구 호출형 다중 턴 GraphRAG

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-29 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/agentic-multiturn-graphrag` |
| 선행 PR | PR #32 |
| 작업 상태 | 구현·검증 완료, stacked Draft PR 생성 예정 |

## 1. 목적

PR #32의 개인화·다섯 outcome·Verified KG 보완을 바탕으로, 이전 대화의 생략 표현과
사용자 진술을 안전하게 해석하고 한 질문에서 필요한 KG 조회를 제한적으로 반복하는
도구 호출형 GraphRAG를 구현한다. 채팅방과 메시지는 로그인·서버 DB 없이 브라우저에
저장한다. 기존 SafetyPipeline, sealed `ChatResponse` 8필드, Claim과 Citation provenance는
완화하지 않는다.

## 2. 선행 PR 상태

- 검토·수정 Head: `ddc9446b05b04b7b2c6e260835e8f67fe66cecba`
- PR #32 Actions와 전체 회귀는 성공했다.
- GitHub branch protection의 독립 review 요구만 남아 일반 merge가 차단됐다.
- 자기 승인과 관리자 우회를 사용하지 않고, 사용자 지시에 따라 PR #32 Head에서 stacked
  브랜치를 만들었다. 새 PR은 PR #32 브랜치를 base로 사용해야 한다.

## 3. 구현

### 오케스트레이터·도구

- `AgenticCurriculumChatService`가 bounded conversation을 LLM에 제공하고 도구 계획을
  검증한다.
- `read_user_profile`, `resolve_course`, `query_curriculum`,
  `calculate_remaining_credits`, `ask_clarification`, `grounded_narrative`에 닫힌 JSON
  schema를 정의했다.
- 한 턴 최대 도구 4개, 하위 질문 3개, course code 20개로 제한했다.
- 모델이 작성한 임의 Cypher나 Fact를 실행하지 않고 기존 personalization → planner →
  canonicalization → 정적 검증 → EXPLAIN → Result/Claim 검증 경로를 재사용했다.
- 복수 승인 조회는 내부 sealed composite payload로만 결합하며 Claim ID 충돌, Evidence
  metadata 충돌과 Citation 상한을 검사한다.

### 자연어 표시 답변

- sealed `ChatResponse.answer_text`는 결정론적 Claim renderer의 canonical 문장으로
  유지한다.
- LLM은 승인된 작은 Claim packet으로 자연스러운 `grounded_answer`를 초안할 수 있다.
- Python이 subject, course code, 숫자 multiset과 단위 역할, 학년·학기 역할, enum,
  operator, Boolean polarity를 재검증한다.
- 숫자 역할 교환, enum·극성 반전, 추가 사실, 내부 구문은 거부한다. 과목 목록·원문 규칙·
  복잡한 Claim은 재작성하지 않고 canonical 문장을 사용한다.

### 대화·브라우저 저장

- `ConversationContext version=1`: 최근 8개 메시지, 1,200자 요약, 현재 주제, 최근 승인
  course/evidence, clarification을 제한한다.
- 프로필은 기존 versioned `localStorage`, 채팅방·메시지는 IndexedDB version 1에 저장한다.
- 새 채팅, 선택, 개별 삭제, 전체 삭제, 새로고침 복원을 제공한다. 채팅 삭제와 프로필
  초기화는 분리한다.
- 대명사·생략은 최근 승인 Course identity에만 연결하고, 새 과목을 명시하면 이전 과목을
  상속하지 않는다. 이전 assistant 문장은 Evidence가 아니며 필요한 사실을 KG에서 다시
  조회한다.
- `agent_trace`와 `conversation_update`는 별도 versioned SSE envelope다. 기존 progress,
  inspection, clarification, Citation·PDF와 8필드 response를 변경하지 않는다.

### 개인화·일반화 보완

- 한글 수 학점, 단일 영어시험 점수 정정, 일반 규정 전환과 transcript 질문을 구분했다.
- 명시 과목과 이수구분·학년·학기·학수번호의 requested field를 중앙 slot 규칙으로
  보완했다.
- 근거 없는 대체·신청·실시간 여부는 확인된 Fact와 한계를 분리한다.
- 사용자 학점과 Verified 최소 기준 계산은 Python이 수행하며 null을 0으로 보지 않는다.

## 4. 평가

- PR #10 50문항을 각기 새 conversation으로 실제 `/api/ask` SSE 실행했다.
  결과 상태는 `ANSWERED 22`, `NEEDS_USER_INFO 5`, `INSUFFICIENT_EVIDENCE 16`,
  `OUT_OF_SCOPE 1`, `ADVISORY 6`이며 기대 taxonomy 50/50이다.
- 미공개 단일 턴 50개를 실제 SSE로 실행했다. 분포는 `ANSWERED 29`,
  `INSUFFICIENT_EVIDENCE 10`, `NEEDS_USER_INFO 3`, `OUT_OF_SCOPE 5`, `ADVISORY 3`이다.
- 다중 턴 20개(총 65턴)를 실제 SSE로 실행했다. 분포는 `ANSWERED 43`,
  `INSUFFICIENT_EVIDENCE 8`, `NEEDS_USER_INFO 1`, `OUT_OF_SCOPE 1`, `ADVISORY 12`다.
- 모든 `ANSWERED` turn은 Citation을 보유했다. 대명사, 정정, 주제 전환, 프로필 재사용,
  범위 밖, prompt injection, 긴 요약을 포함했다.
- 상세 결과는 `docs/evaluations/question-set-v1-agentic-final.md`와
  `docs/evaluations/agentic-graphrag-v1.md`에 기록했다.

## 5. 검증

| 검증 | 결과 |
|---|---|
| 전체 unittest | 350 PASS, 6 skip |
| 전체 pytest | 344 PASS, 370 subtests PASS, 6 skip |
| agent/multiturn/web 집중 회귀 | 131 PASS, 50 subtests PASS |
| schema exporter stale check | PASS |
| Verified migration `--check` | PASS |
| Neo4j validate/check/verify | 1,536 / 3,287 / 520 PASS |
| Neo4j opt-in read integration | 3 PASS, 6 subtests PASS |
| 실제 PR #10 50문항 | 전체 실행·문항별 결과 기록 |
| 미공개 단일 턴 50개 | 전체 실행·결과 기록 |
| 다중 턴 20개/65턴 | 전체 실행·결과 기록 |
| Chromium 실제 UI | 프로필 저장·복원, IndexedDB 방 2개·resume, PDF 17쪽, 390px 확인 |
| `uv lock --check`, `git diff --check`, Markdown 상대 링크 | PASS |

실제 UI에서는 자료구조 2학년 1학기 답변과 PDF modal을 확인했고, 새 채팅에서 이전
transcript는 격리되지만 공통 프로필은 유지되며 기존 채팅 재개 시 메시지 2건이 복원됐다.
브라우저 도구의 Citation 개수 selector는 현재 markup과 맞지 않아 0으로 기록됐지만 같은
응답의 원문 보기 버튼으로 발췌 PDF 17쪽 modal과 Evidence 원문을 직접 열어 확인했다.

## 6. 실패한 접근과 수정

- 첫 다중 턴 구현은 과거 Course code를 계속 합쳐 새 과목 질문에도 사용했다. 현재 승인
  subject로 교체하고 새 명시 과목을 우선하도록 수정했다.
- LLM 연결 문구만 허용하면 “자연스러운 사실 답변” 목표를 충족하지 못했다. sealed
  canonical 문장은 유지하면서 안전하게 재검증 가능한 Claim만 표시 문장 재작성을
  허용했다.
- 복합 질의를 한 결과로만 처리하면 일부 근거가 누락됐다. 최대 3개 하위 질문을 각각
  SafetyPipeline으로 재조회하고 sealed 결과만 결합했다.
- 인라인 학점 질문의 도구 선택이 메시지에서 추출한 typed profile보다 먼저 이뤄져 기준만
  답하는 경우가 있었다. 추출·범위 검증 후 계산 도구를 선택하고, 원 질문의 부족분 표현에
  대해 Python 계산을 다시 적용하도록 수정했다.
- 필수 과목을 다른 전공선택 학점으로 채울 수 있는지 묻는 변형은 숫자 기준만 답하면 적용
  허용처럼 보일 수 있었다. 확인된 21학점 기준은 Citation으로 유지하되 직접 대체 근거가
  없어 `INSUFFICIENT_EVIDENCE`로 구분했다.

## 7. 제한과 후속 검토

- PR #32가 먼저 병합돼야 새 PR을 `main`으로 retarget할 수 있다.
- 브라우저 저장은 기기 간 동기화되지 않으며 서버 영구 저장·인증은 없다.
- 복잡한 목록과 Verified 원문 규칙은 자연어 재작성을 하지 않는다.
- 성적·재수강·실시간 시간표·잔여석은 현재 Evidence 범위 밖이다.
- 팀원 담당 KG 한국어 표시·graph animation·육하원칙 UI는 변경하지 않았다.
- 팀원 Draft PR #33도 `server.py`와 정적 UI 파일을 수정하므로 병합 순서에 따라 충돌 해결이
  필요하다. 이 PR은 기존 progress/inspection/graph envelope 이름과 8필드 response를
  제거하거나 바꾸지 않았다.
