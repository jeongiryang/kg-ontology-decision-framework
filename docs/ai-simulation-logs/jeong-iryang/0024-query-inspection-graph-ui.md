# 0024. 질의 inspection 그래프 UI

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-18 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/query-inspection-graph-ui` |
| 관련 커밋 | 본 작업 커밋 |
| 관련 Issue/PR | 신규 Draft PR |
| 작업 상태 | 완료 |

## 1. 작업 목적

최신 `main`의 sealed `ChatResponse`, canonical Cypher와 Fact–Evidence 승인 경계를
유지하면서 로컬 Starlette 화면의 실제 처리 단계를 펼쳐 보고, 승인된 질의 구조와
결과 provenance를 제한된 그래프로 확인할 수 있게 한다.

## 2. 작업 전 상태

- 실제 callback 단계는 누적됐지만 단계별 상세가 한 패널에 분리돼 있었다.
- 승인 Cypher와 집계 수는 별도 영역에 표시됐고 그래프 projection은 없었다.
- 질문 입력창은 한 줄 자동 확장과 간결한 placeholder 계약이 부족했다.
- 작업 트리에는 기존 Git 제외 `Zone.Identifier`가 있었으며 그대로 보존했다.

## 3. 핵심 설계 결정

1. 상세 정보는 `KG_CHAT_SHOW_QUERY_DETAILS=true`인 서버에서만 version 2
   `inspection_update`로 전송한다. 클라이언트 요청으로 활성화하지 않는다.
2. 질의 그래프는 동일 attempt에서 정적 검증과 EXPLAIN을 모두 통과한 canonical
   Cypher의 label·relationship과 generated schema catalog로만 만든다.
3. 결과 그래프는 ResultValidator 승인 행과 ClaimValidator의 승인 provenance 집합이
   정확히 일치할 때만 `VERIFIED Fact ─SUPPORTED_BY→ VERIFIED Evidence`로 만든다.
4. raw 내부 ID 대신 request-local HMAC opaque ID를 쓰며 추가 Neo4j 조회는 하지 않는다.
5. 그래프는 외부 CDN 없이 안전한 SVG DOM API로 만들고, 실패하면 검증 관계 목록으로
   fallback한다. 동적 HTML 문자열 삽입은 사용하지 않는다.
6. sealed `ChatResponse` 8필드는 변경하지 않고 graph는 presentation envelope에만 둔다.

## 4. 수행 작업

- 10개 실제 단계 행을 disclosure button으로 만들고 상태·elapsed와 단계별 allowlist
  상세를 연결했다.
- 승인 Cypher를 `NEO4J_EXPLAIN`, 결과 수와 provenance 그래프를
  `GRAPH_EXECUTION` 상세로 통합했다.
- 질의 구조·결과 provenance projection과 200 node/300 edge 상한을 구현했다.
- 그래프 확대·축소·화면 맞춤·초기화, node 선택, 범례, 모바일 세로 배치와 내부
  스크롤을 추가했다.
- placeholder, 최대 5줄 textarea 자동 확장과 예시 질문 chip 접근성을 정리했다.
- 스트림 worker 완료 신호를 Future로 명확히 하고 queue를 모두 비운 뒤 종료하도록 해
  테스트 ASGI transport와 실제 SSE 종료 계약을 일치시켰다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/evidence_chat/graph_projection.py` | 추가 | 안전한 질의·provenance projection |
| `src/evidence_chat/server.py` | 수정 | stage inspection, graph envelope, SSE 종료 |
| `src/evidence_chat/static/index.html` | 수정 | 입력 placeholder와 통합 처리 패널 |
| `src/evidence_chat/static/app.js` | 수정 | disclosure, SVG 그래프, textarea 자동 확장 |
| `src/evidence_chat/static/app.css` | 수정 | 접근성·그래프·모바일 스타일 |
| `src/kg_builder/query/safety_pipeline.py` | 수정 | 승인 단계의 제한된 검증 요약 |
| `src/kg_builder/answer/service.py` | 수정 | 승인 provenance와 renderer 계약 요약 |
| `tests/test_evidence_chat.py` | 수정 | graph·SSE·DOM·8필드 회귀 |
| `docs/evidence-chat.md` | 수정 | projection 출처와 공개 경계 |

## 6. 보안 경계

- 검증 전·EXPLAIN 실패 후보와 이전 재시도 Cypher는 전송하지 않는다.
- 기본 모드에는 QueryPlan, Cypher, 파라미터, EXPLAIN, graph payload가 없다.
- 질문 원문 trace, prompt, 모델 원문, 접속정보, 자격증명, 로컬 경로, traceback과 승인
  내부값은 상세 모드에도 포함하지 않는다.
- projection은 답변과 Citation을 바꾸지 않는 표시 전용 데이터다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 관련 최소 테스트 | `uv run --no-sync pytest -q tests/test_evidence_chat.py tests/test_dynamic_query_safety.py` | 54 passed, 39 subtests passed |
| Python compile | `python -m compileall` 관련 변경 모듈 | 통과 |
| JavaScript 문법 | `node --check` | 미실행: Node.js 미설치 |
| diff whitespace | `git diff --check` | 통과 |
| 실제 질문 1건 | 로컬 Ollama·Neo4j 도달성 확인 | 미실행: Ollama·14B 모델은 준비됐으나 Neo4j Bolt가 도달 불가 |
| 브라우저 시각 검증 | 실제 브라우저 | 미실행: 자동화 도구 없음 |

## 8. 발견된 문제와 해결

초기 관련 테스트에서 Starlette streaming response가 worker 완료 뒤 종료되지 않았다.
별도 daemon worker는 유지하되 완료 Future와 queue drain 조건을 분리해 단일 GPU
semaphore를 worker 종료까지 유지하면서 스트림이 정상 종료되도록 수정했다.

## 9. 실행하지 않은 검증

- 전체 unittest·전체 pytest
- 전체 Neo4j·Ollama 통합과 기존 6문항 회귀
- 전체 PDF 검사와 clean reinstall
- 실제 브라우저의 시각·클릭·콘솔 검사

## 10. 남은 제한사항

- 그래프는 presentation projection이며 범용 Neo4j explorer가 아니다.
- Evidence 원문 전체와 raw 내부 ID는 의도적으로 graph node에 넣지 않는다.
- 사용자가 브라우저 연결을 끊어도 이미 시작된 로컬 모델 호출은 즉시 중단되지 않는다.
