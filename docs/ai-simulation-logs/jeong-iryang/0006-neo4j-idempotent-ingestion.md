# 0006. Neo4j V0.2 멱등 적재 구현

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-10 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/neo4j-ingestion` |
| 관련 커밋 | 없음 |
| 관련 Issue/PR | PR #9 병합 이후 작업 |
| 작업 상태 | 완료 |

## 1. 작업 목적

온톨로지 V0.2와 2026 Verified KG bundle을 Neo4j에 적재하기 위한 로컬 연결 설정, 명세 기반 사전 검증, 제약조건·인덱스, 멱등 batch 적재기와 검증 절차를 구현한다.

## 2. 요청 내용 요약

PR #9 병합 결과를 최신 `main`에 반영한 뒤 전용 브랜치에서 공식 Neo4j Driver 기반 적재 CLI를 구현한다. 빈 DB 안전 검사 후 같은 bundle을 두 번 적재하고 노드·관계·Evidence 개수가 불변인지 확인하도록 설계한다. 자연어 질의, LLM, 챗봇은 범위에서 제외한다.

## 3. 작업 전 상태

- PR #9는 2026-08-10에 병합됐다.
- 로컬 `main`을 merge commit `84e478d`까지 fast-forward했다.
- `feat/jeongiryang/neo4j-ingestion` 브랜치를 새로 생성했다.
- 기존 `pyproject.toml`, `.env.example`, `ontology/schema.cypher`와 핵심 Python 파일은 0바이트 골격이었다.
- Python은 3.12.3, uv는 0.11.32였다.
- Neo4j 접속 환경변수와 로컬 `.env`는 없었다.

## 4. 수행한 작업

- `neo4j` 공식 Driver와 `python-dotenv`를 uv 의존성으로 추가하고 잠금 파일을 생성했다.
- 최종 게시 전 검증 명령을 재현할 수 있도록 `pytest`를 개발 전용 dependency group에 추가했다.
- 비로컬 URI, 빈 비밀번호와 누락 환경변수를 거부하는 설정 모듈을 작성했다.
- 온톨로지·Verified bundle의 ID, endpoint, 속성, 통제어휘, Evidence, Neo4j 속성 타입과 병렬 관계를 검증하도록 구현했다.
- identity rule 기반 고유 제약조건 22개와 조회 인덱스 7개를 생성했다.
- 노드 라벨 조합과 실제 endpoint ID 속성별 batch `MERGE` 적재를 구현했다.
- 적재 전 빈 DB 검사, 첫 적재 검증, 같은 bundle 두 번째 적재와 개수 불변 검증을 `load` 명령에 포함했다.
- 대표 데이터 검증 Cypher와 운영 절차를 문서화했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `pyproject.toml` | 수정 | Python 범위와 프로젝트·의존성 설정 |
| `uv.lock` | 생성 | 재현 가능한 의존성 잠금 |
| `.env.example` | 수정 | 비밀값 없는 Neo4j 환경변수 예시 |
| `.gitignore` | 수정 | setuptools 로컬 생성물 `*.egg-info/` 제외 |
| `src/kg_builder/config.py` | 생성 | 환경변수와 로컬 URI 안전 검사 |
| `src/kg_builder/graph_bundle.py` | 생성 | 온톨로지 기반 bundle 사전 검증 |
| `src/kg_builder/neo4j_schema.py` | 생성 | 제약조건·인덱스 생성 및 적용 |
| `src/kg_builder/neo4j_ingest.py` | 생성 | CLI, 연결, 적재, 멱등·대표 사실 검증 |
| `ontology/schema.cypher` | 수정 | 재현 가능한 정적 스키마 29문장 |
| `tests/test_graph_bundle.py` | 생성 | bundle·설정·Cypher 안전 단위 테스트 |
| `docs/neo4j-ingestion.md` | 생성 | 설치·적재·검증 운영 문서 |
| `README.md` | 수정 | Neo4j 적재 문서와 최소 명령 링크 |

## 6. 주요 결정과 이유

- 관계는 Verified bundle에 동일 type·endpoint 병렬 관계가 없음을 확인한 뒤 endpoint와 type을 `MERGE` 키로 사용했다. 병렬 관계가 추가되면 조용히 병합하지 않고 사전 검증에서 실패한다.
- `Rule` 하위 라벨은 `Rule.rule_id` 고유 제약조건을 공유하므로 하위 라벨별 중복 제약조건을 만들지 않았다.
- `load`는 데이터베이스가 비어 있을 때만 시작하고 내부에서 같은 bundle을 두 번 적재한다. 기존 데이터 자동 삭제나 `--clear`는 제공하지 않는다.
- null은 Neo4j에서 미존재 속성으로 처리하고 숫자 0과 구분한다. 중첩 map을 문자열로 변환하지 않는다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| Python | `uv run python --version` | Python 3.12.3 |
| 의존성 | `uv tree` | 런타임 `neo4j 6.2.0`, `python-dotenv 1.2.2`; 개발 `pytest 9.1.1` |
| bundle | `uv run python -m kg_builder.neo4j_ingest validate` | 노드 1,518, 관계 3,260, Evidence 511 통과 |
| 스키마 | 정적 파일과 Python 생성 결과 단위 테스트 | 제약조건 22, 인덱스 7 일치 |
| 단위 테스트 | `uv run python -m unittest discover -s tests -v` | 14개 통과 |
| unresolved 거부 | unresolved 파일을 `validate --data`로 전달 | 종료 코드 2로 거부 |
| 실제 연결 | `check-connection` 및 읽기 Cypher | Neo4j Kernel 2026.06.0, 로컬 endpoint, `neo4j` DB 연결 성공 |
| 적재 전 안전 검사 | 노드·관계 count | 노드 0, 관계 0 확인 |
| 실제 스키마 | `apply-schema`, `SHOW CONSTRAINTS`, `SHOW INDEXES` | 고유 제약조건 22, 프로젝트 RANGE 인덱스 7 |
| 1차 적재 | `load` 내부 첫 실행 | 노드 1,518·관계 3,260·Evidence 511 생성 |
| 2차 적재 | `load` 내부 두 번째 실행 | 생성 0, 노드 1,518·관계 3,260 기존 매칭, 개수 불변 |
| 대표 Cypher | `verify` 및 상세 읽기 쿼리 | 11개 필수 항목 전체 PASS |
| 의존성 계약 | 기존 Python import와 0바이트 골격 조사 | 현재 main에 전처리 패키지를 요구하는 실행 코드 없음 |

## 8. 발견된 문제와 위험

- 첫 `uv add`에서 `uv_build`가 기존 `src/__init__.py`를 namespace-package 규칙 위반으로 거부했다. 기존 파일을 삭제하지 않고 `setuptools` build backend로 변경한 뒤 정상 동기화했다.
- `uv sync --locked`는 기존 빈 `pyproject.toml`에 선언되지 않았던 과거 로컬 `.venv` 패키지를 제거하고 잠금 파일의 최소 4개 패키지로 환경을 동기화했다. `.venv`는 Git 제외 상태다.
- 최초 서버 버전 진단 쿼리는 `dbms.components()`가 여러 행을 반환해 단일 행 검사에 실패했다. DB 변경은 없었으며 컴포넌트를 집계한 뒤 Neo4j Kernel 버전을 선택하도록 수정했다.
- DB가 비어 있지 않으면 적재기는 기존 데이터를 삭제하지 않고 중단한다.
- Neo4j 2026의 `SHOW CONSTRAINTS` 타입은 `NODE_PROPERTY_UNIQUENESS`이며, 기본 LOOKUP 인덱스 2개는 프로젝트가 생성한 RANGE 인덱스 7개와 별도로 존재한다.
- 현재 main의 기존 `main.py`, 설정·DB·builder·evaluator·기존 테스트는 모두 0바이트 골격이다. Docling, PyMuPDF, Pandas, Streamlit import가 없어 현 단계에서 근거 없는 optional dependency group은 추가하지 않았다.

## 9. 남은 작업

- 구현 변경을 검토한 뒤 커밋하고 일반 PR로 게시한다.
- PR 이후 학생 예상 질문을 기반으로 Cypher 질의 계층과 Evidence 포함 응답 API를 설계한다.

## 10. 다음 작업 제안

실제 적재가 완료되면 학생 예상 질문을 기반으로 파라미터화된 Cypher 질의 계층과 Evidence 포함 응답 API를 구현한다.
