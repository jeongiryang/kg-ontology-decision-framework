# 0012. 지어낸 연출을 걷어내고 엔진이 실제 실행한 순서로 재생

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작업일 | 2026-08-29 |
| 담당자 | 황대겸 |
| 확인자 | 정이량 |
| 브랜치 | `feat/hwang-daegyeom/query-traversal-view` |
| 커밋 | `a464d27` (2차), `e0f8733` (3차) |
| 관련 PR | #33 |
| 선행 로그 | [0011](0011-query-traversal-visualization.md) — **이 로그가 0011의 결론 두 개를 뒤집는다** |

## 1. 작업 목적

0011은 승인된 Cypher의 MATCH 경로를 순서대로 그렸다. 그때는 그것이 보여 줄 수 있는
전부라고 판단했다. 담당자가 "실제 지식그래프에서의 동작을 그대로" 요구하면서 그 판단이
두 번 틀렸음이 드러났고, 엔진이 실제로 실행한 것으로 대체한다.

## 2. 요청 내용 요약

시간순이다. 뒤 요청이 앞 요청을 뒤집은 것이 이 작업의 핵심이다.

1. 실시간으로 동기화해서 실제 동작과 동일하게. 노드마다 소요 시간(1ms 등) 표시.
2. 없는 기능이면 만들어라. 시간별로 탐색 과정이 보이게. 최우선.
3. BFS **처럼**이 아니라 실제 지식그래프에서의 동작을 시각화. **만들어낸 것이면 안 된다.**
4. 실제와 동일하게 하면 된다. **시간 늘리지 마라.**
5. 배속 선택 필요 없다. 빠르면 빠른 대로 둬라. 결과창에서 확인하면 된다.
6. 모든 과정을 보여주고 한국어로 드러나게, 원본도 함께.

## 3. 작업 전 상태

0011 시점의 화면은 다음 상태였다.

- 재생이 **고정 900ms 간격**이었다. 실제 동작과 아무 관계가 없었다.
- 0011 로그와 PR #33 본문에 *"순서는 승인된 MATCH 패턴이 쓰인 차례이며 Neo4j 엔진의
  내부 실행 순서가 아니다"* 라고 적어 두었다.
- 탐색 패널이 `선택 스키마` · `승인 Cypher` · `조회 그래프` 세 탭으로 나뉘어 있었다.

## 4. 수행한 작업

### 4.1 PROFILE 도입 (2차, `a464d27`)

실행기가 `tx.run(text)` 대신 `tx.run("PROFILE " + text)` 로 조회한다. 실행되는 질의는
동일하고 통계만 더 받는다. explainer 가 `EXPLAIN` 을 앞에 붙이는 것과 같은 방식이며,
검증기가 LLM Cypher 에서 `PROFILE` 을 막는 규칙은 그대로 둔다.

`args.Details` 가 `(cv)-[:HAS_OFFERING]->(o)` 형태로 관계 타입을 주므로 승인 경로의 hop 과
대응시킬 수 있다. 반환형을 `ExecutionOutcome(rows, steps)` 으로 바꾸되 파이프라인이 행
목록도 받아들이게 해 기존 테스트 대역을 깨지 않았다. 인스턴스 상태를 쓰지 않아 동시 요청에
안전하다.

### 4.2 모든 operator 보존과 한국어 설명 (3차, `e0f8733`)

`Expand` 계열만 골라내던 것을 모든 operator 로 넓혔다. 단계마다 무엇을 확인했는지
한국어 문장을 붙이고 엔진 원본 표기도 함께 싣는다. 표기는 `ontology_spec.json` 이 이미
보유한 값을 쓴다(속성 `description_ko` 147/147). `cache[o.status]` 같은 내부 표기는 벗기고,
받침에 따라 조사를 고른다(`교과목로` → `교과목으로`).

### 4.3 되돌린 것

| 시도 | 되돌린 이유 |
|---|---|
| BFS 층 파동 재생 | Neo4j 는 BFS 로 돌지 않는다. 지어낸 연출이었다 |
| 재생 길이 확대(단계당 최소 520ms) | "시간 늘리지 마라". 실측 시간 그대로로 되돌림 |
| 배속 선택 UI | "필요 없다". 추가한 그 턴에 제거 |
| 큰 스켈레톤 자리표시 | 화면을 가리기만 했다. 한 줄 안내로 축소 |
| 되묻기 후보 그래프(0011에서 추가) | 0011 시점 요청이 철회돼 이미 제거된 상태 |

## 5. 변경된 파일

| 파일 | 변경 |
|---|---|
| `src/kg_builder/query/query_executor.py` | PROFILE 실행, `TraversalStep`/`ExecutionOutcome`, 전체 operator 보존, `detail` 수집 |
| `src/kg_builder/query/safety_pipeline.py` | 실측 단계와 `execution_ms` 를 `GRAPH_EXECUTION` 이벤트로 |
| `src/evidence_chat/graph_projection.py` | `describe_operator_ko()`, hop 과 실측치 대응 |
| `src/evidence_chat/server.py` | `_safe_traversal_steps()` 검열, 한국어 설명 부착, `$autostring`·`cache[]` 정리 |
| `src/evidence_chat/static/app.js` | operator 순서 재생, 노드 순차 등장, 간선 흐름, 판독 패널, 탭 제거, 접힘, 육하원칙 |
| `src/evidence_chat/static/app.css` | 순차 등장·간선 흐름·판독 목록 스타일, 가로 넘침 차단 |
| `tests/test_evidence_chat.py` | 송출 필드 화이트리스트, 탭 계약 반전, CSS 계약 갱신 |

## 6. 주요 결정과 이유

### 6.1 0011의 결론을 뒤집었다 — 두 번

**첫 번째.** 0011은 *"순서는 MATCH 패턴이 쓰인 차례이며 엔진 실행 순서가 아니다"* 라고
못 박았다. PROFILE 을 쓰면 **엔진이 실제로 실행한 순서**를 받을 수 있었다. 그 서술은 이제
사실이 아니며 PR #33 본문 앞쪽에 그 사실을 표시했다.

**두 번째.** 2차에서 BFS 층 파동으로 그렸다. 담당자가 *"BFS 처럼이 아니라 실제 동작이어야
하고 만들어낸 것이면 안 된다"* 고 지적했고, 확인해 보니 Neo4j 는 BFS 로 돌지 않는다.
`NodeIndexSeek` 로 시작해 `Expand` 와 `Filter` 를 번갈아 흘려보내는 파이프라인이다.
층 단위 파동은 내가 만든 연출이었으므로 제거했다.

### 6.2 Filter 를 빼면 탐색의 내용이 사라진다

2차는 `Expand` 계열만 골라 그렸다. 그 결과 **37행이 9행으로 좁혀지는 구간이 화면에서
통째로 빠졌다.** 어디서 좁혀졌는지가 곧 탐색의 내용이므로 모든 operator 를 남기도록 고쳤다.

### 6.3 시간을 지어내지 않는다

`PROFILE` 의 operator 별 `time` 은 비어 있다. 그래서 hop 마다 `rows` 와 `dbHits` 를 실측값으로
표시하고, 총 실행 시간을 DB 접근 비율로 나눈 값은 화면에 **`배분`** 이라고 명시해 측정값과
구분한다. 재생 길이도 늘리지 않는다.

### 6.4 안전 계약을 넓힌 범위

- `scope_identity` 별칭 **하나만** 선택적으로 허용(0011에서 도입). 없으면 종전 동작으로 물러난다.
- `PROFILE` 은 실행기가 자기 질의에만 붙인다. 검증기가 LLM Cypher 에서 막는 규칙은 유지.

## 7. 검증

| 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 단위 테스트 | `uv run pytest -q` | 260 passed, 6 skipped, 356 subtests passed |
| 번들 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS; nodes=1518, relationships=3260, Evidence=511 |
| JS 문법 | `node --check src/evidence_chat/static/app.js` | 통과 (Windows Node) |
| 실제 질의 | Starlette SSE · 컴퓨터공학과 전공필수 | ANSWERABLE, citations 9, 13단계 한국어 설명 수신 |
| 구조 일치 대조 | 승인 Cypher hop 집합 vs 화면 hop 집합 | 누락 0 / 유령 0 |
| 요구사항 대조 | 담당자 요청 22항목을 payload·서빙 코드로 자동 점검 | 22/22 통과 |
| 브라우저 시각 검증 | Chrome 헤드리스 | **부분 실패.** 아래 8.3 참고 |

실측된 엔진 실행(컴퓨터공학과 전공필수, 9행):

```
 1. NodeUniqueIndexSeek  rows=1   dbHits=2
 2. Expand(All)          rows=1   dbHits=18   (d)<-[:FOR_DEPARTMENT]-(cv)
 3. Filter               rows=1   dbHits=2
 4. Expand(All)          rows=37  dbHits=41   (cv)-[:HAS_OFFERING]->(o)
 5. Filter               rows=9   dbHits=83
 6. Expand(All)          rows=9   dbHits=79   (o)-[:SUPPORTED_BY]->(e)
 7. Filter               rows=9   dbHits=18
 8. Expand(All)          rows=9   dbHits=79   (o)-[:OF_COURSE]->(c)
 9. Filter               rows=9   dbHits=9
10~13. Limit / CacheProperties / Projection / ProduceResults
```

## 8. 발견된 문제와 위험

### 8.1 그래프 조회는 20ms 만에 끝난다

단계별 시각을 찍어 확인했다. 사용자가 기다리는 15초는 전부 LLM(질문 분석 5.7초 +
Cypher 생성 9.7초)이고 DB 구간 전체가 0.2초다.

```
03:52:15.60  GRAPH_EXECUTION  시작
03:52:15.62  GRAPH_EXECUTION  완료   ← 20ms
```

**"실제로 동작하면서 노드를 방문하는 것을 보는 것"은 물리적으로 불가능하다.** 화면이 하는
것은 끝난 탐색을 실측 순서·행수·DB접근 그대로 되감는 재생이다.

### 8.2 operator 별 시간은 Community 에서 나오지 않는다 — Enterprise 에서는 나온다

처음에 "Neo4j 가 주지 않는다"고 단정했으나 정확하지 않았다. 서버 응답으로 확인했다.

```
warn: unsupported runtime. The query cannot be executed with 'runtime=pipelined';
instead, 'runtime=slotted' is used.
Cause: This version of Neo4j does not support the requested runtime: `pipelined`.
```

별도 포트로 Enterprise 이미지를 탐침한 결과 `runtime=PIPELINED` 에서 operator 별 `time` 이
나온다(`AllNodesScan time=18,025,907ns`). 즉 **Community 에서 불가능, Enterprise 에서 가능**이다.
탐침 컨테이너는 제거했고 기존 `neo4j-db` 는 건드리지 않았다.

### 8.3 브라우저 시각 검증이 부분적으로만 됐다

Chrome 헤드리스 **스크린샷은 성공**했다(58KB PNG). 질문을 입력해 그래프 화면까지 몰고 가려면
CDP 원격 제어가 필요한데 실패했다.

```
WSL → 127.0.0.1:9333      → 연결 실패
WSL → 192.168.96.1:9333   → 연결 실패
```

Chrome 이 Windows 쪽에서 돌고 WSL2 의 localhost 포워딩은 Windows→WSL 단방향이라, WSL 에서
Windows 루프백에 묶인 CDP 포트에 붙을 수 없다. Windows Node(v24, `WebSocket` 내장)로
우회하는 경로가 남아 있으나 이번에는 UI 수정을 우선했다.

### 8.4 화면 결함 3건을 담당자 보고로 발견해 고쳤다

- 처리 중 화면이 후보 스키마 목록을 먼저 보여 줬다. EXPLAIN 전이라 전부 `미사용` 으로 표시돼
  아직 판정되지 않은 것을 미사용으로 오해하게 했다.
- 그리드 자식의 기본 `min-width: auto` 때문에 긴 텍스트가 칸을 밀어내 페이지가 가로로
  넘치고 왼쪽이 잘렸다.
- 탐색 그래프가 있어도 기본 탭이 `선택 스키마` 라 그림이 보이지 않았다. 이후 탭 자체를 제거했다.

## 9. 남은 작업

- 브라우저 시각 검증 완성 (Windows Node 로 CDP 구동)
- `docs/evidence-chat.md` 에 탐색 재생 계약 반영
- `docs/environment-setup.md` 에 실측 2건 기록: WSL `libcuda.so.1` 이 ldconfig 캐시에 없어
  Ollama 가 CPU 로 떨어지던 문제, Windows Node 로 JS 문법 검사가 가능하다는 사실
- CLAUDE.md 6절 보완: `gh pr edit` 의 `--add-assignee` 와 `--body-file` 도 Projects(classic)
  GraphQL 오류로 실패한다. 문서에는 `--add-reviewer` 만 적혀 있다. 전부 REST 로 우회했다

## 10. 다음 작업 제안

- 정이량에게 6.1(0011 결론 반전 두 건)과 `PROFILE` 도입 확인 요청
- operator 별 실제 시간이 필요하면 Enterprise 전환을 별도 작업으로 계획. 라이선스 판단과
  데이터 이전(현재 `neo4j-db` 가 익명 볼륨, 이슈 #22)이 선행돼야 한다
- PR #32(정이량, 50문항 질의 정확도)와 범위가 겹칠 수 있어 병합 순서 협의
