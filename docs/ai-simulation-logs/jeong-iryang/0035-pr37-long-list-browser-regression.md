# 0035. PR #37 긴 목록·정정·브라우저 회귀 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-31 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/conversational-request-fulfillment` |
| 기준 commit | `e043055226ccf08e4adfaef861f4d464e1d89354` |
| 관련 PR | Draft PR #37 |
| 작업 상태 | 후속 회귀 보완·검증 |

## 1. 작업 목적

실사용 화면에서 발견된 균형교양 전체 목록의 180초 경계 실패, 한 메시지 안의 명시적
학점 정정 충돌, 추천 질문 UI와 좁은 desktop 폭을 일반적인 목록·정정·반응형 계약으로
수정한다. 전체 목록을 자르거나 client timeout을 늘리지 않고, 기존 Citation·승인 Cypher·
실제 traversal·연속 채팅 계약을 유지한다.

## 2. 재현과 근본 원인

- 균형교양 parent area 요청을 네 자식 영역의 독립 질문으로 바꿔 planner부터 답변까지
  네 번 실행했다. 189개의 row-shaped FactPacket과 Citation이 LLM·검증·SSE를 반복 통과해
  실제 179.762초, raw SSE 약 599KB가 걸렸고 browser 180초 경계에서 실패할 수 있었다.
- ProfileExtractor는 동일 credit category의 여러 값을 위치와 정정 cue 없이 `set`으로
  모았다. 같은 메시지 뒤 문장의 45학점이 42학점의 명시적 정정인지 알 수 없어 잘못된
  clarification을 만들었다.
- 앱 셸은 최대 1,040px이고 추천 질문이 별도 DOM·focus·mobile overflow 영역을 차지했다.
  189과목·Citation·그래프를 한 열의 좁은 카드에 쌓아 desktop 공간을 활용하지 못했다.

## 3. 구현

### 긴 목록

- `COURSE_LIST`만 최대 250행을 허용하는 공용 limit 정책을 generator, static validator,
  ResultValidator와 SafetyPipeline에 동일하게 연결했다. 그 밖의 plan은 기존 100행을
  유지한다.
- parent `area_ids` 한 번의 QueryPlan으로 영역 이름, stable Course identity와 직접
  Evidence를 조회한다. 전체 목록 plan은 정확한 bounded limit을 요구해 조용한 잘림을
  막는다.
- Claim에 검증된 `area_name`을 전달하고 Python renderer가 Course identity 중복 제거,
  영역별 그룹과 전체 목록을 결정론적으로 만든다.
- 20개가 넘는 Course list는 LLM에 원시 과목·Citation을 다시 보내지 않는다. 영역 수와
  고유 Course 수만으로 optional discourse를 생성하고 검증된 전체 목록은 그대로 보존한다.
- traversal envelope는 실제 승인 결과 572 nodes / 756 edges를 보존한다. 브라우저는
  100개를 넘으면 실제 영역 요약을 먼저 표시하고 사용자가 요청할 때만 전체 SVG를 만든다.
  Evidence button도 disclosure를 열 때만 생성한다.

### 정정

- credit observation의 위치를 보존하고 일반적인 정정 cue와 뒤의 최신 값을 연결한다.
- category가 생략된 replacement는 cue 앞의 가장 가까운 category가 하나일 때만 적용한다.
- 정정 cue가 없는 서로 다른 값은 기존대로 `conflicting_fields`에 남겨 clarification한다.
- 적용된 최신 profile에서 영역별 합계·전공 잔여·총 잔여를 Python이 전부 다시 계산한다.

### UI

- 추천 질문의 server payload, HTML, JavaScript listener/state와 CSS를 삭제했다.
- 앱 셸을 최대 1,440px로 넓히고 일반 text는 78ch, assistant/user card는 75%/62%, 긴
  답변은 100% 폭을 사용한다.
- transcript를 주 스크롤로 유지하고 composer와 최신 메시지 버튼이 답변을 덮지 않게
  grid 위치와 표시 임계값을 조정했다.
- 반복되는 assistant `답변` 표시는 시각적으로 제거하되 screen reader label은 유지했다.

## 4. 데이터 대조

Verified bundle과 Neo4j의 결과가 일치했다.

| 영역 | 원시 Offering | 원시 Course | 직접 VERIFIED Offering/Course/Evidence |
|---|---:|---:|---:|
| 디지털커뮤니케이션 | 27 | 26 | 25 / 25 / 25 |
| 사회와문화 | 68 | 68 | 66 / 66 / 66 |
| 인문예술 | 58 | 58 | 58 / 58 / 58 |
| 자연·과학·기술의이해 | 42 | 42 | 40 / 40 / 40 |
| 합계 | 195 | 194 | 189 / 189 / 189 |

조회 coverage와 presentation 문제였으므로 Raw·Verified KG, 원본 PDF,
`ontology_spec.json`은 변경하지 않았다. 파생 `llm_query_schema.json`만 100/250행 정책과
동기화했다. Neo4j는 작업 전후 `1,536 / 3,287 / 520 Evidence`를 유지한다.

## 5. 실제 결과

- 균형교양 전체 목록: 한 query, 4영역, 고유 Course 189, Citation 189,
  `ANSWERED/COMPLETE`, 누락·중복·SAFE_FAILURE 0.
- 수정 후 기록한 반복 표본 전체 시간은 17.277, 16.479, 16.865, 16.915, 17.984초로
  P50/P95 `16.915/17.984초`였다. 최신 표본의 첫 SSE는 2.140초, KG query 10.428초,
  bounded LLM discourse 3.078초, 전체 raw SSE는 약 785KB였다.
- 최종 Chromium 표본은 17.425초에 189개 bullet을 표시했다. Evidence 189건의 lazy DOM은
  약 106ms, 전체 traversal 명시 렌더는 약 329ms였다. PDF modal, 처리 과정 disclosure,
  canonical Cypher 895자, reload 복원, Shift+Enter와 IME 보호를 확인했고 SSE에는 실제
  완료 progress 10단계가 있었다.
- 한 메시지 정정은 교양 30·전공 45·일반선택 12를 사용해 합계 87, 교양 잔여 4,
  전공 잔여 33, 총 잔여 43으로 다시 계산했다. cue 없는 `전공 42 / 전공 45`는 계속
  최소 clarification을 반환했다.

스크린샷과 viewport별 수치는
[PR #37 긴 목록·브라우저 회귀](../../evaluations/pr37-long-list-browser-regression.md)에
기록했다.

## 6. 검증

| 검증 | 실제 결과 |
|---|---|
| `uv sync --locked`, `uv lock --check` | PASS |
| 전체 unittest | 422 passed, 외부 opt-in 6 skipped |
| 전체 pytest | 416 passed, 423 subtests passed, 외부 opt-in 6 skipped |
| Neo4j opt-in | 3 passed, 6 subtests passed |
| Ollama·Neo4j·Starlette opt-in | 3 passed, 18 subtests passed |
| schema exporter | PASS |
| Verified migration | PASS |
| bundle validate/check/verify | PASS, DB counts 동일 |
| `git diff --check` | PASS |

- PR #10 원본 50문항은 최신 Head 서버의 독립 SSE 요청으로 처음부터 다시 실행했다.
  기대 상태 50/50, 평균/P50/P95 `13.572/17.099/24.751초`, `ANSWERED` Citation
  22/22, 공개 오류·SAFE_FAILURE 0이었다.
- 미공개 단일 50문항은 이전 상태 분포와 50/50 일치했고 평균/P50/P95
  `14.644/14.575/49.376초`, `ANSWERED` Citation 30/30이었다. 다중 턴 20개·65턴도
  이전 상태 전이와 65/65 일치했고 `13.783/14.704/29.164초`, Citation 43/43이었다.
  165개 평가 항목(원본 50문항 + 미공개 50문항 + 다중 턴 65턴)의 tool trace는
  351회, KG query 112회, 부분 재작성 38회, canonical fallback
  0회, 공개 오류·SAFE_FAILURE 0이었다.
- 최종 Chromium 시나리오와 GitHub Actions 결과는 commit·push 직전 최신 결과로
  갱신한다. 실행하지 않은 검증은 통과로 기록하지 않는다.

실제 Chromium의 1920px와 390px에서는 추천 DOM·가로 overflow·console error가 모두
0건이었다. HTTP favicon 요청은 기능·console 오류가 아니므로 별도 완료 항목으로
간주하지 않았다. 189과목 turn은 Evidence 189건과 PDF modal, 처리 과정, 승인 Cypher, 실제
traversal 572 nodes / 756 edges를 열었고 reload 뒤 동일 turn이 복원됐다. CSE 전체 목록도
16.080초에 전공선택 28 + 전공필수 9 = 37개 bullet, Citation 37건으로 표시됐다.

## 7. 실패한 접근과 보완

- 단순히 ResultValidator의 limit만 올리면 ordinary query까지 250행으로 넓어질 수 있었다.
  plan selection mode에 따라 generator와 두 검증 단계가 같은 상한을 쓰게 했다.
- 189개 결과를 한 query로 바꾼 첫 시도도 전체 FactPacket을 LLM에 넣어 133.696초가
  걸렸다. 전체 목록은 deterministic body로 유지하고 LLM 입력을 bounded aggregate로
  축소했다.
- 전체 traversal을 답변과 동시에 SVG로 그리면 browser 작업이 커졌다. 승인 envelope는
  보존하되 summary-first disclosure로 렌더 시점만 늦췄다.

## 8. 남은 제한사항

- 전체 목록의 Citation과 traversal envelope 자체는 완전성을 위해 크다. DOM/SVG는
  점진적으로 만들지만 network payload pagination은 별도 versioned 계약이 필요하다.
- 로컬 단일 Ollama 요청의 지연은 GPU 상태에 영향을 받는다. 목록 결과의 정확성을 위해
  일부 행을 생략하거나 질문별 캐시를 추가하지 않았다.
