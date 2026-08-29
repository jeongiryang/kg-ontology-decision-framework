# PR #32~#35 연속 채팅 통합

## 1. 작업 목적

PR #32의 개인화·응답 상태·Verified KG 보완, PR #34의 Agent·연속 채팅, PR #33의
한국어 표시·승인 질의 traversal·처리 과정·Cypher 기능을 하나의 최신 연속 채팅 구조로
통합하고 PR #35 문서를 실제 최종 구현에 맞춘다.

## 2. 조사와 의존관계

- PR #32는 PR #34의 선행 커밋이며, PR #33은 PR #35의 선행 커밋이었다.
- PR #33과 #34는 `server.py`, 정적 UI 3개 파일, query validation과 chat 테스트 등
  10개 파일이 겹쳤다.
- PR #33의 화면은 이전 전역 결과 구조를 사용하고, PR #34는 IndexedDB의 assistant-turn
  `presentation_snapshot`으로 Citation·progress·inspection을 보존한다.
- 내부 KG 스키마는 영어를 유지하고 ontology 명세의 한국어 이름은 presentation에서만
  사용해야 한다.

## 3. 통합 결정

- PR #32와 #34를 먼저 검증·병합한 뒤 PR #33에 최신 `main`을 merge commit으로
  반영했다. rebase와 force push는 사용하지 않았다.
- PR #34의 상시 composer, 연속 메시지, turn snapshot, 프로필·대화 저장을 기준 UI로
  유지했다.
- PR #33의 `PathStep`, PROFILE 관찰값, 한국어 schema catalog, request-local opaque
  projection과 traversal 순서는 보존했다.
- 실패·폐기 Cypher 공개, 상세 모드 기본 `full`, 승인 경로가 없을 때 ontology endpoint로
  간선을 추정하는 동작은 보안·정확성 계약에 맞지 않아 제거했다.
- 각 assistant turn 아래에 `근거 N개`, `처리 과정`, `그래프 탐색`, `Cypher 보기`를
  독립 disclosure로 연결했다. 새 turn은 이전 turn의 snapshot을 변경하지 않는다.
- traversal 재생은 승인된 `traversal_order`만 사용하며 실행 중 Neo4j 이벤트처럼
  표현하지 않는다. reduced-motion에서는 최종 상태를 즉시 표시한다.

## 4. 보안 경계

- Cypher는 동일 후보의 canonicalization·정적 검증·EXPLAIN 성공 뒤에만 공개한다.
- ResultValidator와 ClaimValidator가 승인한 VERIFIED Fact–Evidence 직접 연결만 결과
  graph에 포함한다.
- 승인 hop이 없으면 간선을 추정하지 않는다.
- raw Fact/Evidence ID는 요청별 HMAC opaque ID로 바꾸고 ChatResponse 8필드는 유지한다.
- PROFILE detail의 비밀번호·토큰·URI·로컬 경로 marker는 공개 전 제거한다.
- `KG_CHAT_SHOW_QUERY_DETAILS` 기본은 `off`; `full`에서만 Cypher와 graph를 전송한다.

## 5. 검증

- `uv sync --locked`, `uv lock --check`: 통과
- 전체 unittest: 386건 통과, 외부 서비스 선택 테스트 6건 skip
- 전체 pytest: 380건 통과, 399 subtests 통과, 외부 서비스 선택 테스트 6건 skip
- schema exporter, personalization migration check, Verified bundle validate: 통과
- 로컬 Neo4j connection/verify 및 opt-in query integration: 통과
- 로컬 Ollama·Neo4j·Starlette opt-in 6문항: 통과. 전공필수 9과목·21학점과 Citation
  9건을 포함했고 데이터 개수는 1,536/3,287/520으로 유지됐다.
- 실제 Chromium 데스크톱: 전공필수 질문에서 10단계, 10 disclosure, 10개 육하원칙,
  traversal 29 node/28 edge, PROFILE operator 13개, 콘솔 오류 0건을 확인했다.
- 실제 Chromium 390px reduced-motion: 자료구조 질문에서 한국어 label/relation,
  traversal 5 node/4 edge, 가로 overflow 없음, 콘솔 오류 0건을 확인했다.
- 실제 4턴 대화: 같은 conversation에서 자료구조 대명사 연결, 대체 인정 근거 부족,
  개인 이수정보 최소 요청을 확인했고 8개 메시지가 reload 뒤 복원됐다. 앞의 3개
  grounded turn은 각자 Citation·처리 과정·graph·Cypher snapshot을 유지했다.
- 브라우저 입력·관리: Shift+Enter 줄바꿈, IME 조합 Enter 무전송, 새 채팅 생성·전환·
  삭제, PDF modal 열기·확대·이전·다음·닫기, 모바일 overflow 없음, 콘솔 오류 0건을
  확인했다.

통합 브라우저에서 `고급자료구조로 대신하면 인정돼?`가 처음에는 일반 졸업학점 규칙으로
복구되는 의미 오류를 발견했다. 중앙 과목 resolver에 조사 `-로`를 추가하고, 대체 인정
질문의 후속 조회가 대체·인정 의미를 버리지 못하게 제한했으며, 결합 뒤 원질문 Evidence
경계를 다시 검사했다. 수정 후에는 확인된 두 과목 identity와 직접 대체 근거 부재를
구분해 `INSUFFICIENT_EVIDENCE`로 표시했다.

## 6. 남은 작업

- PR #33의 최신 Actions와 독립 리뷰를 확인한 뒤 정상 merge로 병합했다. 최종 merge
  commit은 `46fb8e45dedff5c4c02c223fb5d2767f22e0965c`이다.
- PR #35에는 기존 PR #33 마감 기록을 역사적 상태로 보존하고, 최종 turn별 구조,
  PR #32·#34·#33 병합 순서, 실제 브라우저·외부 서비스 검증과 현재 제한사항을 갱신했다.
- PR #35 병합 뒤 최신 `main`의 ancestry, 보호 파일, 데이터 개수와 checks를 최종
  재확인한다.
