# 0011. 실제 질의가 밟은 경로를 그대로 그리는 탐색 시각화

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작업일 | 2026-08-28 ~ 2026-08-29 |
| 담당자 | 황대겸 |
| 확인자 | 정이량 |
| 브랜치 | `feat/hwang-daegyeom/query-traversal-view` |
| 분기 기준 | `origin/main` `738575f` |

## 1. 작업 목적

work.txt 4·5번(노드·관계의 한국어 표기, 처리 과정 시각화)을 구현한다. 승인된 질의가
지식그래프를 실제로 밟은 경로를 루트부터 순서대로 화면에 그려, 어떤 노드를 거쳐 답과
근거에 도달했는지 확인 가능하게 한다.

## 2. 요청 내용 요약

담당자 요청을 시간순으로 적는다. 뒤 요청이 앞 요청을 좁히거나 뒤집은 경우가 많다.

1. 노드·관계·간선 이름을 한국어로. 단 코드 식별자는 영어 유지.
2. 탐색 과정 시각화. 방문 노드·간선 색 변화, 간선에 순서 표기, Cypher 노출.
3. 처리 중에도 뜨고 결과 화면에도 같은 그래프가 보여야 함.
4. 되묻기에도 **무조건** 그래프가 떠야 함.
5. → **철회.** 실제 탐색을 수행하지 않으면 탐색 그래프는 필요 없음. 되묻기에는 뜨지 않아야 함.
6. 그림을 사각 카드가 아니라 원형 노드·화살표 형태로.
7. 이름은 노드 **안**에, 순서는 **간선**에.
8. 루트에서 하위 노드로 내려가는 트리 구조로.
9. 왼쪽 그래프 / 오른쪽 방문 순서 목록.
10. 라벨 종류명이 아니라 **실제 노드 이름**을 한국어로.
11. 실제 탐색 구조와 동일한지 **검증**하고 다르면 수정.

## 3. 작업 전 상태

- PR #30에서 조회 그래프 UI가 도입됐으나, 온톨로지가 허용하는 끝점의 **교차곱**으로
  간선을 그려 질의가 밟지 않은 간선이 나타났다. 화면에 뜬 것은 경로가 아니라 스키마 도식이었다.
- 처리 중 화면에는 그래프가 없었다. 커밋 `080319f`가 "최종 PM 결정"으로 제거했고,
  `tests/test_evidence_chat.py`가 `assertNotIn("progress-exploration", markup)`으로 부재를 고정하고 있었다.
- `.env`의 `KG_CHAT_*` 네 개가 적용되지 않아 `KG_CHAT_SHOW_QUERY_DETAILS=true`를 넣어도
  상세가 열리지 않았다.

## 4. 수행한 작업

### 4.1 승인 경로의 순서 보존

`CypherValidator`는 MATCH를 파싱하며 순서가 살아 있는 간선 목록을 이미 만들지만,
`ValidatedCypher`로 내보낼 때 `tuple(sorted(labels))`, `tuple(sorted(relationship_types))`
집합만 실어 순서를 버리고 있었다. `PathStep`을 추가해 순서를 그대로 전달한다.

### 4.2 한국어 표기 배선

`ontology_spec.json`이 이미 node label `name_ko` 26/26, relationship `name_ko` 31/31,
속성 `description_ko` 147/147을 보유하고 있었다. `SchemaCatalog`가 파싱 시 이를 버리고
있어 보존하도록 고치고 `label_ko()` / `relationship_ko()`를 추가했다. 새 사전을 만들지 않았다.

### 4.3 통합 탐색 그래프

`build_traversal_projection()`을 추가해 스키마 경로와 provenance를 그림 두 장으로 나누던
것을 한 장으로 합쳤다. 상단 범위 노드는 검증된 질의 파라미터에서, 하단은 ResultValidator가
승인한 행과 ClaimValidator가 승인한 provenance 쌍에서만 나온다.

### 4.4 실제 노드 이름 (RETURN 계약 확장)

뿌리 노드를 `2026학년도 대학 공통 교양 교육과정`처럼 실제 이름으로 부르기 위해
`scope_identity` 별칭 **하나만** 선택적으로 허용하도록 RETURN 계약을 넓혔다. 검증기가
온톨로지 선언 속성인지 재확인하고(`CYPHER_SCOPE_IDENTITY_INVALID`), 결과 검증기도 값을
확인한다(`RESULT_SCOPE_IDENTITY_INVALID`). 없으면 종전 조합 이름으로 물러난다.

### 4.5 되묻기 후보 그래프 — 만들었다가 제거

요청 4에 따라 `build_clarification_projection()`으로 되묻기 후보를 그래프로 그렸으나,
요청 5로 철회돼 함수·송출·렌더·검증 화이트리스트를 모두 제거했다.

### 4.6 화면

- 원형/스타디움 노드, 이름은 노드 안(윗줄 카테고리, 아랫줄 실제 이름), 순서는 간선 배지
- 루트 위·자식 아래 트리 배치(리프를 왼쪽부터 놓고 부모를 자식 가운데에)
- 왼쪽 그래프 / 오른쪽 방문 순서 목록, 재생 시 양쪽 동시 강조
- 처리 중 화면은 자동 재생, 결과 화면은 `▶` 버튼
- 탐색 그래프가 있으면 그 탭을 먼저 연다(종전 기본값 `선택 스키마`라 그림이 있어도 안 보였다)
- 조회가 없었으면 "지식그래프 탐색" 상자 자체를 내린다

## 5. 변경된 파일

| 파일 | 변경 |
|---|---|
| `src/kg_builder/query/cypher_validator.py` | `PathStep` 추가, `path_edges` 전달, `scope_identity` 선택 별칭 허용 |
| `src/kg_builder/query/schema_catalog.py` | `name_ko`/`description_ko` 보존, `label_ko()`/`relationship_ko()` |
| `src/kg_builder/query/safety_pipeline.py` | `STATIC_VALIDATION` 이벤트에 `path_edges` 동봉 |
| `src/kg_builder/query/result_validator.py` | `scope_identity` 값 검증 |
| `src/kg_builder/llm/cypher_generator.py` | 스캐폴드가 `cv.version_name AS scope_identity` 반환 |
| `src/evidence_chat/graph_projection.py` | 교차곱 → 실제 경로, `build_traversal_projection()` 추가 |
| `src/evidence_chat/server.py` | `load_dotenv()` 선행, `path_edges`/통합 그래프 송출 |
| `src/evidence_chat/static/app.js` | 트리 배치, 노드 안 이름, 간선 순서, 재생, 방문 순서 목록 |
| `src/evidence_chat/static/app.css` | 노드·간선·재생·2단 배치 스타일 |
| `src/evidence_chat/static/index.html` | 처리 중 화면에 `progress-exploration` 추가 |
| `tests/test_evidence_chat.py` | 송출 필드 화이트리스트 갱신, 처리 중 그래프 계약 반전, 환경 고정 |

## 6. 주요 결정과 이유

### 6.1 순서의 근거를 "승인된 MATCH 패턴이 쓰인 차례"로 한정

담당자는 "실제 지식그래프에서 어떤 식으로 탐색이 일어났는지"를 요구했다. Neo4j 드라이버는
행 단위 방문 시각이나 엔진 내부 실행 순서를 주지 않는다. EXPLAIN이 주는 것은 operator
목록뿐이다. 따라서 순서는 **질의가 그래프를 밟은 차례**로 정의하고, 화면 문구와 코드
주석에 그 취지를 명시했다. 알 수 없는 것을 그리지 않는다.

### 6.2 되묻기 후보 그래프 철회 (요청 4 → 5)

되묻기 시점에 선택지가 가리키는 적재 사실을 후보 그래프로 그렸다가, "실제 탐색을 하지
않으면 탐색 그래프는 필요 없다"는 요청으로 전량 제거했다. 조회가 실행되지 않은 요청에는
보여 줄 탐색이 없다는 원칙이 더 명확하다.

### 6.3 2026-08-18 PM 결정을 뒤집음

`080319f`가 "처리 중 화면은 실제 callback 텍스트 타임라인만 사용한다"는 결정으로 그래프
애니메이션을 제거했고(287줄 삭제), 테스트가 그 부재를 고정하고 있었다. 담당자 지시로
"탐색 중에도 뜨고 결과창에도 같은 것이 보여야 한다"로 바뀌어 계약을 반전했다. 테스트에
변경 사유와 날짜를 주석으로 남겼다. **정이량 확인이 필요하다.**

### 6.4 RETURN 계약은 별칭 하나만, 선택적으로

실제 노드 이름을 위해 계약을 넓혔으나 `scope_identity` 하나로 한정하고 선택 항목으로 두어,
값이 없으면 종전 동작으로 물러나게 했다. 안전 경계를 넓히지 않기 위한 것이다.

### 6.5 잘림은 축소가 아니라 가로 스크롤로 해결

뷰포트에 억지로 맞추면 한국어가 뭉개져 읽을 수 없다. `overflow-x: auto`와 자연 폭 전달로
원래 크기를 보존한다.

## 7. 검증

| 항목 | 명령 | 결과 |
|---|---|---|
| 단위 테스트 | `uv run pytest -q` | 260 passed, 6 skipped, 356 subtests passed |
| 번들 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS; nodes=1518, relationships=3260, Evidence=511 |
| 생성 스키마 | `uv run python -m kg_builder.query.schema_exporter check` | matches ontology_spec.json |
| JS 문법 | `node --check src/evidence_chat/static/app.js` | 통과 (Windows Node `/mnt/c/Program Files/nodejs/node.exe`) |
| 실제 질의(3노드) | Starlette SSE, 교양 최소 이수학점 | ANSWERABLE, citations 1, 경로 3노드 2간선 |
| 실제 질의(29노드) | Starlette SSE, 컴퓨터공학과 전공필수 | ANSWERABLE, citations 9, 노드 29 / 간선 28 |
| **경로 일치 검증** | 승인 Cypher의 MATCH hop 집합 vs 화면 hop 집합 대조 | **완전 일치. 누락 0, 유령 0** |
| 브라우저 시각 검증 | — | **미실행.** 브라우저 자동화 도구 없음 |

## 8. 발견된 문제와 위험

### 8.1 그린 그래프에 실제로 없는 노드가 있었다 (수정함)

담당자의 "실제 구조와 동일한지 검증하라"는 요청으로 대조한 결과, `CourseOffering`이
스코프 노드 1개 + 실제 인스턴스 9개로 **중복 생성**되고 `Course`가 라벨명만 뜨는 유령 노드가
그려지고 있었다. fact 라벨로 **들어오는** hop만 걸러내고 **나가는** hop을 걸러내지 않은
탓이다. 양끝 어디로든 닿으면 스코프 척추에서 제외하도록 고치고, fact→이웃 hop은 행이 실제로
들고 있는 값으로 전개했다. 대조 결과 누락 0 / 유령 0.

### 8.2 `.env`의 `KG_CHAT_*`가 적용되지 않고 있었다 (수정함)

`load_dotenv()`가 `LLMSettings.from_env()` 안에서만 불렸고 그 호출이 `_env_bool` 뒤에 있어,
`KG_CHAT_DEBUG` / `KG_CHAT_SHOW_QUERY_DETAILS` / `KG_CHAT_MAX_CONCURRENT` /
`KG_CHAT_CLIENT_TIMEOUT_SECONDS`가 프로세스 환경변수로 넘길 때만 적용됐다. `.env.example`과
`docs/deployment.md`가 안내하던 설정이 무효였다. `ChatState.open()` 선두로 옮겨 해결했다.

이 수정으로 테스트가 개발자 로컬 `.env`를 읽게 돼 한 건이 깨졌다. 기본값을 기대하는 테스트
두 곳에 값을 명시적으로 고정해 해결했다.

### 8.3 스키마 탭 표기가 사실과 달랐다 (수정함)

`선택된 node label`이라 적혀 있었으나 실제로는 모델에게 건넨 **후보 목록**이었다. 실측에서
`CourseOffering`, `HAS_OFFERING`은 후보로만 주고 최종 질의에 쓰이지 않았다. `후보 node
label`로 바꾸고 `· 사용됨` / `· 미사용`을 표시했다.

### 8.4 남아 있는 위험

- 브라우저 실제 렌더링을 확인하지 못했다. 29노드 배치에서 겹침이 있을 수 있다.
- 뿌리 노드가 `CurriculumVersion`이 아닌 fact family에서는 `scope_identity`가 없어 조합
  이름으로 물러난다.
- PR #32(정이량, 50문항 질의 정확도)와 범위가 겹칠 수 있다. 병합 순서 협의가 필요하다.

## 9. 남은 작업

- 결과 화면 그래프를 기본 접힘으로 두고 펼쳤을 때 재생 (담당자 요청, 미구현)
- 섹션별 육하원칙 상세 (work.txt 5번 후반, 미구현)
- 브라우저 시각 검증
- `docs/evidence-chat.md`에 탐색 그래프 계약 반영

## 10. 다음 작업 제안

- 정이량에게 6.3(2026-08-18 결정 반전)과 6.4(RETURN 계약 확장) 확인 요청
- `docs/environment-setup.md`에 두 가지 실측 기록: WSL `libcuda.so.1`이 ldconfig 캐시에
  없어 Ollama가 CPU로 떨어지는 문제, Windows Node로 JS 문법 검사가 가능하다는 사실

---

## 정정 (2026-08-29)

**대상: 8.4 남아 있는 위험**

> 뿌리 노드가 `CurriculumVersion`이 아닌 fact family에서는 `scope_identity`가 없어 조합
> 이름으로 물러난다.

이 서술은 사실이 아니다. 확인 방법과 결과는 다음과 같다.

`build_syntax_scaffold()`가 `scope_identity`를 붙이는 조건은
`aliases.get("CurriculumVersion") == "cv"` 하나다. `fact_families.EXTENDED_FAMILIES`의
모든 항목과 기본 두 family(`CourseOffering`·`Rule`)의 alias 선언을 대조한 결과,
**16개 `selection_mode` 전부가 `CurriculumVersion` 별칭 `cv`를 쓴다.** 따라서 조합 이름으로
물러나는 fact family는 현재 하나도 없다.

`_scoped_label()`의 폴백 경로는 남겨 둔다. 앞으로 `CurriculumVersion`을 거치지 않는
family가 추가될 수 있고, 그때 이름이 비는 것보다 조합 이름이라도 나오는 편이 낫다.

정정 사유: 담당자 지시로 실제 선언을 대조해 확인함. 8.4의 해당 항목은 위 내용으로 읽는다.

---

## 갱신 (2026-08-29) — 7절 검증, 9절 남은 작업

작성 당시에는 브라우저 검증이 불가능했고 남은 작업이 여섯 항목이었다. 그 뒤 커밋
`81ad658` · `be20ee4` · `09cfc15` · `7e63248` 로 대부분이 처리됐다. 아래가 최신이며,
7절과 9절은 이 내용으로 읽는다.

### 7절 갱신 — 검증

| 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 단위 테스트 | `uv run pytest -q` | 264 passed, 6 skipped, 373 subtests passed |
| 번들 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS; nodes=1518, relationships=3260, Evidence=511 |
| JS 문법 | `node --check src/evidence_chat/static/app.js` | 통과 (Windows Node) |
| **브라우저 시각 검증** | Playwright + Chromium | **실행함.** 아래 참고 |
| 3노드 · 29노드 (라이트/다크) | `scripts/verify_chat_ui.py` | 말줄임 0 · 콘솔오류 0 · 노드 겹침 0 |
| `prefers-reduced-motion` | 〃 `--reduced-motion` | 통과 |
| 진행 중 · 결과 화면 배치 일치 | 노드 `transform` 29개 대조 | 전부 일치 |
| 단계별 상세 4경우 | 정상 / 되묻기 / 재시도 / UNRESOLVED | 10 / 3 / 14 / 3 단계로 각각 채워짐 |
| 추적 내보내기 | JSON 복사 후 파싱 | 질문·시각·10단계·인용 포함, 개인 데이터 마스킹 켬 |
| 화면 문구 | 한글 없는 항목 탐색 | 0건 |

작성 당시 "브라우저 자동화 도구 없음"으로 미실행 처리했으나, WSL 에서 sudo 없이 준비
가능했다. 절차는 `docs/environment-setup.md` 12절과
`scripts/setup_browser_verification.sh` 에 있다.

**브라우저 검증이 잡아낸 결함 3건.** 셋 다 `node --check` 와 단위 테스트를 통과했다.

| 결함 | 증상 |
|---|---|
| `collapsible` 미선언 | 결과 렌더가 `ReferenceError` 로 죽고 화면에는 "연결이 종료되었습니다" 로만 보임 |
| CSS `transform` 이 SVG `transform` 속성을 덮어씀 | 미방문 노드가 좌표를 잃고 원점으로 튀어 루트 위에 겹침 |
| 형제 줄바꿈 첫 구현 | 서로 다른 부모의 자식이 같은 x 를 써서 겹침 13쌍 |

### 8.4 갱신 — `scope_identity` 폴백

위 「정정 (2026-08-29)」 절 참고. **16개 `selection_mode` 전부**가 `CurriculumVersion`
별칭 `cv` 를 써서 실제 이름을 받는다. 조합 이름으로 물러나는 fact family 는 없다.

### 9절 갱신 — 남은 작업

| 작성 당시 항목 | 현재 |
|---|---|
| 결과 화면 그래프 기본 접힘 | **완료** (`be20ee4`) |
| 섹션별 육하원칙 상세 | **완료** — 9단계 전부, 값의 출처 표시 포함 |
| 브라우저 시각 검증 | **완료** |
| `docs/evidence-chat.md` 에 탐색 그래프 계약 반영 | **남음** |

새로 생긴 남은 작업은 다음과 같다.

- PR #32 병합 후 리베이스. 공유 6파일이 겹치며 병합 순서는 #32 → #33 으로 합의했다.
- 진행 중 화면과 결과 화면의 `viewBox` 가 각각 2155x1282 / 2101x1067 로 다르다. 노드
  좌표는 29개 전부 일치하므로 배치 문제는 아니고 `getBBox()` 측정 시점 차이로 보이나
  확인하지 않았다. 시각 결함이 없어 우선순위는 낮다.
- 실시간 hop 탐색은 하지 않기로 했다. 근거는 [0013](0013-no-realtime-hop-traversal.md).
