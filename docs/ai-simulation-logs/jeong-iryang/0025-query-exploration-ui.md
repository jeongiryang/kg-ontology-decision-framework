# 0025. 실제 승인 데이터 기반 질의 탐색 UI 개선

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-18 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/query-inspection-graph-ui` |
| 관련 커밋 | 본 작업 후속 커밋 |
| 관련 Issue/PR | Draft PR #30 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #30의 첫 SVG projection에서 발생한 node 잘림, 긴 label·edge 겹침과 상세 정보 탐색성
문제를 해결한다. Neo4j 내부 실행을 가장하지 않고 실제 callback에서 승인된 스키마,
canonical Cypher, ResultValidator Fact와 ClaimValidator provenance만 순차적으로 표시한다.

## 2. 작업 전 상태

- 승인 Cypher와 그래프가 각 처리 단계 disclosure 안에 분산돼 있었다.
- SVG가 넓은 고정 구조를 사용해 기본 상태에서도 가로 스크롤과 우측 잘림이 생길 수
  있었다.
- 긴 node·relationship label과 edge가 겹칠 수 있었다.
- 스키마 선택과 ResultValidator 승인 Fact를 별도 단계로 보여 주지 않았다.
- 실제 데이터가 없는 단계도 시간·상태만 반복하는 빈 disclosure가 있었다.

## 3. 핵심 설계 결정

1. 상세 탭과 graph payload는 서버의 `KG_CHAT_SHOW_QUERY_DETAILS=true`에서만 제공한다.
2. `SCHEMA_SELECTION`은 선택된 label·relationship projection만 만들고 Cypher를 노출하지
   않는다.
3. canonical Cypher와 질의 경로는 같은 후보가 정적 검증과 EXPLAIN을 모두 통과한 뒤에만
   표시한다.
4. Graph 실행 행은 곧바로 node로 만들지 않고 ResultValidator가 Fact·Evidence 상태와
   직접 provenance를 모두 승인한 뒤 VERIFIED Fact projection으로 만든다.
5. ClaimValidator가 승인한 정확한 Fact–Evidence 쌍만 provenance edge로 만든다.
6. 그래프 애니메이션은 새 승인 projection의 등장 순서를 표현할 뿐 Neo4j 내부 처리나
   모델 추론을 표현하지 않는다.

## 4. 수행 작업

- 처리 화면과 답변 화면에 `선택 스키마`, `승인 Cypher`, `조회 그래프` 탭을 추가했다.
- 실제 callback 데이터가 없는 탭은 비활성화하고 승인 데이터가 도착할 때만 갱신한다.
- 단계 disclosure는 안전한 상세 정보가 있는 단계에만 생성하고 해당 탭 이동 링크를
  제공한다.
- 선택 schema, 승인 query path, VERIFIED Fact, 직접 Evidence provenance를 별도
  projection으로 순차 렌더링한다.
- 반응형 SVG `viewBox`, 자동 화면 맞춤, desktop type-column/mobile vertical layout,
  node 간격, 두 줄 label, ellipsis tooltip, 관계 label 배경과 offset을 적용했다.
- 기본 배율에서는 가로 overflow를 숨기고 사용자가 확대했을 때만 pan을 허용했다.
- node fade, 승인 query path와 provenance pulse, reduced-motion 대체 상태를 추가했다.
- 외부 그래프 라이브러리와 추가 Neo4j 조회는 도입하지 않았다.

## 5. 보안과 계약

- 기본 모드에는 상세 탭과 inspection graph payload가 없다.
- EXPLAIN 전·실패·폐기 후보 Cypher는 graph, SSE, DOM과 clipboard에 들어가지 않는다.
- ResultValidator 승인 전 row와 ClaimValidator 승인 밖 Fact–Evidence 쌍은 표시하지 않는다.
- raw Fact/Evidence ID 대신 request-local opaque `ui:*` ID만 사용한다.
- 동적 문자열은 `textContent` 또는 SVG DOM API로만 삽입한다.
- sealed `ChatResponse` 8개 wire 필드는 변경하지 않았다.

## 6. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 관련 최소 테스트 | `uv run --no-sync pytest -q tests/test_evidence_chat.py tests/test_dynamic_query_safety.py` | 56 passed, 39 subtests passed |
| Python compile | 변경 Python 모듈 `compileall` | 통과 |
| diff whitespace | `git diff --check` | 통과 |
| JavaScript 문법 | `node --check` | 미실행: Node.js 미설치 |
| 실제 질문 한 건 | `컴퓨터공학과 전공필수 과목은?` Starlette SSE | 첫 요청은 데이터 기반 selection mode clarification, 서버 발급 `COURSE_LIST` 선택 후 ANSWERABLE: 9과목·21학점, Citation 9건 |
| 상세 inspection | 임시 `KG_CHAT_SHOW_QUERY_DETAILS=true` 서버 | schema 8 node → query 5 node → Fact 9 node → provenance 18 node/9 edge, 승인 Cypher event가 result보다 먼저 도착 |
| Neo4j 사후 개수 | read-only count | node 1,518 / relationship 3,260 / Evidence 511 |
| 브라우저 시각 검증 | desktop·narrow 화면 | 미실행: 브라우저 자동화 도구 없음 |

## 7. 실패한 접근과 수정

최초 테스트에서 RESULT_VALIDATION progress에 `ValidatedCypher.rows`를 참조해 4개 테스트가
실패했다. 조회 실행 결과인 `result.rows`로 배선을 수정한 뒤 지정된 최소 테스트가 모두
통과했다. 실패한 결과를 통과로 기록하지 않았다.

## 8. 실행하지 않은 검증

- 전체 unittest·전체 pytest
- 전체 Neo4j·Ollama 통합과 기존 6문항 회귀
- 전체 PDF 검사와 clean reinstall
- 실제 브라우저의 클릭·반응형·console 검사

실제 질문 전 DB 개수는 이 작업에서 별도 측정하지 않았다. 사후 개수는 저장소의 기존
기준값과 일치하지만 이를 독립적인 전후 측정으로 과장하지 않는다.

## 9. 남은 제한사항

- projection은 승인 산출물을 재생하는 UI이며 Neo4j 내부 token 이동 이벤트가 아니다.
- schema/query 구조는 승인 label·relationship의 schema catalog 연결이며 범용 Cypher AST
  또는 Neo4j explorer가 아니다.
- 200 node/300 edge 상한을 넘거나 안전 계약을 위반하면 관계 목록 fallback 또는 미표시로
  종료한다.
