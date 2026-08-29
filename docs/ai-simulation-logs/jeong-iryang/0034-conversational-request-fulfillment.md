# 0034. 자연어 요청 충족도와 연속 대화 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-29 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/conversational-request-fulfillment` |
| 기준 commit | `8ea92cdf5dbb16f01eda52b5ad631563abc7b8ad` |
| 구현 commit | `ba7d1b782a9da1f23863674dbf369f0afe5085f8` |
| 관련 PR | Draft PR #37 |
| 작업 상태 | 구현·검증·Draft PR 생성 완료 |

## 1. 작업 목적

PR #32~#35가 통합된 최신 `main`에서 프로필 갱신이 본래 질문을 삼키고, 전체 과목
목록·복합 요청·후속 재요청·사용자 정정을 충분히 처리하지 못하던 구조를 일반적인
요청 항목과 충족도 계약으로 보완한다. sealed `ChatResponse` 8필드, canonical Cypher,
SafetyPipeline, VERIFIED Fact–Evidence와 팀원의 traversal UI는 유지한다.

## 2. 작업 전 증상과 원인

- `컴공과`가 포함된 목록 질문은 ProfileExtractor가 학과를 갱신한 뒤
  `profile statement only` 경로에서 Agent loop를 종료했다.
- Agent 종료 조건이 프로필 부수효과와 질문 답변 작업을 분리하지 않아 `profile_update`
  성공을 턴 성공으로 오인했다.
- `모든·전부·다` 범위와 반환 Course identity 수를 대조하는 계약이 없어서 부분 목록도
  성공할 수 있었다.
- assistant 실패 뒤 사용자가 `다 출력해달라고`라고 반복해도 완료하지 못한 원요청을
  구조화해 저장하지 않았다.
- 하나의 outcome만 있어 복합 질문에서 어느 항목이 답변·미해결인지 표현하기 어려웠다.
- 동일 의미의 CourseOffering을 이름 목록에서 중복 제거하는 기준이 명확하지 않았다.
- 학점 정정은 category 값만 바꾸고 총합 등 파생값을 재계산하는 보장이 약했다.
- 추천·가능 여부에서 개설 학기 사실과 선수·수강 제한 규정이 섞일 수 있었다.
- IndexedDB의 presentation snapshot에는 항목별 충족도와 pending request가 없었다.

## 3. 구현

### 요청 항목과 pending request

- `RequestedItem`, `PendingRequest`, `TurnFulfillmentStatus`를 versioned presentation
  계약으로 추가했다.
- profile update는 `side effect`, 목록·조회·요건·계산·가능 여부·추천은 `answer task`로
  분리했다.
- 각 항목은 `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`,
  `OUT_OF_SCOPE` 중 하나를 가지며 전체 턴은 `COMPLETE`, `PARTIAL`, `UNRESOLVED`다.
- 미완료 항목은 conversation context에 저장한다. 반복 발화는 이전 사용자 요청만
  복원하며 assistant 답변을 Evidence나 원질문으로 사용하지 않는다.
- `request_fulfillment version=1` SSE를 sealed 응답과 분리했다.

### 목록·복합 질문

- 과목 목록 표현을 일반 action으로 정규화하고 학년도·학과·학년·학기·이수구분 filter와
  requested field를 분리했다.
- 전체 범위 표현은 `모든·전체·전부·모두·다·빠짐없이·몽땅`과 목록·명단·정리 표현을
  포함한다. 질문 원문이나 평가 ID를 비교하지 않는다.
- 2026 CSE 전체 목록은 직접 VERIFIED Evidence가 있는 offering의 stable Course identity를
  기준으로 완전성을 대조한다. 복수 offering 때문에 같은 과목명을 중복하지 않는다.
- 상위 교양 영역은 Verified bundle의 자식 area ID를 읽어 제한된 하위 조회로 분해한다.
- 복합 졸업요건+과목 목록은 등록된 fact family별로 조회하고 항목별로 결과를 합친다.

### 프로필·정정·근거 경계

- 질문 속 학과·학년은 프로필에 반영하되 조회 요청을 계속한다.
- `42학점이 아니라 45학점` 정정은 최신 category 값을 채택하고 category 합계와 잔여
  교양·전공·총학점을 모두 다시 계산한다.
- 다음 학기 열린 추천은 현재 학년을 모르면 그 한 필드만 확인하며 특정 과목을 추측하지
  않는다.
- 개설 학기를 찾았더라도 선수·수강 제한의 직접 근거가 없으면 가능 여부 항목은
  `INSUFFICIENT_EVIDENCE`로 남긴다.
- 시험명을 생략한 일반 영어 기준은 Verified atomic Rule family를 조회하고 기준값은 KG
  결과에서만 읽는다.

### 브라우저

- IndexedDB를 schema version 3으로 올리고 기존 version 1·2 row를 읽은 뒤 저장 시
  migration한다.
- 각 assistant message에 요청 충족도와 pending request를 보존한다.
- 프로필 갱신은 작은 비차단 상태이며 답변 카드 본문을 대체하지 않는다.
- 긴 답변은 접근 가능한 접기·펼치기를 제공하고 추천 질문 chip은 desktop wrap, mobile
  swipe를 유지하면서 기본 두꺼운 scrollbar를 숨긴다.
- 기존 턴별 Citation, PDF, 처리 과정, 실제 traversal, 한국어 표시와 승인 Cypher를
  보존했다.

## 4. KG 대조

- Verified bundle과 Neo4j 모두 `1,536 nodes / 3,287 relationships / 520 Evidence`다.
- CSE의 직접 근거가 있는 고유 Course/Offering은 37개이며 전공필수 9개, 전공선택 28개다.
- 전체 목록 응답은 37개 Course identity와 Citation 37건을 반환했다.
- 균형교양 자식 area의 직접 VERIFIED Evidence를 가진 고유 offering 목록은 189개다.
- 조회 coverage 문제였으므로 Raw·Verified KG, 원본 PDF와 `ontology_spec.json`은 변경하지
  않았다. QueryPlan의 `area_ids` 필터 때문에 파생 `llm_query_schema.json`만 생성기로
  갱신했다.

## 5. 실제 평가

- PR #10 50문항: 기대 상태 50/50, 평균/P50/P95
  `12.989/15.776/23.232초`, `ANSWERED` Citation 22/22, SAFE_FAILURE 0.
- 미공개 단일 50문항: 기대 상태 50/50, 평균/P50/P95
  `14.082/13.759/43.265초`, `ANSWERED` Citation 30/30, 공개 오류 0.
- 다중 턴 20개·65턴: 기대 상태 65/65, 평균/P50/P95
  `12.594/13.296/24.348초`, `ANSWERED` Citation 43/43, 공개 오류 0.
- 요청 충족도 변형 단일 30문항: `ANSWERED 18`, `ADVISORY 3`,
  `INSUFFICIENT_EVIDENCE 4`, `NEEDS_USER_INFO 4`, `OUT_OF_SCOPE 1`.
  평균/P50/P95는 `25.983/16.495/131.750초`, 모든 `ANSWERED`에 Citation이 있다.
- 변형 다중 3개·8턴: 평균/P50/P95 `28.830/30.511/48.271초`, 모든 `ANSWERED`에
  Citation이 있다.
- 프로필 갱신으로 중단된 턴, CSE 범위 질문의 OUT_OF_SCOPE, 조회 가능한 목록의
  INSUFFICIENT_EVIDENCE, SAFE_FAILURE, 공개 오류는 모두 0건이다.
- 전체 응답 전문과 항목별 판정은
  [요청 충족도 평가](../../evaluations/conversational-fulfillment-v1.md)에 기록했다.

## 6. 브라우저 검증

- 실제 Chromium에서 자료구조→필수 여부→대체 인정→개인 잔여요건 4턴을 같은
  conversation으로 전송했고 reload 뒤 8개 message가 복원됐다.
- 390px에서 CSE 37과목, Citation 37건, 실제 traversal 113 nodes/112 edges,
  10개 progress/five-W-one-H 상세, 승인 Cypher를 확인했다.
- 프로필 저장·reload 복원·초기화, 새 채팅·기존 채팅 재개·개별 삭제를 실제 UI로 확인했다.
- Enter 전송, Shift+Enter 줄바꿈, IME composition Enter 비전송을 확인했다.
- Citation PDF modal의 원문, 확대 125%, 이전·다음, 닫기를 확인했다.
- 42→45학점 정정 후 합계 84→87, 전공 잔여 36→33, 총 잔여 46→43으로 재계산됐다.
- desktop·390px 모두 문서 가로 overflow와 브라우저 console error가 0건이었다.

## 7. 자동 검증

| 검증 | 결과 |
|---|---|
| `uv sync --locked`, `uv lock --check` | PASS |
| 전체 unittest | 415 passed, 외부 opt-in 6 skipped |
| 전체 pytest | 409 passed, 419 subtests passed, 외부 opt-in 6 skipped |
| Neo4j opt-in | 1 test, 6 subtests passed |
| Ollama answer opt-in | 1 test, 6 subtests passed |
| schema exporter check | PASS |
| Verified migration check | PASS |
| bundle validate/check/verify | PASS, DB counts 동일 |
| `git diff --check` | PASS |
| GitHub Actions `Query safety checks / unit-tests` | PASS |

## 8. 실패한 접근과 보완

- 목록 regex를 처음 넓혔을 때 `다시 그 과목으로 돌아가서 학기를 알려 줘` 같은 단일
  과목 후속 질문까지 목록으로 잡을 위험이 있었다. 전체 범위 marker와 목록 action의
  결합으로 좁혔다.
- course lookup 일부가 성공하면 전체 outcome의 근거 부족까지 ANSWERED로 승격되던
  로직을 항목별 충족도와 원 outcome 보존으로 교정했다.
- 브라우저 검증 첫 시도에서 숨겨진 채팅방 패널의 버튼을 직접 클릭해 timeout이 났다.
  실제 toggle을 연 뒤 조작하도록 수정해 재검증했다.
- 첫 전체 unittest에서 파생 query schema stale 한 건이 실패했다. 생성기를 실행해
  `area_ids` 선언만 동기화한 뒤 전체를 다시 실행했다.

## 9. 제한사항

- 추천은 선수과목·실시간 시간표·잔여석을 추측하지 않는다. 열린 다음 학기 추천은 현재
  학년 등 최소 조건이 필요하다.
- 균형교양 전체 목록은 189개 Citation 때문에 응답 P95가 길다. 정확성·완전성을 위해
  결과를 자르지 않았으며 후속 pagination은 별도 UX 과제다.
- 브라우저 로컬 저장은 기기 간 동기화나 서버 복구를 제공하지 않는다.
- 인증·운영 queue·정식 배포는 이번 범위가 아니다.
