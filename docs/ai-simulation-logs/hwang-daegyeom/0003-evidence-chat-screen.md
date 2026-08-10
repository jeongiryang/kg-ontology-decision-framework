# 0003. 학사규정 근거 챗봇 화면 구현

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 황대겸 |
| 확인자 | 정이량 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | `feat/hwangdaegyeom/evidence-chat` |
| 분기 기준 | 최초 `origin/feat/jeongiryang/query-evidence-api` (PR #12) → 작업 중 PR #12가 `main`에 squash merge(`eb0409a`)되어 `origin/main` 위로 리베이스 |
| 관련 커밋 | `7e5a60e`, `d72e797` |
| 관련 Issue/PR | PR #12 후속 단계 |
| 작업 상태 | 완료 |

## 1. 작업 목적

Verified 지식그래프와 읽기 전용 질의 계층 위에 최종 사용자 화면을 만든다. 질문 입력, 처리 과정, 답변과 근거를 3단계 화면으로 분리하고, 대기 시간 동안 어떤 처리를 하고 있는지 텍스트로 드러내 디버깅이 가능하게 한다. 답변에는 근거 PDF 페이지와 페이지 내 위치를 함께 표시한다.

## 2. 요청 내용 요약

- 1단계는 질문 입력 프롬프트만 보여 준다.
- 2단계로 넘어가 처리 과정을 상세히 텍스트로 표시하고 회전 로딩 표시로 대기 시간을 해소한다.
- 3단계에서 답변과 근거를 보여 준다. 근거 PDF 페이지를 표시하고, 여러 페이지를 참조하면 참조한 페이지만 띄우며, 페이지 내 특정 부분만 참조하면 그 위치를 빨간 박스 등으로 표시한다.
- 단계별로 지금 무슨 작업을 하는지 뜨게 해 디버깅이 가능해야 한다.
- 브랜치를 새로 만들어 작업하고, `CLAUDE.md`는 올리지 않는다.
- 화면을 다 만들면 작업을 중단하고 담당자 확인을 받은 뒤 draft PR을 생성한다.
- 담당자는 황대겸, 확인자는 정이량으로 기재한다.

## 3. 작업 전 상태

- `main` HEAD는 `5cbde0a`였고 챗봇 화면 코드가 없었다.
- 정이량의 질의·Evidence 계층은 PR #12 draft 상태로 `main`에 병합되지 않았다. 해당 브랜치는 `main` + 커밋 1개였다.
- PR #12 본문의 미구현 범위에 `FastAPI·Streamlit·챗봇 UI`가 명시돼 있었다.
- 로컬 Neo4j에는 노드 1,518 / 관계 3,260 / Evidence 511이 적재된 상태였다(로그 0002).
- `Evidence.bbox`는 511건 전부 null이었다.
- 발췌 PDF 원본은 저장소와 로컬 어디에도 없었다.
- 의존성에 웹 프레임워크와 PDF 처리 라이브러리가 없었다.

## 4. 수행한 작업

- PR #12 분기에서 `feat/hwangdaegyeom/evidence-chat` 브랜치를 만들었다.
- `starlette`, `uvicorn`, `pymupdf`를 런타임 의존성으로 추가하고 잠금 파일을 갱신했다.
- 자연어 질문을 지원 6개 Intent로만 바꾸는 규칙 기반 플래너를 구현했다. LLM 플래너를 끼울 수 있는 프로토콜을 함께 정의했다.
- 8단계 진행 이벤트를 생성하는 파이프라인을 구현했다. 각 단계는 `done`, `skipped`, `failed`로 끝나고 상세 사유를 함께 낸다.
- 질의 응답을 한국어 문장과 항목 목록으로 바꾸는 답변 구성기를 구현했다. 통제어휘 코드를 한국어 라벨로 변환했다.
- `Evidence.raw_text`를 검색어로 쪼개 PyMuPDF 텍스트 검색으로 페이지 내 좌표를 찾고 정규화하는 모듈을 구현했다.
- SSE로 단계를 스트리밍하고 PDF 페이지를 PNG로 렌더링하는 Starlette 앱을 구현했다.
- 3단계 화면(질문 입력 / 처리 과정 / 답변과 근거)을 정적 HTML·CSS·JS로 구현했다. 외부 CDN과 웹폰트를 쓰지 않고 라이트·다크 테마를 모두 지원한다.
- 단위 테스트 46개를 추가하고 합성 PDF로 강조 좌표 계산을 검증했다.
- 실행·디버깅 문서를 작성하고 README에 링크를 추가했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/evidence_chat/__init__.py` | 생성 | 패키지 선언 |
| `src/evidence_chat/planner.py` | 생성 | 질문 정규화, 규칙 기반 Intent 매핑, LLM 플래너 프로토콜 |
| `src/evidence_chat/pipeline.py` | 생성 | 8단계 진행 이벤트 생성과 실패·건너뜀 처리 |
| `src/evidence_chat/answer.py` | 생성 | 질의 응답 → 한국어 답변과 판정 라벨 |
| `src/evidence_chat/pdf_evidence.py` | 생성 | PDF 탑재 검사, 페이지 렌더, 강조 좌표 계산 |
| `src/evidence_chat/server.py` | 생성 | Starlette 앱, SSE 스트리밍, PDF 페이지 엔드포인트 |
| `src/evidence_chat/static/index.html` | 생성 | 3단계 화면 구조 |
| `src/evidence_chat/static/app.css` | 생성 | 라이트·다크 테마 스타일 |
| `src/evidence_chat/static/app.js` | 생성 | 화면 전환, SSE 수신, 근거 박스 겹치기 |
| `tests/test_evidence_chat.py` | 생성 | 플래너·답변·PDF·파이프라인·엔드포인트 단위 테스트 |
| `pyproject.toml` | 수정 | `starlette`·`uvicorn`·`pymupdf` 추가, `evidence_chat` 정적 파일 package-data |
| `uv.lock` | 수정 | 의존성 잠금 갱신 |
| `docs/evidence-chat.md` | 생성 | 실행, 화면 구성, 단계별 디버깅, 안전 정책, 제한사항 |
| `README.md` | 수정 | 챗봇 실행 명령과 문서 링크 추가 |
| `docs/ai-simulation-logs/hwang-daegyeom/0003-evidence-chat-screen.md` | 생성 | 이 로그 |
| `docs/ai-simulation-logs/hwang-daegyeom/README.md` | 수정 | 로그 목록과 다음 번호 갱신 |

질의 계층, 온톨로지 명세, Raw·Verified 데이터는 수정하지 않았다. `CLAUDE.md`는 `.gitignore` 대상이라 커밋에 포함되지 않는다.

## 6. 주요 결정과 이유

- 정이량의 `query_service`를 재구현하지 않고 그대로 사용하기 위해 PR #12 분기에서 브랜치를 냈다. 해당 분기는 `main` + 커밋 1개여서 현재 `main`의 모든 파일을 포함한다. PR base를 PR #12 브랜치로 두면 diff에 이번 변경만 남는다.
- Streamlit 대신 Starlette + SSE를 골랐다. 화면 전환, 단계 실시간 스트리밍, PDF 이미지 위 좌표 겹치기를 모두 제어해야 했고 Streamlit은 이 세 가지를 동시에 다루기 어렵다.
- LLM을 붙이지 않았다. API 키가 없는 상태에서 LLM 호출 코드를 넣으면 검증하지 못한 경로가 남는다. 대신 플래너 교체 지점을 정의하고, 진행 화면 2단계에 실제 사용된 플래너 이름을 출력해 `LLM이 변환 중`이라는 거짓 표시가 생기지 않게 했다.
- 진행 단계에 실행되는 Cypher 전문과 최종 파라미터를 그대로 노출했다. 사전 등록된 읽기 전용 템플릿이고 사용자 값은 파라미터로만 전달되므로 노출해도 질의 주입 경로가 생기지 않으며, 디버깅 가치가 크다.
- 강조 좌표를 `Evidence.bbox`에서 읽지 않고 텍스트 검색으로 계산했다. `bbox`가 511건 모두 null이기 때문이다. `bbox`가 채워지면 이 단계를 저장된 좌표로 대체할 수 있게 함수를 분리했다.
- 강조를 서버에서 이미지에 그리지 않고 정규화 좌표를 응답에 담아 프런트엔드가 겹치게 했다. 근거 카드와 박스를 상호 강조하려면 좌표가 클라이언트에 있어야 한다.
- PDF가 없을 때 예외를 던지지 않고 `skipped` 상태와 이유를 반환하도록 했다. 원본 PDF가 아직 없는 현 상태에서도 나머지 화면이 동작해야 하기 때문이다.
- 과목명 사전을 Neo4j 조회가 아니라 로컬 Verified bundle에서 만들었다. 사전 조회용 Cypher를 새로 추가하면 allowlist 정책을 깨야 한다.
- 서버 기본 바인딩을 `127.0.0.1`로 고정했다. 인증이 없는 로컬 개발용 화면이다.
- 화면 이름을 `학사규정 근거 챗봇`, 패키지를 `evidence_chat`으로 정했다. 온톨로지의 `Evidence` 노드와 정이량 계층의 Evidence 응답 정책을 이름이 그대로 이어받게 해, 세 계층의 어휘를 일치시켰다. 커밋 이전에 결정해 개명 비용이 없었다. 후보였던 `유리상자 챗봇`은 도메인이 이름에서 드러나지 않아, `CWNU 학사 내비게이터`는 현재 지원 범위(2026학년도·컴퓨터공학과·Intent 6개)보다 이름이 커서 제외했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| 컴파일 | `uv run python -m compileall -q src` | 통과 |
| 의존성 잠금 | `uv lock --check` | 통과 (18 packages) |
| 전체 테스트 | `uv run pytest -q` | 75 passed, 1 skipped, 38 subtests passed |
| 챗봇 테스트 | `uv run pytest -q tests/test_evidence_chat.py` | 46 passed, 15 subtests passed |
| 플래너 매핑 | 예시 질문 6건 → Intent·파라미터 | 6건 모두 의도한 Intent로 매핑, 계약 검증 통과 |
| 범위 밖 질문 거부 | `오늘 점심 뭐 먹지`, `장학금 신청 방법` | `PlannerError`로 거부, 1단계 `failed` 후 이후 단계 `skipped` |
| 실 Neo4j 파이프라인 | 예시 질문 6건 전체 실행 | 6건 모두 `ANSWERABLE`, 8단계 정상 진행 |
| 서버 기동 | `uv run python -m evidence_chat.server --port 8531` | Neo4j 연결 및 PDF 상태 로그 출력 후 정상 기동 |
| 화면·정적 파일 | `GET /`, `GET /static/app.js`, `GET /static/app.css` | 각각 HTTP 200 |
| 상태 응답 | `GET /api/health` | 연결 상태, PDF 상태, 예시 질문 반환 |
| SSE 스트리밍 | `POST /api/ask` | HTTP 200, `text/event-stream`, 15,632 bytes, `step`→`result`→`end` 순서 확인 |
| PDF 없음 경로 | PDF 미탑재 상태 실행 | 7단계 `skipped` + 사유 표시, 나머지 단계 정상, `GET /api/pdf/page/1.png` → 404 |
| PDF 있음 경로 | 합성 19쪽 PDF를 `CURRICULUM_PDF_PATH`로 지정 | 7단계 `done`, 강조 탐색 성공 9/9건, 페이지 렌더 HTTP 200 |
| 페이지 범위 검사 | `GET /api/pdf/page/25.png` (19쪽 PDF) | HTTP 404 |
| 해시 불일치 경고 | 합성 PDF(해시 다름) | `sha256_matches=false`와 경고 문구를 상태·진행 단계에 표시 |
| 강조 좌표 정확도 | 페이지 17에 박스를 그려 이미지로 확인 | 전공필수 7과목 행에 정확히 위치 (고급자료구조·컴퓨터구조·알고리즘·소프트웨어공학·데이터베이스이론·운영체제·데이터통신) |
| 참조 페이지만 표시 | 전공필수 질의 근거 그룹 | p.17 7건, p.18 2건만 반환. 나머지 17개 페이지는 응답에 없음 |
| 요청 본문 제한 | 20,000자 질문 전송 | HTTP 413 |
| 잘못된 JSON | 깨진 본문 전송 | HTTP 400 |
| Neo4j 미연결 | 연결 실패 상태로 질문 전송 | HTTP 503, 화면 상단에 원인 표시 |
| Neo4j 데이터 불변 | 검증 전후 `check-connection` | 노드 1,518 / 관계 3,260 / Evidence 511 동일 |

실 Neo4j 조회 결과는 다음과 같았다.

| 질문 | 판정 | 답변 | 근거 |
|---|---|---|---:|
| 2026학번 교양은 최소 몇 학점? | 확정 답변 | 교양 최소 34학점 | 1건 (p.1) |
| 균형교양 이수요건? | 확정 답변 | 균형교양 최소 12학점 | 2건 (p.1) |
| 편입생 교양 면제 가능? | 확정 답변 | 면제 대상 | 1건 (p.1) |
| 자료구조는 몇 학년 몇 학기? | 확정 답변 | 2학년 1학기, 3학점 | 1건 (p.17) |
| 컴퓨터공학과 전공필수? | 확정 답변 | 9과목 21학점 | 9건 (p.17, p.18) |
| 자료구조 이수구분? | 확정 답변 | 전공선택 | 1건 (p.17) |

## 8. 발견된 문제와 위험

- 첫 서버 기동이 `TypeError: Starlette.__init__() got an unexpected keyword argument 'on_startup'`으로 실패했다. Starlette 1.6에서 `on_startup`·`on_shutdown`이 제거됐다. `lifespan` 컨텍스트 관리자로 교체해 해결했다.
- SSE 스트림이 완료된 뒤 서버 로그에 `anyio.BrokenResourceError`가 남았다. async generator 안에서 task group과 memory object stream을 들고 `yield`한 구조가 원인이었다. 워커 스레드 + 무제한 큐 + 센티넬 구조로 바꿔 해결했고 이후 재현되지 않았다.
- 답변 문장에 통제어휘 코드가 그대로 노출돼 `34CREDIT`으로 표시됐다. 단위 라벨 변환을 추가했다. 모르는 단위 코드는 임의로 바꾸지 않고 원문 그대로 노출한다.
- 검증용 셸에서 `pkill -f "evidence_chat.server"`가 자기 자신을 매칭해 셸이 종료됐다. 포트로 프로세스를 찾는 스크립트로 대체했다. 저장소에는 영향이 없다.
- 발췌 PDF 원본이 없어 실제 규정집 페이지에서의 강조 정확도는 검증하지 못했다. 합성 PDF로 좌표 계산 경로만 확인했다. 합성 PDF에는 한글 글리프가 렌더링되지 않아 영문 과목명 조각이 매칭됐다. 원본 PDF에서는 한글 조각 매칭이 동작해야 하며 이는 아직 미검증이다.
- `Evidence.bbox`가 전건 null이므로 강조는 텍스트 검색에 의존한다. 스캔 이미지 PDF에서는 박스가 생기지 않는다.
- PyMuPDF는 AGPL-3.0이다. 현재는 로컬 연구용이라 문제되지 않으나 배포 형태가 바뀌면 라이선스를 확인해야 한다.
- 질문 해석이 규칙 기반이므로 표현 변형에 약하다. 평가 질문셋 50문항 중 학생 개인 이수내역, 정원·시간표, 영어 면제 확정 답변은 데이터·스키마 공백으로 여전히 불가하다(로그 0001).
- 작업 중 PR #12가 `main`에 squash merge됐다. 분기 기준 커밋 `3c25a99`가 `main`의 조상이 아니게 되어 `git rebase --onto origin/main`으로 옮겼다. 충돌은 없었고 리베이스 후 테스트를 다시 통과했다.

## 9. 남은 작업

- 정이량이 draft PR을 검토한다. 확인 요청 항목은 PR 본문에 정리했다.
- 발췌 PDF를 확보해 실제 규정집 페이지에서 한글 조각 강조 정확도를 검증한다.
- `Evidence.bbox` 채우기 여부를 결정한다. 채우면 텍스트 검색 단계를 저장된 좌표로 대체한다.
- LLM 플래너를 붙일지, 붙인다면 어떤 모델과 키 관리 방식을 쓸지 결정한다.

## 10. 다음 작업 제안

평가 질문셋 50문항을 이 화면에 실제로 넣어 판정 분포를 측정한다. `확정 답변`, `추가 정보 필요`, `검토 보류`, `지원 범위 밖`, `해당 사실 없음` 비율을 표로 만들고, 규칙 기반 플래너가 놓치는 표현과 스키마 공백을 분리해 다음 확장 대상을 정한다.
