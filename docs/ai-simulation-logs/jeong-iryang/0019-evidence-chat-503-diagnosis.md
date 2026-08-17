# 0019. Evidence chat HTTP 503 진단과 과목코드 실동작 보완

## 작업 목적

Draft PR #28의 단위 테스트는 통과했지만 실제 `/api/ask` 네 요청이 HTTP 503으로
종료된 원인을 분리하고, 로컬 Ollama·Neo4j·PDF가 준비된 환경에서 최소 실동작을
확인한다. 환경만의 문제면 코드를 바꾸지 않고, 실제 질의 계약 문제가 재현될 때만
일반화된 회귀 수정을 추가한다.

## 확인한 환경 상태

- Ollama 프로세스와 loopback HTTP endpoint가 실행 중이었다.
- `qwen2.5-coder:14b`는 Ollama API 모델 목록에 존재했다. 새 모델은 받지 않았다.
- LLM provider·base URL·model 설정과 `NEO4J_QUERY_*` 네 설정은 모두 존재했다.
- Neo4j query 설정 파싱, connectivity 확인과 읽기 전용 count 조회가 성공했다.
- 로컬 발췌 PDF는 열 수 있었고 Verified 기준과 같은 19페이지였다.
- 비밀번호, 토큰, 전체 URI, 로컬 PDF 절대 경로와 문서 해시는 출력하거나 기록하지
  않았다.

## HTTP 503 원인

이전 수동 검증은 제한된 실행 샌드박스에서 수행돼 localhost Neo4j 소켓에 접근하지
못했다. 이 때문에 Starlette lifespan의 `ChatState.open()`이 일반 시작 오류인
`CHAT_STARTUP_ERROR`로 정리됐고 `/api/ask`는 HTTP 503을 반환했다.

동일 checkout을 로컬 서비스 접근이 가능한 조건에서 다시 실행했을 때 다음 단계는
모두 성공했다.

1. LLM 설정 파싱과 provider client 생성
2. query 전용 Neo4j 설정 파싱
3. Neo4j connectivity 확인
4. `ChatState.open()`
5. PDF 19페이지 검사
6. `/api/health`: HTTP 200, `service_ready=true`, `pdf_mounted=true`

따라서 503 자체를 해결하기 위한 저장소 설정 fallback이나 서비스 자동 시작 코드는
추가하지 않았다.

## 추가로 발견한 실제 질의 문제

`이산수학의 과목코드가 뭐야`에서 로컬 모델은 올바른 과목명 필터, `course_code`
요청 필드와 `SINGLE_COURSE`를 만들었지만 첫 상태를 `CLARIFICATION_REQUIRED`,
`evidence_required=false`로 반환했다. planner의 안정적인 범위 보완으로 이 계획은
실행 가능한 `READY`가 됐으나 Evidence 불변조건에서 두 번 실패해
`LLM_PLAN_CONTRACT_INVALID`가 발생했다.

모든 실행 가능한 질의는 Evidence가 필수라는 애플리케이션 계약이므로, 확정적인
범위 검사를 거쳐 `READY`가 된 계획에는 planner가 `evidence_required=true`를
강제하도록 수정했다. 이 보완은 질문 문장이나 학수번호·정답값을 분기하지 않으며,
모델이 만든 사실값을 보정하지 않는다.

## 변경 파일

- `src/kg_builder/llm/planner.py`
  - 실행 가능한 모든 `READY` QueryPlan에 Evidence 필수 계약을 강제했다.
- `tests/test_local_llm_pipeline.py`
  - 완전히 범위가 정해진 단일 과목 질문에서 모델이 Evidence flag를 false로 줘도
    질문을 재시도 실패시키지 않고 Evidence 필수 계획으로 정규화하는 회귀 테스트를
    추가했다.

## 실제 네 질문 결과

| 질문 요약 | 상태 | 핵심 결과 | Citation | 시간 |
|---|---|---|---:|---:|
| 자료구조 개설 학년·학기 | ANSWERABLE | 2학년 1학기 | 1 | 13.15초 |
| 이산수학 학수번호 | ANSWERABLE | CDA0157 | 1 | 15.23초 |
| 편입생 교양 이수 | ANSWERABLE | 교양 이수 의무 없음 | 1 | 12.80초 |
| 개인 이력 기반 졸업판정 | UNSUPPORTED | 결정론적 한국어 지원 범위 안내 | 0 | 0.02초 |

영문 clarification은 노출되지 않았다. 질문 원문 네 개는 수동 실행 입력과 테스트
fixture/UI 예시에만 존재하며, 런타임 답변 분기나 정답값 하드코딩에는 사용되지
않는다.

## 진행·inspection 확인

- ANSWERABLE 요청에서 `QUESTION_ANALYSIS`부터 `ANSWER_RENDERING`, `COMPLETED`까지
  실제 단계의 STARTED/COMPLETED 이벤트가 순서대로 도착했다.
- 기본 `KG_CHAT_SHOW_QUERY_DETAILS=false`에서는 inspection을 보내지 않았다.
- 일회성 검증 실행에서만 inspection을 활성화했고, 정적 검증과 EXPLAIN을 통과한
  읽기 전용 Cypher, 정제된 파라미터, 사용 라벨·관계, EXPLAIN 연산자, 행·Evidence
  수와 단계 시간만 포함됨을 확인했다.
- system prompt, 모델 원문, 비밀번호, URI, traceback, 미검증 Cypher는 노출하지
  않았다.

## PDF Citation 최소 확인

실제 이산수학 응답 Citation 한 건으로 발췌 17쪽, 원본 PDF 262쪽, 인쇄 254쪽을
확인했다. Evidence 텍스트 검색은 실제 강조 위치 한 곳을 찾았고, 페이지 endpoint는
유효한 PNG를 HTTP 200으로 반환했다. 해당 Evidence의 DB 검증 상태도 VERIFIED였다.
브라우저 자동화는 실행하지 않았으므로 확대·축소, 이전·다음, 닫기는 연결된 UI
handler와 기존 테스트 범위만 확인했고 수동 클릭 통과로 기록하지 않는다.

## 실행한 검증

```text
UV_CACHE_DIR=/tmp/kg-uv-cache uv run --no-sync pytest -q \
  tests/test_local_llm_pipeline.py tests/test_evidence_chat.py
→ 39 passed, 6 subtests passed

git diff --check
→ 통과
```

추가로 실제 `/api/health`, `/api/ask` 네 질문, Citation 한 건의 PDF 이미지·강조,
Neo4j 전후 count를 읽기 전용으로 확인했다.

## 실행하지 않은 검증

- 전체 `unittest`
- 전체 `pytest`
- 전체 6문항 회귀
- 전체 Neo4j 통합 테스트
- 전체 Evidence 59건 PDF 재검사
- 브라우저 자동 시각·클릭 검사
- clean 환경 전체 재설치

이 항목들은 이번 최소 실동작 범위에서 통과로 기록하지 않는다.

## 데이터 불변성과 남은 제한사항

- Neo4j 전후 기준은 노드 1,518, 관계 3,260, Evidence 511로 유지됐다.
- Raw·Verified KG, `ontology_spec.json`, `.env`, PDF는 커밋 변경 범위에 없다.
- Neo4j Community 계정의 DB 수준 읽기 전용 권한 보장은 별도 운영 위험으로 남는다.
- 샌드박스처럼 localhost 소켓이 차단된 환경에서는 실제 서비스 검증에 동일한 503이
  재현될 수 있으므로, 그 결과를 로컬 서비스 장애와 구분해야 한다.
