# 0001. main·evaluate-question 브랜치 분석 및 에이전트 수칙 기록

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 황대겸 |
| 사용 에이전트 | Claude Code (Opus 5) |
| 작업 브랜치 | `main` (읽기 분석 및 미추적 파일 생성만 수행) |
| 관련 커밋 | 없음 (분석 시점 HEAD `5cbde0a`) |
| 관련 Issue/PR | 없음 |
| 작업 상태 | 완료 |

## 1. 작업 목적

현재 `main` 브랜치의 구현 범위와 공백을 파악하고, `docs/evaluate-question` 브랜치의 챗봇 평가 질문셋이 현재 지식그래프로 답변 가능한 범위인지 확인한다. 그리고 이후 AI 에이전트가 이 저장소에서 지켜야 할 작업 수칙을 공식 파일로 기록한다.

## 2. 요청 내용 요약

- 현재 `main` 브랜치 저장소를 분석한다.
- 분석 대상을 `main`과 `docs/evaluate-question` 두 브랜치로 한정한다.
- 담당자가 황대겸임을 전제로 기록한다.
- 에이전트가 지켜야 할 수칙을 `AGENTS.md` 같은 공식 방식으로 저장한다.
- 작업 단위마다 AI 시뮬레이션 로그를 작성하고, 환경 변경이 있으면 `docs/environment-setup.md`를 갱신한다.

## 3. 작업 전 상태

- 브랜치는 `main`, HEAD는 `5cbde0a`, `git status`는 clean이었다.
- 루트 `AGENTS.md`는 1바이트 빈 파일이며 `.gitignore` 10행으로 추적 제외 상태였다.
- `CLAUDE.md`는 없었다.
- `docs/ai-simulation-logs/hwang-daegyeom/`에는 로그가 없고 다음 번호는 `0001`이었다.
- 원격 브랜치는 `main` 외 8개가 존재했다.

## 4. 수행한 작업

- `main` 브랜치의 파일 구성, 구현된 모듈과 0바이트 골격 파일을 구분해 조사했다.
- `validate`와 단위 테스트를 실행해 현재 코드가 실제로 동작하는 범위를 확인했다.
- Verified bundle과 온톨로지 명세의 라벨·관계 타입·개수를 집계했다.
- `origin/docs/evaluate-question`의 커밋 수, 변경 파일과 질문셋 내용을 확인했다.
- 질문셋 50문항 중 현재 온톨로지로 답변 불가한 항목을 실제 데이터 조회로 검증했다.
- 에이전트 수칙을 `CLAUDE.md`에 작성하고 `AGENTS.md`를 이 파일을 가리키는 포인터로 정리했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `CLAUDE.md` | 생성 | 에이전트 작업 수칙 (Git 추적 제외, 로컬 전용) |
| `AGENTS.md` | 삭제 | 빈 파일이었고 역할이 `CLAUDE.md`와 중복되어 제거 |
| `.gitignore` | 수정 | 10행의 추적 제외 대상을 `AGENTS.md`에서 `CLAUDE.md`로 변경 |
| `docs/ai-simulation-logs/hwang-daegyeom/0001-main-branch-analysis-and-agent-rules.md` | 생성 | 이 로그 |
| `docs/ai-simulation-logs/hwang-daegyeom/README.md` | 수정 | 로그 목록과 다음 번호 갱신 |

## 6. 주요 결정과 이유

- 수칙 파일을 `CLAUDE.md` 하나로 통일했다. Claude Code가 세션 시작 시 자동으로 읽는 공식 프로젝트 메모리 파일이다.
- 담당자 지시에 따라 `AGENTS.md`를 삭제하고 `.gitignore`의 추적 제외 대상을 `CLAUDE.md`로 바꿨다. 수칙 파일은 로컬 전용으로 유지한다.
- 수칙 파일이 추적되지 않으므로 팀 전체에 적용할 규칙은 `docs/` 아래 추적 대상 문서에 반영한다는 원칙을 `CLAUDE.md`에 명시했다.
- 분석 범위를 `main`과 `docs/evaluate-question`으로 한정했다. 나머지 추출 파이프라인 브랜치는 접근 방식이 서로 다르고 병합 결정이 선행돼야 하므로 이번 로그에서 다루지 않았다.
- README의 구조 설명을 신뢰 근거로 쓰지 않고 실제 파일을 기준으로 판단했다. 두 내용이 일치하지 않기 때문이다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| bundle 사전 검증 | `uv run python -m kg_builder.neo4j_ingest validate` | PASS — 노드 1,518 / 관계 3,260 / Evidence 511 / 제약조건 22 / 인덱스 7 |
| 단위 테스트 | `uv run pytest -q` | 14 passed, 2 subtests passed |
| 구현 규모 | `wc -l src/kg_builder/*.py` | `config.py` 71, `neo4j_schema.py` 120, `neo4j_ingest.py` 393, `graph_bundle.py` 478 (합계 1,062줄) |
| 0바이트 골격 확인 | `wc -l` 및 파일 크기 | `main.py`, `config/settings.py`, `src/database/neo4j_client.py`, `src/kg_builder/builder.py`, `src/decision_engine/evaluator.py`, `tests/test_neo4j.py` 모두 0줄 |
| 명세 규모 | `ontology_spec.json` 파싱 | 라벨 26, 관계 타입 31, identity rule 22, 통제어휘 20 |
| 브랜치 비교 | `git rev-list --left-right --count main...origin/docs/evaluate-question` | 브랜치가 1커밋 앞, 4커밋 뒤 |
| 질문셋 내용 | `git show origin/docs/evaluate-question:eval/question-set-v1.md` | 102줄, 50문항, 7대분류 |
| 학생 관련 라벨 부재 | 명세 라벨 목록에서 `student`/`enroll`/`transcript` 계열 검색 | 해당 라벨 없음 |
| 정원·시간표 속성 부재 | 명세 전체 속성명에서 `capacity`/`quota`/`timetable`/`time_slot` 검색 | 해당 속성 없음 |
| 영어 면제 규칙 상태 | Verified bundle의 `ExemptionRule` 노드 조회 | `college-english-waiver`는 `REVIEW_REQUIRED`, 나머지 2건은 `VERIFIED` |
| 영어 면제 기준값 | `Condition` 노드 9건 조회 | TOEIC 700, TOEIC Speaking 130, TOEFL iBT 79, TEPS 494, New TEPS 264, OPIc IM1, G-TELP L2 65 / L3 85, FLEX 630 |

## 8. 발견된 문제와 위험

- `main`은 온톨로지 정의, Verified 기준 데이터, Neo4j 멱등 적재까지만 구현돼 있다. 파이프라인 앞단(PDF 추출)과 뒷단(추론 엔진)은 0바이트 골격이다. README는 이 파일들이 동작하는 것처럼 서술하고 있어 오해 소지가 있다.
- README의 프로젝트 구조 절에 `data/verified/`와 실제 구현 모듈 4개가 빠져 있다.
- `src/kg_builder/neo4j_ingest.py`의 `verify_representative_facts()`는 2026학년도 컴퓨터공학과 데이터에 하드코딩돼 있다. 다른 연도·학과 bundle에서는 실패한다.
- 설정 체계가 이원화돼 있다. `config/settings.py`는 0바이트이고 실제 설정은 `src/kg_builder/config.py`가 담당한다. `config/`는 `pyproject.toml`의 `package-dir = {"" = "src"}` 범위 밖이다.
- `src/database/neo4j_client.py`는 비어 있고 `neo4j_ingest.py`가 드라이버를 직접 생성한다. README가 설명한 계층 구조가 실제로는 적용되지 않았다.
- `metadata.source_document`가 참조하는 원본 PDF(`2026 교육과정(교양이수요건+컴공교육과정).pdf`, SHA-256 `8ee5ee9d…`)가 저장소와 로컬 `data/raw/` 어디에도 없다. Evidence의 원문 대조 재검증이 현재 불가능하다.
- 평가 질문셋 50문항 중 상당수가 현재 KG로 확정 답변할 수 없다. 확인된 항목은 다음과 같다.

| 대분류 | 문항 수 | 상태 | 근거 |
|---|---|---|---|
| 1. 현재 이수 내역 분석 | 8 | 답변 불가 | 학생·수강내역 계열 라벨이 명세에 없음 |
| 4. 영어 면제와 졸업인증 | 6 | 확정 답변 불가 | `college-english-waiver`가 `REVIEW_REQUIRED` (기준값 `Condition` 9건은 적재됨) |
| 6. 학년·학기와 수강신청 중 41·44번 | 2 | 답변 불가 | 정원·시간표 속성이 명세에 없음 |

- 추출 파이프라인 브랜치 7개는 `main`과 6~10커밋 갈라져 있고 접근 방식이 서로 다르다. 방식 선택 결정이 없으면 병합할 수 없다.

## 9. 남은 작업

- 평가 질문셋 확정 후 답변 가능 범위와 부족 항목을 다시 대조한다.
- README의 프로젝트 구조 절과 실제 트리를 일치시킨다.
- 학생·수강내역 모델링 여부를 결정한다. 결정 없이는 평가 질문셋 1번 대분류를 처리할 수 없다.
- `college-english-waiver`의 `REVIEW_REQUIRED` 해제 조건을 정한다.
- 원본 PDF 보관 방식을 정한다. 현재 재검증 경로가 없다.
- `CLAUDE.md`가 추적 제외이므로, 팀 공통으로 강제해야 하는 수칙은 추적 대상 문서로 옮길지 결정한다.

## 10. 다음 작업 제안

평가 질문셋이 확정되면 문항별로 필요한 노드·관계·속성을 역산해 온톨로지 공백 목록을 만들고, 학생 수강내역 모델링과 `decision_engine` 설계를 같은 단위에서 진행한다.
