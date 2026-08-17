# 0009. 재시작 절차 확립과 상태 점검의 사각지대 기록

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-15 |
| 담당자 | 황대겸 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | feat/hwang-daegyeom/answer-coverage |
| 관련 커밋 | 미커밋 (작업 트리) |
| 관련 Issue/PR | 없음 |
| 작업 상태 | 완료 |

## 1. 작업 목적

담당자가 화면을 확인하려는 시점에 스택 전체가 내려가 있었다. 되살리는 과정에서 문서에 없던
절차와, 상태 점검이 잡지 못하는 사각지대를 확인했다. 같은 상황에서 다시 헤매지 않도록
`docs/environment-setup.md`에 남긴다.

코드 변경은 없다. 운영 절차와 문서만 다룬다.

## 2. 요청 내용 요약

1. "지금 확인해보게 웹 띄워줘"
2. "도커는 내가 켰는데 NEO4J는 어캐 킴?"
3. "어 진행해" — 확인한 내용을 환경 문서에 반영하라는 승인.

## 3. 작업 전 상태

- `docs/environment-setup.md`에는 **설치** 절차만 있었다. 이미 설치된 환경을 재부팅 후 다시
  올리는 절차는 없었다.
- 9.1절에 "WSL 세션마다 다시 띄워야 한다"고 적혀 있으나, 웹 챗봇을 쓰려면 무엇을 어떤 순서로
  켜야 하는지는 어디에도 없었다.

## 4. 수행한 작업

### 4.1 무엇이 내려가 있었는지 확인

| 대상 | 상태 | 확인 방법 |
|---|---|---|
| Neo4j 컨테이너 | `Exited (255)` | `docker ps -a` |
| Ollama | 프로세스 없음, 11434 닫힘 | `pgrep -a ollama`, `ss -ltn` |
| 웹 서버 | 떠 있으나 `service_ready: false` | `curl /api/health` |
| 스크래치패드 임시 파일 | 지워짐 | 측정 스크립트 재작성 필요 |

Docker Desktop이 재시작되면서 컨테이너가 함께 내려갔고, WSL 세션이 정리되면서 Ollama도 함께
사라졌다. 처음에는 Neo4j만 문제로 보였다.

### 4.2 되살린 순서

```bash
docker start neo4j-db                                        # 8절의 docker run 을 다시 하지 않는다
nohup ~/.local/bin/ollama serve > ~/ollama-serve.log 2>&1 &
uv run python -m evidence_chat.server                        # 반드시 마지막
```

`docker start` 만으로 데이터가 그대로 올라왔다(`nodes=1518, relationships=3260,
evidence=511`). 컨테이너를 다시 만들 이유가 없다.

### 4.3 확인한 사각지대 — `/api/health`가 Ollama를 보지 않는다

Neo4j를 켜고 웹 서버를 다시 띄우자 `service_ready: true`가 나왔는데도 질문이 실패했다.

```text
■ ㅇ런아러      1턴 OUT_OF_SCOPE                       ← 정상 응답
■ 과목 알려줘    1턴 SAFE_FAILURE · QUERY_PLANNING_FAILED  ← 실패
```

`src/evidence_chat/server.py`의 `ChatState.open()`을 읽어 원인을 확인했다. 시작 시
`verify_connectivity()` 로 확인하는 것은 **Neo4j 뿐**이고, LLM 클라이언트는 객체만 만든다.
실제 호출은 질문이 들어올 때 처음 일어난다. 그래서 Ollama가 꺼져 있어도 상태 점검은 정상으로
보고한다.

| 무엇이 내려갔나 | `/api/health` | 질문했을 때 |
|---|---|---|
| Neo4j | `service_ready: false` | `503 Service Unavailable` |
| Ollama | `service_ready: true` | `SAFE_FAILURE` · `QUERY_PLANNING_FAILED` |

이 조합이 판별식이 된다.

**0008에서 넣은 검사가 진단을 헷갈리게 만들 수 있다.** 뜻 없는 입력은 LLM을 부르기 전에 범위
밖으로 끝나므로, Ollama가 꺼져 있어도 그 입력만은 정상 응답한다. "웹은 되는데 질문만 안 된다"는
상태를 오해하지 않도록 문서에 함께 적었다.

### 4.4 웹 서버를 마지막에 띄워야 하는 이유

`ChatState.open()`은 애플리케이션 시작 시 한 번만 실행되고 실패해도 재시도하지 않는다. 실패하면
`error` 를 세우고 드라이버를 닫으며, `ready` 는 계속 `False` 다. Neo4j 없이 웹 서버를 먼저
띄우면 그 뒤 Neo4j를 켜도 웹 서버는 스스로 붙지 않는다. **코드를 읽어 확인한 사실이며, 재시도가
일어나지 않는 것을 런타임에서 재현해 보지는 않았다**(웹 서버를 곧바로 재시작했다).

### 4.5 문서 갱신

`docs/environment-setup.md`에 `12. 재부팅·재시작 후 다시 올리기`를 신설하고, 기존
`12. 팀 공통 운영 규칙`을 `13.`으로 옮겼다. 하위 절은 다음과 같다.

- 12.1 웹 서버를 마지막에 띄우는 이유
- 12.2 `/api/health`는 Ollama를 확인하지 않는다
- 12.3 Ollama는 WSL 세션마다 다시 띄운다

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `docs/environment-setup.md` | 수정 | 12절 신설(재시작 절차·상태 점검 사각지대), 기존 12절을 13절로 이동 |

코드 변경 없음.

## 6. 주요 결정과 이유

### 6.1 상태 점검에 Ollama를 넣지 않았다

넣는 것이 옳아 보이지만, 시작 시 LLM을 호출하면 서버 기동이 모델 적재 시간만큼 늦어지고
모델이 내려가 있을 때 기동 자체가 실패한다. 지금은 **판별 방법을 문서에 적는 것**으로 두고,
점검 항목 추가는 별도 판단으로 남긴다. 담당자 확인 없이 기동 동작을 바꾸지 않는다.

### 6.2 컨테이너를 다시 만들지 않았다

`docker run` 을 다시 하면 이름 충돌이 나거나 새 익명 볼륨이 생긴다. 8.1절에 이 환경의
`neo4j-db` 가 이미 익명 볼륨을 쓰고 있다고 기록돼 있어, 재생성은 데이터 연결을 잃을 위험이
있다. `docker start` 로 충분함을 실측으로 확인했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| Neo4j 재기동 | `docker start neo4j-db` | `Up`, 포트 7474·7687 노출 |
| Neo4j 접속·개수 | `uv run python -m kg_builder.neo4j_ingest check-connection` | `Connection: OK`, Kernel 2026.06.0, nodes=1518 / relationships=3260 / evidence=511 |
| bundle·명세 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS; constraints=22, indexes=7 |
| Ollama 기동 | `curl -s http://127.0.0.1:11434/api/tags` | 모델 `qwen2.5-coder:14b` 응답 |
| 웹 서버 | `curl -s http://127.0.0.1:8501/api/health` | `service_ready: true` |
| 단위·계약 테스트 | `uv run pytest -q` | 222 passed, 6 skipped, 342 subtests passed |
| 복구 후 동작 | 실서버 `POST /api/ask` | `ㅇ런아러` → `OUT_OF_SCOPE`, `과목 알려줘` → 3단계 연쇄 후 답변, `캡스톤 과목 알려줘` → 답변 |
| 웹 서버 자동 재접속 여부 | **미실행** | 코드(`ChatState.open()`)를 읽어 재시도 없음을 확인. 런타임 재현은 하지 않았다 |

## 8. 발견된 문제와 위험

- `/api/health`가 Ollama를 확인하지 않는다. 문서로만 보완했고 코드는 그대로다.
- Ollama가 서비스로 등록돼 있지 않아 WSL 세션마다 수동 기동이 필요하다.
- 이 환경의 `neo4j-db`는 여전히 익명 볼륨을 사용한다(8.1절에 2026-08-10 기록). 컨테이너를
  재생성해야 할 일이 생기면 데이터 연결을 먼저 확인해야 한다.
- 임시 작업 파일이 지워지는 경우가 있어 측정 스크립트를 다시 만들어야 했다. 측정에 쓰는
  스크립트는 저장소에 남기지 않는 것이 원칙이라 이 비용은 감수한다.

## 9. 남은 작업

- 상태 점검에 LLM 항목을 넣을지 판단 (기동 지연·기동 실패와의 맞바꿈).
- 0007·0008에서 이월된 항목: 렌더 보장 테스트, 선택지 결과 0건 판정, `course_code` 외
  식별자 대조, 화면 스크립트 문법 검사 수단.
- 담당자 화면 실측 후 PR #29 갱신, PR #28 리뷰 (수칙 6.1).

## 10. 다음 작업 제안

1. 담당자가 복구된 화면에서 0007·0008 변경분을 확인한다.
2. 확인 뒤 PR #29에 3차 절을 더한다.
3. 상태 점검에 LLM을 넣을지 담당자와 정한다.
