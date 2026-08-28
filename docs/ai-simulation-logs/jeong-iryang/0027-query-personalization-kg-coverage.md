# 0027. 50문항 질의 정확도·브라우저 개인화·KG 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-28 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/query-personalization-kg-coverage` |
| 관련 커밋 | 이 Draft PR의 구현·데이터·문서 커밋 |
| 관련 Issue/PR | 평가 원본 PR #10, 새 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #10의 평가 질문 50개를 실제 Starlette `/api/ask` SSE 경로로 실행해 과도한
clarification, 상태 혼동과 부분 답변을 일반 규칙으로 개선한다. 로그인·서버 학생 DB 없이
브라우저 프로필과 채팅에서 제공한 학적·이수 정보를 후속 질문에 적용하고, raw 추출에는
있지만 Verified KG에서 확정 조회할 수 없던 영어 면제 시험 임계값을 직접 Evidence와 함께
보완한다.

## 2. 요청 내용 요약

- 평가 질문 원문·문항 번호별 런타임 분기 없이 50문항 기준선과 최종 결과 기록
- `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`,
  `ADVISORY` typed outcome
- versioned localStorage 프로필, 채팅 추출·정정·충돌·초기화
- 사용자 진술과 VERIFIED KG 사실의 provenance 분리
- PDF 추출 JSON, Verified bundle, Neo4j와 Evidence 대조
- 누락된 영어 면제 atomic Rule·Evidence의 additive·멱등 보완
- sealed `ChatResponse` 8필드, SafetyPipeline, progress/inspection과 팀원 시각화 계약 유지

## 3. 작업 전 상태

- 기준 `main`: `738575f` (`docs: refresh README and add local demo deployment guide (#31)`)
- PR #10 Head: `9066b138…`, 질문 파일 `eval/question-set-v1.md`, 구현 의존성은 없는
  평가 원본이다. PR #10은 수정·병합하지 않았다.
- 기준선 50문항: `CLARIFICATION_REQUIRED` 31, `ANSWERABLE` 13,
  `UNRESOLVED` 3, `SAFE_FAILURE` 2, `OUT_OF_SCOPE` 1, 총 501.420초
- raw JSON: 노드 1,115, 관계 1,946, Evidence 364
- Verified/Neo4j: 노드 1,518, 관계 3,260, Evidence 511
- raw의 모든 identity와 관계는 Verified/Neo4j에 있었으나, 영어 면제 Condition 9건에는
  개별 threshold를 확정 조회할 VERIFIED atomic Rule·Evidence가 없었다.

## 4. 수행한 작업

### 질의·답변

- Verified bundle에서 생성한 Course resolver를 planner와 프로필 extractor가 공유한다.
- 같은 길이 한글 한 글자 substitution만 유일 identity일 때 허용해 데이터/데이타 표기를
  처리하고, 일반 명사를 다른 과목으로 확장하는 삽입·삭제 fuzzy match는 거부한다.
- 명시적 과목 field와 Verified Rule semantic term은 결정론적 계획으로 처리하고 나머지는
  기존 LLM planner를 사용한다. 모든 경로는 canonicalization, 정적 검증, EXPLAIN,
  read executor, ResultValidator를 거친다.
- `course_codes` 다중 filter와 공통 교양 CourseOffering 경로를 추가했다.
- Course Claim에 학년·학기·이수구분을 함께 보존하고, numeric requirement와 Rule 원문,
  0학점·과목 수·학점 합계의 의미 역할을 유지했다.
- 검증 Rule 값과 USER_ASSERTION을 Python에서 비교·계산하고, 근거가 없는 적용·대체·신청,
  실시간 수강신청, 성적·재수강 판단은 `INSUFFICIENT_EVIDENCE`로 남겼다.
- 교육과정 학년·학기 기반 조건부 순서만 `ADVISORY`로 제공하고 선수관계는 만들지 않았다.

### 개인화

- immutable `UserProfile version=1`, `ProfileExtractor`, `DecisionOutcome`과
  `PersonalizedCurriculumChatService`를 추가했다.
- 입학/교육과정 연도, 학과, 학년·학기, 입학·전공 유형, stable course, 범주별 학점,
  영어 자격, 진로 목표와 메모를 타입·범위·vocabulary로 검증한다.
- 현재 메시지의 명시 값과 `42가 아니라 45` 정정을 우선하고, 정정 없는 복수 값은
  `NEEDS_USER_INFO`로 처리한다.
- 프론트는 `evidence-chat-profile-v1` localStorage에 저장하고 저장·개별 과목 삭제·전체
  초기화를 제공한다. 손상/미지원 version은 빈 프로필 fallback이다.
- 서버는 프로필을 영구 저장하거나 Neo4j Student로 만들지 않는다. `profile_update`와
  다섯 상태 `outcome`은 별도 versioned SSE envelope로 전달한다.

### KG

- `scripts/complete_english_waiver_rules.py`는 기존 Condition의 값만 복사해 시험 9종의
  atomic Rule 9, Evidence 9, 관계 27을 idempotent하게 추가한다.
- parent 영어 면제 Rule은 직접 atomic Evidence가 생긴 뒤 VERIFIED로 승격했다.
- Evidence는 발췌 1쪽, 원본 33쪽, 인쇄 25쪽의 표 원문을 보존한다.
- `neo4j_ingest sync`는 현재 DB가 새 bundle identity/relationship의 부분집합인지 먼저
  검사하고 MERGE만 수행한다. 삭제·초기화·ingestion credential fallback은 없다.

### 평가 자동화

- PR #10을 Git ref에서 읽어 실제 SSE로 실행하는 evaluator, 개인화 8시나리오 evaluator,
  50문항 Markdown report renderer를 추가했다.
- 런타임 `src/`에서 50개 질문 원문 exact match는 0건이었다. 원문은 평가 report/script
  실행 입력과 테스트 fixture에만 있다.

## 5. 변경된 파일

| 범주 | 주요 경로 | 내용 |
|---|---|---|
| 개인화 | `src/kg_builder/personalization.py`, `answer/personalized_service.py` | 프로필·추출·다섯 outcome |
| planner/query | `llm/planner.py`, `query/course_names.py`, `query_plan.py`, `cypher_generator.py` | 데이터 기반 identity·rule 계획, 다중 course code |
| Claim | `answer/contracts.py`, builder/validator/renderer | Course 상세와 Rule 값 안전 렌더링 |
| 웹 | `src/evidence_chat/server.py`, `static/` | profile/outcome SSE, localStorage UI |
| KG | Verified bundle, migration script, `neo4j_ingest.py` | 영어 면제 9 atomic Rule/Evidence, additive sync |
| 평가 | `scripts/evaluate_*`, `render_*`, `docs/evaluations/` | 실제 before/after 50문항 |
| 테스트 | personalization, planner, answer, web, bundle, integrations | 새 계약·회귀·개수 |
| 문서 | README, 개인화·챗봇·적재·안전 문서 | 상태·프로필·KG 현재값 |

## 6. 주요 결정과 이유

1. 다섯 outcome은 sealed `ChatResponse`를 바꾸지 않고 별도 SSE로 두었다. 팀원의 progress,
   inspection, Citation/PDF UI 소비자와 8필드 계약을 유지하기 위해서다.
2. 사용자 프로필은 브라우저와 request-local Python에만 둔다. 교육과정 KG와 학생별
   자기진술의 신뢰·수명 주기가 다르기 때문이다.
3. course alias는 data-derived 동일 길이 substitution으로 제한했다. 질문셋 단어 allowlist와
   generic-token 오탐을 동시에 피하기 위해서다.
4. 근거가 확인된 사실과 개인 계산만 ANSWERED로 만들었다. 적용 절차나 대체 가능성의 직접
   근거가 없으면 관련 Rule을 보여 주더라도 INSUFFICIENT_EVIDENCE로 구분했다.
5. 기존 DB는 전체 reload하지 않고 subset-checked additive sync를 사용했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 기준선 50문항 | 실제 `/api/ask` SSE, 질문별 빈 상태 | 50건 기록, 501.420초 |
| 최종 50문항 | 실제 `/api/ask` SSE, 질문별 빈 상태 | 기대 taxonomy 50/50, 391.473초 |
| 최종 분포 | 평가 report | ANSWERED 22 / NEEDS 5 / INSUFFICIENT 16 / OUT 1 / ADVISORY 6 |
| ANSWERED grounding | 최종 JSON 대조 | 22/22 Citation 보유, 계약 밖 상태 0 |
| 개인화 8시나리오 | 실제 `/api/ask` SSE | 8/8 기대 동작 |
| bundle | `neo4j_ingest validate` | 1,536 / 3,287 / 520 PASS |
| Neo4j verify | `check-connection`, `verify` | 1,536 / 3,287 / 520, 대표 사실 PASS |
| additive idempotency | `neo4j_ingest sync` | 1차·2차 모두 생성 0, PASS |
| migration | `complete_english_waiver_rules.py --check` | PASS |
| 잠금/스키마 | `uv sync --locked`, `uv lock --check`, schema exporter check | PASS |
| unittest | `python -m unittest discover -s tests -v` | 288 PASS, 외부 통합 6 skip |
| pytest | `pytest -q` | 282 PASS, 6 skip, 356 subtests PASS |
| Neo4j opt-in | query/dynamic integration | 3 PASS, 6 subtests PASS |
| Ollama/Neo4j opt-in | local LLM + answer + Starlette integrations | 기존 6문항 계층 모두 PASS |
| 보안·형식 | exact-question scan, protected diff, `git diff --check` | 런타임 원문 0, 보호 파일 변경 0, PASS |

최종 커밋 전 위 전체 테스트를 다시 실행했다. 실제 브라우저 새로고침, 클릭과 모바일 시각
검증은 자동화하지 않았으며 통과로 기록하지 않는다.

## 8. 발견된 문제와 위험

- 최초 Verified migration이 기존 JSON을 정렬해 diff가 커졌다. 기존 순서를 보존하고 신규
  18노드·27관계만 append하도록 재생성했다.
- TOEIC Speaking 문구가 일반 TOEIC으로도 추출되고 일반 credential이 Speaking Rule과
  비교되는 문제를 발견해 긴 시험명을 우선하는 중앙 매핑으로 수정했다.
- 한 글자 edit가 `프로그래밍`을 `웹프로그래밍`으로 확장했다. insertion/deletion을 막았다.
- 전체 unittest가 과거의 불필요 clarification 기대 2건과 충돌했다. stable identity와 명시
  requested field를 바로 계획하는 현재 계약으로 테스트를 갱신했다.
- opt-in integration fixture가 keyword-only API와 상세 모드 환경값을 반영하지 못했다.
  fixture를 갱신하고 기본 모드 비노출과 상세 모드 계약을 분리했다.
- 사용자가 입력한 학점·과목의 진위는 검증하지 않는다. USER_ASSERTION으로만 표시한다.

## 9. 남은 작업

- 로그인·서버 프로필 동기화와 성적표 업로드는 범위 밖이다.
- 실제 브라우저 새로고침/키보드/모바일 프로필 조작 수동 검증이 남아 있다.
- 성적·재수강, 휴복학·전과 적용, 실시간 개설·잔여석은 현재 Evidence가 없어 확정하지 않는다.
- PR #10은 평가 원본이며 이 구현의 코드 선행 의존성이 아니다.

## 10. 다음 작업 제안

Draft PR 최신 Head에서 팀원 교차 검토를 받고, 특히 프로필 UI의 수동 저장·복원·삭제와
영어 면제 원문 Evidence를 확인한다. 이번 작업에서는 Draft 해제와 merge를 수행하지 않는다.
