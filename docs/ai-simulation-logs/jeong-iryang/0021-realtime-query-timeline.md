# PR #28 실시간 처리 타임라인과 승인 Cypher 추적 UI

## 작업 목적

대표 진행 문구만 교체하던 화면을 실제 pipeline callback 기반 누적 타임라인으로 확장하고, 동일 후보의 정적 검증과 Neo4j EXPLAIN을 모두 통과한 읽기 전용 Cypher만 처리 중·결과 화면에 표시하도록 했다.

## 구현 결정

- `progress` 이벤트의 실제 시작·완료·실패만으로 타임라인 상태를 변경한다.
- 아직 호출되지 않은 단계는 대기 상태로만 표시하며 완료로 만들지 않는다.
- 완료 단계와 실제 소요시간은 결과 화면의 `처리 과정 보기`에 유지한다.
- 공개 가능한 대문자 오류 코드만 실패 행에 표시한다.
- 후보 재생성 시 별도 retry 이벤트를 표시하고 이전 후보 inspection을 철회한다.
- 단계별 상세 정보는 `inspection_update`로 실제 확정 시점에 보낸다.
- 정적 검증 결과는 후보 전용 임시 상태로 유지하고 같은 attempt의 EXPLAIN 완료 후에만 Cypher·파라미터·연산자를 공개한다.
- EXPLAIN 이후 실행·결과 검증이 실패해 재생성되면 이전 승인 후보도 클라이언트에서 제거한다.
- 상세 모드는 `KG_CHAT_SHOW_QUERY_DETAILS=true`일 때만 서버가 전송한다.
- sealed `ChatResponse`의 8개 wire 필드는 변경하지 않는다.

## UI

- 처리 중 화면에 실제 단계 타임라인과 선택적 상세 inspection을 실시간 누적한다.
- 결과 화면에 `처리 과정 보기`와 `Cypher 및 지식그래프 탐색 정보 보기`를 분리해 유지한다.
- 승인 Cypher는 접기·펼치기, 가로 스크롤, 키보드 버튼과 복사 성공·실패 안내를 제공한다.
- 취소 시 완료된 단계는 유지하고 당시 진행 중인 단계만 취소 상태로 바꾼다.
- 질문·Cypher·Evidence를 포함한 동적 문자열은 `textContent`로 삽입한다.

## 공개 정보와 비공개 정보

공개 정보:

- 정제된 QueryPlan과 파라미터
- 선택 라벨·관계
- EXPLAIN 승인 읽기 전용 Cypher와 연산자, LIMIT
- 행·Fact·VERIFIED Evidence·Claim·Citation 수
- 단계별·전체 시간과 정제된 request ID

비공개 정보:

- 검증 전·실패·폐기 후보 Cypher
- system prompt와 모델 원문·내부 추론
- 비밀번호·토큰·API key, Neo4j URI·계정
- 로컬 파일 경로, traceback, 승인 seal·digest

## 검증

- `uv run --no-sync pytest -q tests/test_evidence_chat.py tests/test_local_llm_pipeline.py`
  - 49 passed, 6 subtests passed
- 실제 Starlette `/api/ask` 한 질문: `컴퓨터공학과 전공필수 과목은?`
  - HTTP 200, `ANSWERABLE`, 약 21.8초
  - 9과목·21학점, Citation 9건
  - 행 9, 고유 Fact 9, VERIFIED Evidence 9
  - 전체 10개 pipeline 단계가 실제 순서로 도착
  - EXPLAIN 승인 update가 result보다 먼저 도착
  - LIMIT 100 읽기 전용 Cypher와 EXPLAIN 연산자 확인
  - sealed `ChatResponse` 8개 필드 유지
  - system prompt, URI, 비밀번호, traceback 비노출 확인

## 미실행 검증

- 전체 unittest와 전체 pytest
- 전체 Neo4j 통합 테스트
- 기존 6문항 전체 회귀
- 브라우저 시각·반응형 확인
- 실제 복사 버튼 클릭과 키보드 조작
- 전체 Evidence PDF 검사

## 남은 제한사항

- 브라우저 취소는 UI 연결을 중단하지만 이미 시작한 Ollama 계산을 즉시 중단하지 않는다.
- 타임라인은 관찰 가능한 callback과 승인 산출물만 보여 주며 모델의 내부 추론은 제공하지 않는다.
- 복사 기능은 브라우저 Clipboard API 권한 정책의 영향을 받으며 실패 시 한국어 안내를 표시한다.

## 다음 작업

- 최신 PR #28 Head에서 실시간 이벤트 순서, Cypher 승인 경계와 기본·상세 모드 비노출을 독립 재검토한다.
