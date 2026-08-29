# 0015. PR #33 마감, 시간 분포 기록, 수동 확인 환경 기동

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-29 |
| 담당자 | 황대겸 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | `docs/hwang-daegyeom/pr33-wrapup` (기반: `feat/hwang-daegyeom/query-traversal-view`) |
| 관련 커밋 | `3740a69` (ADR 0013 갱신) 및 이 브랜치의 커밋 |
| 관련 Issue/PR | PR #33, PR #34 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #33을 리뷰 가능한 최종 상태로 마감하고, 담당자가 직접 브라우저로 확인할 수 있게
서버를 띄우는 것이다. 코드 변경은 없다.

## 2. 요청 내용 요약

시간순으로 정리한다.

1. 시간 분포(총 10,588ms 중 LLM 두 단계가 99.7%)를 ADR 0013에 덧붙여라. 실시간 시각화의
   실질적 제약은 Neo4j가 아니라 LLM 호출 시간이라는 사실이다. 나중에 이 화면을 다시
   만지려는 사람에게 이 숫자가 가장 먼저 필요하다.
2. PR #34에 대해 #33에 코멘트 한 줄만 남겨라. 답은 기다리지 말고 대기해라.
3. 서버를 띄워라. 전제 조건 확인 → `KG_CHAT_SHOW_QUERY_DETAILS=full` 백그라운드 기동 →
   `/api/health` 200 대기 → 접속 주소. WSL이므로 Windows 브라우저에서 열리는 주소인지
   확인. 볼 만한 질문 3개 추천(Alignment 계열 제외). 끄는 명령도. **코드는 고치지 마라.**
4. PR #33을 최종 상태로 정리하고 끝내라. push 확인, 본문 갱신(4·5번 구현 내용 / 하지 않은
   것과 이유 / 리뷰 시 봐 줬으면 하는 곳 3~4개), 리뷰 요청 확인, #32 병합 후 할 일 세
   가지를 본문 맨 아래 체크리스트로.
5. 지금까지 작업한 것을 AI 시뮬레이션 로그로 쓰고 PR로 올려라.

## 3. 작업 전 상태

- PR #33은 `d8fe946`까지 push돼 draft 해제·리뷰 요청된 상태였다.
- `# 최종 상태` 절이 1차 작성분 그대로였다. "미구현 범위"에 이미 끝난 항목 3개가 있었고,
  "브라우저 시각 검증 미실행"은 사실이 아니게 돼 있었다.
- ADR 0013 본문은 제약을 Neo4j 쪽으로만 서술하고 있었다.

## 4. 수행한 작업

### 4.1 ADR 0013에 시간 분포 추가 (`3740a69`)

한 줄이 아니라 **갱신 절**로 붙였다. 본문 결론("가장 큰 제약은 Neo4j")을 일부 뒤집는
내용이라 한 줄로 끼워 넣으면 앞뒤가 맞지 않는다.

| 단계 | 소요 | 비중 |
|---|---|---|
| 질문 분석 (LLM) | 4,060ms | 38.3% |
| Cypher 생성 (LLM) | 6,494ms | 61.3% |
| **LLM 두 단계 합계** | **10,554ms** | **99.7%** |
| 나머지 7단계 합계 | 13ms | 0.1% |

결론 세 가지를 적었다. ① 그래프 질의는 6ms이므로 hop 단위 중계가 가능해져도 사용자가
그것을 보는 시간은 전체의 0.06%다. ② 5~9단계가 같은 프레임 안에서 끝나므로 눈에 보이게
하려면 지연을 넣어야 하고 그것은 원칙과 충돌한다. ③ 다시 시도한다면 대상은 Neo4j가
아니라 LLM 단계여야 하나, 그 경로는 모델 원문 노출과 맞닿아 별도 판단이 필요하다.

단일 표본이라는 사실과 측정 조건을 함께 남겼다.

### 4.2 PR #34 관련 코멘트

지시받은 문장을 그대로 남기고, #33이 단계 구조에 의존하는 자리 네 곳을 함께 적었다:
`PUBLISHED_FIELDS`, `NARROWING_STAGES`, `STAGE_5W1H`, 계약 테스트 허용 목록.
#34는 draft라 열어 보지 않았고, 판단이 제목만 근거라는 것을 코멘트에 명시했다.

### 4.3 수동 확인 환경 기동

전제 조건을 먼저 확인했다.

| 항목 | 확인 결과 |
|---|---|
| Neo4j | `neo4j-db` (2026.06.0) 가동, 7687/7474 |
| 적재 데이터 | 노드 1,518 · 관계 3,260 · Evidence 511 |
| Ollama | `qwen2.5-coder:14b` 적재됨 |
| PDF | 탑재됨 |

`KG_CHAT_SHOW_QUERY_DETAILS=full`로 재기동했다. 직전에 실행 중이던 프로세스는 0014의
`server.py` 변경(`label_names_ko`) 이전에 띄운 것이라 그대로 두면 확인 대상이 달랐다.

WSL 네트워킹 모드는 `nat`이고 `127.0.0.1:8501` 바인딩이다. NAT 모드의 localhost
포워딩으로 Windows 브라우저에서 `localhost:8501`이 열린다. WSL IP(`192.168.98.225`)는
현재 바인딩으로는 닿지 않으므로, 필요할 때 쓸 `CHATBOT_HOST=0.0.0.0` 방법을 함께 안내했다.

추천 질문 3개는 **실제로 실행해 동작을 확인한 것만** 골랐다.

| 질문 | 확인된 동작 | 무엇을 보여 주나 |
|---|---|---|
| 자료구조는 몇 학년 몇 학기에 개설되나? | `ANSWERABLE`, 10단계, 5노드·4간선, 인용 1건 | 되묻기 없이 전체 흐름 |
| 컴퓨터공학과 전공필수 과목은? | 되묻기 → 선택 후 8노드·7간선 | 선택지가 데이터에서 나오는 것과 재조회 |
| 균형교양 이수요건은? | `CLARIFICATION_REQUIRED`, 3단계, 그래프 없음 | 조회가 없으면 `1/9`에서 멈추는 것 |

### 4.4 PR #33 본문 최종 갱신

`# 최종 상태` 절을 통째로 다시 썼다. 낡은 서술을 남기면 리뷰어가 이미 해결된 항목을
확인하게 된다.

- work.txt 4번: 표기 출처 표(`name_ko` 26/26, 31/31, `description_ko` 147/147)
- work.txt 5번: 두 그래프의 구분, 탐색 경로·단계별 성장·9단계 상세·내보내기
- 하지 않은 것: hop prefix 5종이 전부 막히는 실측 표와 ADR 0013 링크
- 리뷰 시 봐 줬으면 하는 곳 4개
- #32 병합 후 할 일 3개를 본문 맨 아래 체크리스트로

리뷰 요청은 `08-28 16:09`에 있었고 draft 해제가 `08-28 23:07`이라 알림은 갔을 것으로
보이나, 그 이후 커밋 5개가 더 들어갔고 push는 새 알림을 만들지 않는다. 요청을 지웠다
다시 넣어 새로 보냈다(`08-28 23:56`).

### 4.5 `.gitignore` 불일치 확인

`CLAUDE.md`가 `git status`에 `??`로 계속 남는 것을 확인했다.

```text
.gitignore 10행 = AGENTS.md   ← CLAUDE.md 아님
git check-ignore CLAUDE.md    → 매칭 규칙 없음
git ls-files CLAUDE.md        → 추적되지 않음
```

`CLAUDE.md` 스스로 "`.gitignore` 10행으로 Git 추적에서 제외돼 있어 로컬 전용"이라고
적고 있으나 10행은 `AGENTS.md`다. 파일명이 바뀌면서 `.gitignore`가 따라가지 않은 것으로
보인다. 현재는 추적되지도 무시되지도 않아, `git add .`를 하면 팀 저장소에 올라간다.
문서가 주장하는 상태를 실제로 만들기 위해 한 줄을 추가했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `docs/ai-simulation-logs/hwang-daegyeom/0013-no-realtime-hop-traversal.md` | 수정 | 갱신 절: 단계별 시간 분포 (커밋 `3740a69`, 기반 브랜치) |
| `docs/ai-simulation-logs/hwang-daegyeom/0015-pr33-wrapup-and-manual-verification.md` | 신규 | 이 로그 |
| `docs/ai-simulation-logs/hwang-daegyeom/README.md` | 수정 | 로그 목록과 다음 번호 |
| `.gitignore` | 수정 | `CLAUDE.md` 한 줄 |

애플리케이션 코드 변경은 **없다.**

## 6. 주요 결정과 이유

- **시간 분포를 한 줄이 아니라 갱신 절로 붙였다.** 본문 결론을 일부 뒤집는 내용이라
  한 줄로는 앞뒤가 맞지 않는다. 수칙은 기존 로그의 소급 수정을 금하므로 하단에 날짜와
  함께 덧붙였다.
- **추천 질문을 실행해 보고 골랐다.** 동작을 확인하지 않고 추천하면 담당자가 확인 중에
  예상과 다른 화면을 보게 된다. 확인한 것만 적었다.
- **리뷰 요청을 다시 보냈다.** 알림이 갔는지는 API로 확인할 수 없다. ready 전환 이후
  커밋 5개가 더 들어갔고 push는 알림을 만들지 않으므로, 확실한 쪽을 택했다.
- **`.gitignore` 수정을 이 PR에 넣었다.** 한 줄이고 되돌리기 쉽다. 다만 담당자가 명시적
  으로 지시한 항목이 아니므로 별도 커밋으로 분리해 떼어낼 수 있게 했다. 이슈로 올리는
  방법도 있으나(수칙 6.4절), 논의할 결정 사항이 없고 고칠 내용이 자명하다.
- **기반 브랜치를 `main`이 아니라 `feat/hwang-daegyeom/query-traversal-view`로 잡았다.**
  로그 0011~0014가 아직 `main`에 없어 `main` 기준으로 0015를 만들면 README의 번호가
  맞지 않고 충돌한다. #33 병합 후 이 PR을 병합한다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 전제 조건 | `docker ps`, `curl /api/tags`, `check-connection` | Neo4j 가동, 1518/3260/511, `qwen2.5-coder:14b` |
| 서버 기동 | `curl /api/health` | 200, `service_ready: true`, `detail_level: full` |
| 바인딩 | `ss -ltnp` | `127.0.0.1:8501` |
| WSL 모드 | `wslinfo --networking-mode` | `nat` |
| 추천 질문 3개 | `/api/ask` SSE 직접 호출 | 4.3절 표 |
| push 상태 | `git log origin/..HEAD` | 남은 커밋 없음, 14 커밋 |
| 리뷰 요청 | `issues/33/timeline` | `08-28 23:56 review_requested → jeongiryang` |
| `.gitignore` | `git check-ignore -v CLAUDE.md` | 수정 전 매칭 없음 |

`pytest`·`validate`는 이 브랜치에서 **미실행**이다. 문서와 `.gitignore`만 바뀌어 코드
경로에 영향이 없다. 직전 커밋(`3740a69`) 기준 결과는 0014 로그 7절에 있다.

## 8. 발견된 문제와 위험

- `CLAUDE.md`가 무시되지 않아 `git add .`로 팀 저장소에 올라갈 수 있었다. 이 PR에서
  고쳤으나, 같은 종류의 불일치가 다른 문서에도 있는지는 확인하지 않았다.
- 서버는 담당자 확인이 끝날 때까지 띄워 둔 상태다. 확인 후 내려야 한다.
- 단계 시간 측정은 단일 표본이다. 비율의 크기는 표본을 늘려도 뒤집히지 않을 것으로 보나
  확인하지 않았다.

## 9. 남은 작업

- 담당자의 브라우저 확인 결과 반영.
- PR #33 리뷰 지적 대응.
- #32 병합 후: `PERSONAL_FIELDS` 채우기 / `docs/evidence-chat.md`는 `58bec75`만 따로
  다루기 / 브라우저 검증 재실행. PR #33 본문 맨 아래 체크리스트에 있다.

## 10. 다음 작업 제안

- PR #34가 `ProgressPhase` 9단계 구조를 바꾸는지에 대한 답을 받으면 병합 순서를 확정한다.
- ADR 0013 10절의 검증 경계 논의는 팀 결정 안건으로 남아 있다.

## 11. 통합 완료 후 갱신 (2026-08-29)

이 절은 위의 PR #33 병합 전 기록을 보존하면서, PR #32~#35 통합 뒤의 실제 최종 상태를
기록한다. 위 4.3절의 `1,518 / 3,260 / 511`과 단일 질문 화면 검증은 당시 기준의 역사적
측정값이며 최종 기준값이 아니다.

### 11.1 병합 순서와 충돌 해결

1. PR #32가 merge commit `86259331a0ff97a53309c06d307ef9fe06c73779`로 병합됐다.
2. PR #34가 merge commit `cb6fc297d463690e402c061489e7b20703e9b064`로 병합됐다.
3. PR #33 브랜치에 최신 `main`을 일반 merge 방식으로 반영하고, 연속 채팅을 기준으로
   충돌 파일 10개를 통합했다.
4. PR #33은 통합 commit `4eca08f5b5de2f249db9625cd5a4da28ac59794c`를 거쳐 merge
   commit `46fb8e45dedff5c4c02c223fb5d2767f22e0965c`로 병합됐다.

PR #34의 상시 입력창, conversation/turn 저장, assistant turn별
`presentation_snapshot`을 유지했다. PR #33의 한국어 schema display mapping,
approved canonical Cypher의 PROFILE 관찰값, 실제 반환 record와 VERIFIED provenance에서
만든 traversal, 색상·순서 재생, 처리 과정 상세와 Cypher 표시는 각 assistant turn 아래로
포팅했다. 이전 단일 질문 wizard와 `새 질문하기` 전환은 복원하지 않았다.

### 11.2 최종 표시·보안 계약

- 각 assistant turn은 `근거 N개`, `처리 과정`, `그래프 탐색`, `Cypher 보기`를 독립적으로
  보존한다. 후속 질문이 이전 turn의 Citation·진행 단계·graph·Cypher를 덮어쓰지 않는다.
- 내부 Label·Relationship·속성 식별자는 영어로 유지하고 한국어 이름은 presentation에서만
  사용한다. 실제 Cypher 원문은 승인된 영어 스키마를 그대로 표시한다.
- traversal은 동일 후보의 comment-free canonicalization, 정적 검증과 EXPLAIN을 통과한
  읽기 질의의 PROFILE operator와 실제 승인 결과에서만 만든다. 승인 path가 없는 집계
  질의에 ontology endpoint를 조합한 가짜 간선을 만들지 않는다.
- 실패·폐기·재시도 이전 Cypher와 질문 원문 trace, prompt, 모델 원문, 자격증명, URI,
  로컬 경로, traceback은 presentation에 포함하지 않는다.
- 애니메이션은 승인된 `traversal_order`의 사후 재생이다. Neo4j가 실행 중 hop 이벤트를
  보낸다고 표현하지 않으며, reduced-motion에서는 이동 효과 없이 최종 상태를 즉시 표시한다.
- 처리 과정의 육하원칙은 실제 stage payload의 allowlist 값과 고정된 안전 설명으로
  구성하고 hidden chain-of-thought를 노출하지 않는다.

### 11.3 최종 검증 결과

| 검증 | 최종 결과 |
|---|---|
| 의존성 | `uv sync --locked`, `uv lock --check` 통과 |
| 전체 unittest | 386건 통과, 외부 서비스 선택 테스트 6건 skip |
| 전체 pytest | 380건 통과, 399 subtests 통과, 외부 서비스 선택 테스트 6건 skip |
| 스키마·마이그레이션·bundle | schema exporter, personalization migration check, Verified bundle validate 통과 |
| Neo4j | connection/verify와 opt-in 통합 통과, `1,536 / 3,287 / 520` 유지 |
| 실제 6문항 | Ollama·Neo4j·Starlette SSE 통과, 전공필수 9과목·21학점과 Citation 9건 확인 |
| 데스크톱 Chromium | 10단계·10 disclosure·10개 육하원칙, traversal 29 node/28 edge, PROFILE operator 13개, 콘솔 오류 0건 |
| 390px reduced-motion | traversal 5 node/4 edge, 한국어 표시, 가로 overflow·콘솔 오류 0건 |
| 연속 4턴 | 대명사 연결, 대체 근거 부족, 최소 사용자 정보 요청, turn별 snapshot과 reload 복원 확인 |
| 입력·PDF | Shift+Enter, IME Enter 보호, 채팅 생성·전환·삭제, PDF modal·zoom·이전·다음·닫기 통과 |

4턴 검증에서 `고급자료구조로 대신하면 인정돼?`가 무관한 졸업학점 규칙으로 복구되는
문제를 발견했다. 질문별 예외 대신 중앙 과목 조사 정규화, 대체 인정 의미 보존, 결합 뒤
원질문 Evidence 경계 재검증을 추가했다. 최종 결과는 확인된 과목 identity와 직접 대체
근거 부재를 분리한 `INSUFFICIENT_EVIDENCE`이며, 점수나 대체 규정을 추측하지 않는다.

### 11.4 최종 제한사항

- PROFILE operator는 승인된 질의의 실행계획 관찰값이지 Neo4j 내부 hop 실시간 스트림이
  아니다.
- 집계 질의가 path를 반환하지 않으면 그래프 영역은 빈 경로 상태를 표시한다.
- 로컬 PoC의 LLM·Neo4j·PDF와 브라우저 저장소에 의존하며 인증·서버 영구 채팅 저장은
  제공하지 않는다.
- 외부 서비스 opt-in 테스트는 해당 로컬 서비스가 준비된 환경에서만 재현할 수 있다.
