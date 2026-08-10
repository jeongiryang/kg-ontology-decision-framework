# 0002. Verified KG 로컬 Neo4j 적재 및 환경 문서 갱신

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 황대겸 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | `main` (적재 실행 및 문서 갱신) |
| 관련 커밋 | 없음 (실행 시점 HEAD `5cbde0a`) |
| 관련 Issue/PR | 없음 |
| 작업 상태 | 완료 |

## 1. 작업 목적

`main`에 이미 구현된 적재 CLI를 사용해 2026 Verified KG bundle을 담당자 로컬 Neo4j에 적재하고, 멱등성과 대표 학사 사실을 검증한다. 적재 과정에서 확인된 개발환경 차이를 `docs/environment-setup.md`에 반영한다.

## 2. 요청 내용 요약

- 새 스크립트를 만들지 않고 현재 만들어진 파이썬 스크립트를 활용해 Neo4j 적재를 수행한다.
- 적재가 완료되면 작업을 중단한다.
- 작업 단위 완료 후 AI 시뮬레이션 로그를 작성하고, 환경 변경 사항이 있으면 `docs/environment-setup.md`를 갱신한다.

## 3. 작업 전 상태

- 브랜치는 `main`, HEAD는 `5cbde0a`였다.
- `.env`가 없었고 `NEO4J_PASSWORD` 미설정으로 모든 DB 명령이 실패했다.
- Ubuntu 안에 `docker` 실행 파일이 없었다. Docker Desktop WSL Integration이 비활성 상태였다.
- Docker 데몬이 실행 중이 아니었다.
- 7687과 7474 포트에 리스닝이 없었다.
- Neo4j는 WSL에 네이티브 설치돼 있지 않았고 Java도 없었다.
- `neo4j-db` 컨테이너(`neo4j:2026.06.0`)가 2026-07-25 생성 상태로 존재했고 `Exited (255)`였다.
- 적재 대상 데이터베이스 `neo4j`는 노드 0, 관계 0이었다.

## 4. 수행한 작업

- Windows에서 Docker Desktop을 실행하고 데몬이 준비될 때까지 대기했다.
- WSL Integration이 비활성이라 `docker` 대신 `docker.exe`로 컨테이너를 운영했다.
- 기존 `neo4j-db` 컨테이너를 새로 만들지 않고 그대로 시작했다.
- 서버 기동 로그에서 `Started.`를 확인하고 WSL에서 `127.0.0.1`·`localhost`의 7687·7474 TCP 연결을 확인했다.
- Neo4j Browser(`http://localhost:7474`)를 열어 담당자가 비밀번호를 확인할 수 있게 했다.
- `.env`를 생성하고 권한을 `600`으로 제한한 뒤 담당자가 전달한 비밀번호를 반영했다.
- `check-connection`으로 서버 버전과 적재 전 개수를 확인했다.
- `load`로 스키마를 적용하고 같은 bundle을 두 번 적재해 멱등성을 확인했다.
- `verify`로 전체 개수와 대표 학사 사실을 재검증했다.
- 확인된 환경 차이를 `docs/environment-setup.md` 5.1절과 8.1절로 추가했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `docs/environment-setup.md` | 수정 | 5.1절 WSL Integration 비활성 환경의 `docker.exe` 대체 경로와 localhost 접속 검증 결과 추가, 8.1절 볼륨 구성 실제 확인 절차 추가 |
| `docs/ai-simulation-logs/hwang-daegyeom/0002-neo4j-verified-kg-local-load.md` | 생성 | 이 로그 |
| `docs/ai-simulation-logs/hwang-daegyeom/README.md` | 수정 | 로그 목록과 다음 번호 갱신 |
| `.env` | 생성 | 로컬 Neo4j 접속 환경변수, 권한 `600`, Git 추적 제외 |

`.env`의 값은 로컬 전용이며 이 로그에 기록하지 않는다. 저장소 추적 대상 코드와 데이터는 변경하지 않았다.

## 6. 주요 결정과 이유

- 기존 `neo4j-db` 컨테이너를 재생성하지 않고 시작했다. 이미 팀 문서 규격의 이미지 태그(`neo4j:2026.06.0`)를 사용하고 있었고, 재생성은 기존 볼륨 데이터를 잃는 결정이라 담당자 판단이 필요했다.
- 비밀번호를 추측하지 않고 담당자에게 요청했다. 인증 정보를 임의로 시도하거나 컨테이너 비밀번호를 재설정하지 않았다.
- WSL Integration을 활성화하지 않고 `docker.exe`로 진행했다. Docker Desktop 설정 변경은 이 작업 범위를 넘고, `docker.exe`로도 목적을 달성할 수 있음을 접속 검증으로 확인했기 때문이다. 다만 이는 대체 경로이므로 정식 해결책은 통합 활성화라는 점을 환경 문서에 명시했다.
- `NEO4J_URI`는 `neo4j://localhost:7687`을 사용했다. `docs/neo4j-ingestion.md` 3절과 `.env.example`의 표기를 따랐다. 적재기는 `bolt://`도 허용한다.
- 담당자의 중단 지시에 따라 적재와 검증까지만 수행하고 커밋·PR은 만들지 않았다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| Docker 데몬 | `docker.exe version --format '{{.Server.Version}}'` | 준비 완료 후 응답 확인 |
| 컨테이너 상태 | `docker.exe ps --filter name=neo4j-db` | `neo4j:2026.06.0`, Up, 7474·7687 published |
| 서버 기동 | `docker.exe logs neo4j-db` | `Started.` 확인 |
| WSL → localhost TCP | Python socket으로 `127.0.0.1`·`localhost`의 7687·7474 연결 | 4건 모두 연결 성공 |
| WSL 네트워킹 모드 | `wslinfo --networking-mode` | `nat` |
| HTTP 엔드포인트 | `http://localhost:7474` 조회 | HTTP 200, `neo4j_version 2026.06.0`, `community` |
| 비밀번호 미설정 시 동작 | `check-connection` (빈 `NEO4J_PASSWORD`) | `ERROR: Missing required Neo4j environment variables: NEO4J_PASSWORD`로 정상 거부 |
| 연결 확인 | `uv run python -m kg_builder.neo4j_ingest check-connection` | Connection OK, Neo4j Kernel 2026.06.0, 적재 전 노드 0·관계 0·Evidence 0 |
| 스키마 적용 | `load` 내부 `apply-schema` | 제약조건 22, 인덱스 7 |
| 1차 적재 | `load` 내부 첫 실행 | 노드 1,518 생성 / 관계 3,260 생성 (matched 0) |
| 2차 적재 | `load` 내부 두 번째 실행 | 생성 0, 노드 1,518·관계 3,260 매칭, `idempotency=PASS` |
| 적재 후 개수 | `uv run python -m kg_builder.neo4j_ingest verify` | 노드 1,518 / 관계 3,260 / Evidence 511 (bundle 기대값 일치) |
| 대표 학사 사실 | `verify` 출력 | 11개 항목 전체 PASS |

`verify`가 확인한 대표 사실은 다음과 같다.

| 항목 | 결과 |
|---|---|
| GEA8617 과목명 | 수식없는물리로보는세상 |
| GEA8617 편성·근거 | 2학기 GENERAL_ELECTIVE Offering 1건, VERIFIED Evidence 1건 |
| GEA8817 과목명 | 융합프로젝트Ⅰ |
| GEA8817 정정 추적 | `source_value=GEA8617` → `corrected_value=GEA8817`, 원문 Evidence 보존, Offering 0건 |
| 컴공 전공필수 편성 | 9과목 / 21학점 |
| 자료구조 개설 시기 | 2학년 1학기 |
| 교양 최소 총학점 | 34 |
| 균형교양 최소 학점 | 12 |
| 부전공 필수 과목 | 운영체제, 자료구조, 컴퓨터구조 |
| 실기 합계 정정 | 원문 12 / 정규화 14 양쪽 보존 |
| 일반 교양규칙 Evidence | 3건 |

## 8. 발견된 문제와 위험

- Ubuntu 안에 `docker`가 없어 문서 5절의 검증 명령(`docker version` 등)이 그대로는 실패한다. WSL Integration 비활성 환경의 대체 경로를 문서에 추가했으나, 근본 해결은 통합 활성화다.
- Docker Desktop 설치 위치가 문서가 가정한 `%ProgramFiles%`가 아니라 사용자 로컬 경로였다. 문서에는 두 경우를 모두 적었다.
- `neo4j-db` 컨테이너가 문서 8절 규격의 named volume(`neo4j_data`, `neo4j_logs`) 대신 익명 볼륨을 사용한다. 컨테이너를 재생성하면 데이터를 다시 연결하기 어렵다. 8.1절에 확인 절차와 현재 상태를 기록했다.
- `docker volume ls`에 어떤 컨테이너도 사용하지 않는 `neo4j-data` 볼륨이 남아 있다. 이름이 문서 규격과 비슷해 혼동 위험이 있다.
- `neo4j:latest` 컨테이너가 같은 포트를 점유하도록 설정된 상태로 남아 있다. `neo4j-db`와 동시에 실행할 수 없다.
- `check-connection`과 `counts()`는 빈 DB에서 `Evidence` 라벨 부재 경고(GQL `01N50`)를 출력한다. 동작 오류는 아니지만 출력이 길어 실제 결과를 가린다.
- 비밀번호가 대화로 전달됐다. `.env`에만 기록하고 문서·로그에는 남기지 않았으나, 대화 전사에는 남아 있으므로 공유 자료로 옮기지 않는다.
- 적재는 담당자 로컬 DB에서만 수행됐다. 다른 팀원 환경의 적재 상태를 보장하지 않는다.
- 커밋과 PR을 만들지 않았다. 문서 갱신과 로그가 아직 원격에 반영되지 않은 상태다.

## 9. 남은 작업

- Docker Desktop WSL Integration을 활성화하고 `docker` 명령 기준으로 환경을 정리한다.
- `neo4j-db`를 named volume 구성으로 재생성할지 결정한다. 재생성 시 기존 익명 볼륨 데이터 처리 방침을 함께 정한다.
- 사용하지 않는 `neo4j:latest` 컨테이너와 `neo4j-data` 볼륨 정리 여부를 결정한다.
- 문서 갱신과 이번 로그 2건을 작업 브랜치에 커밋하고 PR로 게시한다.

## 10. 다음 작업 제안

적재된 그래프를 대상으로 평가 질문셋 기반의 파라미터화된 Cypher 질의 계층을 설계한다. 이때 `VERIFIED` 노드와 `VERIFIED` Evidence만 확정 답변 근거로 사용하고, `REVIEW_REQUIRED` 항목은 답변에서 분리하는 규칙을 함께 구현한다.
