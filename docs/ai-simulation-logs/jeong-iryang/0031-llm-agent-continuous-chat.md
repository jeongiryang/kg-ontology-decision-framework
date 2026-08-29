# LLM 중심 Agent와 연속 채팅 UI 완성

## 1. 작업 목적

PR #34의 기존 도구 호출 계층을 단순 조회 상한 확장이 아닌 결과 기반 Agent loop로
완성하고, 단일 질문 3단계 화면을 같은 채팅방에서 후속 질문을 계속 보낼 수 있는 연속
채팅 화면으로 교체했다. 추가 DSW 접속과 모델 변경은 이번 범위에서 제외했고 로컬
`qwen2.5-coder:14b`를 유지했다.

## 2. 시작 상태와 조사

- PR #32는 `main` 대상 Ready/MERGEABLE이지만 `REVIEW_REQUIRED`여서 병합하지 않았다.
- PR #34는 PR #32 브랜치를 base로 둔 Draft stacked PR이다.
- 팀원 PR #33은 `server.py`, 정적 UI, query safety와 graph projection을 함께 수정하므로
  PR #34와 `app.js`, `app.css`, `index.html`, query validation 계층에서 충돌 가능성이 있다.
  PR #33을 수정하거나 코드를 복사하지 않았다.
- 기존 UI는 다중 턴 DTO와 IndexedDB를 일부 갖고도 전역 단일 결과 상태, 3단계 화면과
  `새 질문하기` 동작을 유지해 후속 질문마다 대화 흐름을 끊었다.
- 기존 `expanded` 모드는 조회 상한만 늘리고 결과 충분성에 따른 중단·재계획과 packet별
  복구가 없어 정확도가 낮아졌다.

## 3. 핵심 설계

### 결과 기반 Agent

- 기본 `agentic` 정책은 계획 도구 6회, KG 조회 6회, 독립 하위 질문 3개, 결과 평가
  3회, 한 턴 180초, narrative repair 1회로 제한한다.
- LLM은 대화 문맥·과목 참조·하위 질문·도구·다음 조회·근거 충분성·FactPacket 표현을
  제안한다.
- Python은 프로필 타입, 학점 계산, 동일 호출 방지, 시간·조회 예산, course identity,
  canonical Cypher, schema/parameter/limit, 정적 검증, EXPLAIN, Evidence, Claim 의미와
  Citation 대응을 계속 강제한다.
- 승인 결과가 충분하면 즉시 멈추고, 추가 질문은 planner가 만든 pending 질문 또는 같은
  주제로 검증된 좁은 질문만 허용한다. 모든 조회는 기존
  `PersonalizedCurriculumChatService`와 SafetyPipeline으로 재진입한다.
- 개인 잔여요건 질문은 LLM이 일반 추천 질문으로 축소하기 전에 원래 사용자 turn에서
  최소 프로필 슬롯을 검증한다. 이 gate는 학사 사실이나 답을 생성하지 않는다.

### FactPacket 전체 답변

- 최대 4개의 sealed answerable source에서 공개 가능한 Claim 필드와 canonical 문장을
  packet으로 만든다.
- LLM은 packet별 전체 문장을 구성하지만 subject, 과목코드, 수치·단위 역할, 학년·학기,
  enum/operator/polarity와 인정·대체·추천 표현을 Python이 다시 비교한다.
- 실패 packet만 한 번 재작성하고 실패한 부분만 canonical 문장으로 남긴다.
- sealed `ChatResponse` 8필드는 변경하지 않고 표시 문장은 별도
  `conversation_update version=1`에 둔다.

### provider 호환성

실제 Ollama 0.32.5는 JSON grammar의 `minLength`와 `maxLength`를 HTTP 400으로
거부했다. Ollama에 전달하는 grammar projection에서 두 keyword만 제거하고, 응답을 받은
후 애플리케이션의 원래 typed schema와 길이 검증을 그대로 적용했다. 이 문제로 Agent
plan과 FactPacket이 조용히 fallback되던 원인을 제거했다.

### 연속 채팅 UI

- 질문·진행·결과 3화면과 `새 질문하기`를 제거했다.
- 채팅방 목록, turn별 메시지 목록, 항상 보이는 하단 composer를 하나의 화면으로
  구성했다.
- Enter 전송, Shift+Enter 줄바꿈, 한글 IME 조합 Enter 방지, 응답 중 중복 제출 방지,
  완료 후 focus 복원을 적용했다.
- 각 assistant message가 Citation/PDF, progress, inspection, 승인 Cypher, agent trace를
  독립 snapshot으로 보존한다.
- IndexedDB schema version 2에 채팅방·메시지를 저장하고 version 1 row를 안전하게
  읽는다. 프로필은 기존 versioned localStorage를 유지한다.
- 새 채팅, 최근순 전환, 개별/전체 삭제, 스마트 자동 스크롤, 최신 메시지 버튼과
  390px 반응형 레이아웃을 구현했다.

## 4. 일반화 보완

- 과목 대체 후속 질문은 현재 명시 과목과 직전 승인 course identity를 함께 조회하고,
  직접 대체 근거가 없으면 `INSUFFICIENT_EVIDENCE`로 남긴다.
- `내 이수내역`을 학사 도메인과 개인 이력 표현으로 일반 인식한다.
- 개인 영역별 학점과 Verified 기준이 있으면 첫 질문과 정정 후 재질문 모두 Python 계산을
  FactPacket canonical message에 반영한다.
- 개인 이수 과목을 바탕으로 무엇을 더 들어야 하는 질문은 학점·영역 정보가 없을 때
  일반 권장과목으로 우회하지 않고 최소 사용자 정보를 묻는다.
- PR #10 질문 원문 50개는 production exact match가 0건이며 평가 fixture와 보고서는
  런타임에서 참조하지 않는다.

## 5. 실제 평가 결과

### PR #10 50문항

독립 conversation으로 공개 `/api/ask` SSE를 모두 실행했다.

- 기대 상태 일치 50/50
- `ANSWERED` 22, `NEEDS_USER_INFO` 5, `INSUFFICIENT_EVIDENCE` 16,
  `OUT_OF_SCOPE` 1, `ADVISORY` 6
- `ANSWERED` 22/22 Citation 보유
- 공개 오류와 `SAFE_FAILURE` 0

### 미공개·다중 턴

- 미공개 단일 50개: `ANSWERED` 30, `INSUFFICIENT_EVIDENCE` 9,
  `NEEDS_USER_INFO` 3, `OUT_OF_SCOPE` 5, `ADVISORY` 3
- 다중 턴 20개·65턴: `ANSWERED` 43, `INSUFFICIENT_EVIDENCE` 7,
  `NEEDS_USER_INFO` 1, `OUT_OF_SCOPE` 1, `ADVISORY` 13
- 전체 `ANSWERED` 73/73 Citation 보유, 공개 오류 0
- FactPacket 96개, 검증된 LLM 재작성 section 31개, canonical fallback 0개,
  부분 repair 42회
- 평균/중앙값/P95: 단일 15.848/14.841/34.719초, 다중 턴
  13.892/13.910/31.091초

마지막 일반화 변경 뒤 직접 영향이 있는 S23, S26, M03, M14만 동일 evaluator로 다시
실행해 전체 결과에 합쳤다. 실행하지 않은 문항을 새로 실행했다고 기록하지 않는다.

## 6. 실제 브라우저 검증

Windows Chrome headless를 실제 Starlette 8502 서버에 연결했다.

- 자료구조 4턴: `ANSWERED → ANSWERED → INSUFFICIENT_EVIDENCE → NEEDS_USER_INFO`
- 프로필·정정 4턴: `ADVISORY → ANSWERED → ADVISORY → ANSWERED`
- 전공 42→45 정정 뒤 부족 전공학점 36→33, 총 잔여학점 58→55 재계산
- 같은 conversation 8메시지와 turn별 presentation snapshot 유지
- PDF modal 열기와 닫은 뒤 원래 버튼 focus 복귀
- IME 조합 Enter 오발송 없음, 채팅방 전환·삭제 정상
- 새로고침 및 Chrome 프로세스 종료·재시작 뒤 IndexedDB 대화 복원
- 390px에서 document width 390px, composer/transcript 표시, 콘솔 오류 0

검증용 Chrome·Starlette·PowerShell 프로세스는 작업 뒤 종료했다. 저장소 밖 임시
브라우저 프로필과 스크린샷은 Git에 포함하지 않았다.

## 7. 검증

| 검증 | 실제 결과 |
|---|---|
| `python -m unittest discover -s tests -v` | 374 PASS, 외부 통합 6 skip |
| `pytest -q` | 368 PASS, 6 skip, 374 subtests PASS |
| Neo4j opt-in query/dynamic | 3 PASS, 6 subtests PASS |
| Ollama/Neo4j/Starlette opt-in | 3 PASS, 18 subtests PASS, 179.03초 |
| schema exporter | PASS |
| Verified migration `--check` | PASS |
| `uv sync --locked`, `uv lock --check` | PASS |
| Markdown 상대 링크 | PASS |
| `git diff --check` | PASS |
| 보호 파일·민감정보·runtime eval 참조 | 변경/노출 0 |
| Neo4j verify | 1,536 nodes / 3,287 relationships / 520 Evidence |

## 8. 변경 범위와 호환성

Agent·provider·planner·검증·개인화·정적 chat UI·테스트·평가·문서를 변경했다. Raw/Verified
KG, ontology, `.env`, PDF, 모델 파일은 변경하지 않았다. ChatResponse 8필드, 기존
progress/inspection/clarification/profile/outcome, Citation/PDF와 팀원 trace envelope는
유지했다.

PR #33은 최신 Head에서도 정적 UI와 query safety 파일이 겹친다. 권장 통합 순서는 PR
#32 반영 후 PR #34를 최신 base에 맞추고, 그 다음 PR #33의 한국어 graph/traversal
presentation을 연속 채팅의 turn별 snapshot과 DOM 확장 지점에 포팅하는 것이다.

## 9. 실패한 접근과 제한

- Ollama grammar 400을 모델 품질 문제로 오인할 수 있었으나 직접 HTTP 응답과 provider
  테스트로 schema keyword 호환성 문제임을 확인했다.
- 첫 Windows PowerShell 5 브라우저 스크립트는 UTF-8 한글 here-string을 손상시켰다.
  별도 UTF-8 JavaScript 파일을 읽는 방식으로 다시 실행했으며 앱 오류로 기록하지 않았다.
- 전체 성적표, 성적·재수강, 실시간 시간표·잔여석 근거는 현재 KG에 없다.
- 브라우저 저장은 로그인 없는 로컬 저장이므로 기기 간 동기화되지 않는다.
- PR #32의 독립 승인과 병합이 완료되지 않아 PR #34는 stacked Draft 상태를 유지한다.
- 이번 작업에서는 DSW 접속, 모델 비교·다운로드·변경을 수행하지 않았다.
