# KG Ontology Decision Framework

지식 그래프 및 온톨로지 기반의 의사결정 추론 프레임워크

---

## 1. 전체 파이프라인 흐름 

```text
[입력 데이터]
├── 학사 규정 (PDF)   ──> PDF 파서 / LLM 추출 ──┐
└── 수강 내역 (CSV)   ──> CSV 정형 변환기     ──┴─> [중간 가공 데이터 (JSON)]
                                                          │
                                                    builder.py (MERGE 쿼리)
                                                          │
                                                          ▼
                                                   [Neo4j 지식 그래프]
                                                          │
                                                    evaluator.py (추론 Engine)
                                                          │
                                                          ▼
                                              [최종 졸업 심사 결과 판정]
```

---

## 2. 프로젝트 구조

```text
kg-ontology-decision-framework/
├── .venv/                      # [가상환경] Python 3.12 독립 패키지 공간
├── .gitignore                  # [설정] Git 추적 제외 목록 파일
├── .env.example                # [환경변수 양식] API 키, DB 비밀번호 등의 설정 템플릿
├── pyproject.toml              # [패키지 관리] uv 패키지 의존성 정의 파일
├── README.md                   # [설명서] 프로젝트 개요 및 구조 안내 문서
├── main.py                     # [진입점] 파싱 -> KG 구축 -> 의사결정 추론 전체 실행 파일
│
├── assets/                     # [시각화/캡처] README 및 문서용 이미지, GIF 저장소
│
├── config/                     # [설정] 프로젝트 주요 설정 관리 폴더
│   ├── __init__.py             
│   └── settings.py             # .env 읽어서 Neo4j IP/비밀번호, LLM API 키 로드
│
├── data/                       # [데이터] 원본 및 가공 데이터 저장소
│   ├── raw/                    # 원본 데이터 (학사 규정 PDF, 학생/수강 목록 CSV)
│   └── processed/              # 파싱/정제 거친 중간 데이터 (JSON 등)
│
├── ontology/                   # [온톨로지] 지식 그래프의 청사진(설계도)
│   ├── schema.cypher           # Neo4j 속도 향상(인덱스) 및 중복 방지(제약조건) 쿼리
│   └── ontology_spec.json      # 노드(학생, 과목, 규정)와 간선(이수함, 필수지정) 정의서
│
├── src/                        # [핵심 로직] 실제 작동하는 파이썬 소스코드
│   ├── __init__.py             
│   ├── database/               # [DB 연동] Neo4j 접속 드라이버
│   │   ├── __init__.py
│   │   └── neo4j_client.py     # 파이썬에서 Neo4j로 Cypher 쿼리를 쏘아주는 실행기
│   │
│   ├── kg_builder/             # [KG 생성기] 데이터를 읽어 그래프로 구축하는 파이프라인
│   │   ├── __init__.py
│   │   └── builder.py          # CSV/PDF를 읽어 노드·간선으로 변환 후 Neo4j에 저장
│   │
│   └── decision_engine/        # [추론 엔진] 지식 그래프 기반 의사결정 로직
│       ├── __init__.py
│       └── evaluator.py        # Neo4j 그래프를 탐색하여 "졸업 가능 여부" 등 추론/판정
│
└── tests/                      # [검증] 코드 테스트 모듈
    ├── __init__.py
    └── test_neo4j.py           # Neo4j DB 연결 및 쿼리 동작 정상 여부 테스트
```


**Git 추적 제외 안내: .venv/(가상환경 디렉토리), .env(민감한 환경변수 설정), CSV/PDF/HWP 데이터 파일 등은 보안 및 용량 관리를 위해 .gitignore에 등록되어 저장소 추적 대상에서 제외.**

---

## 3. 디렉토리 및 파일 상세 설명

### 루트 파일 및 환경 설정
* **`.venv/`**: 프로젝트 전용 독립 파이썬 실행 공간. 외부 패키지와 버전 충돌을 방지함.
* **`.gitignore`**: Git에 올라가면 안 되는 비밀번호(`.env`), 대용량 데이터(`data/`), 가상환경(`.venv/`)을 차단함.
* **`.env.example`**: DB 접속 비밀번호 및 API 키 설정용 서식. 실제 값은 Git에 올라가지 않는 `.env`에 적어서 사용함.
* **`pyproject.toml`**: `uv` 패키지 매니저 전용 라이브러리 주문서. `neo4j`, `python-dotenv` 등의 의존성 버전을 고정함.
* **`README.md`**: 프로젝트의 설계 의도, 실행 방법, 구조 및 각 파일의 역할을 설명하는 안내 문서.
* **`main.py`**: 전체 프로그램 실행 진입점. 데이터 읽기부터 그래프 생성, 의사결정 추론까지 한 번에 실행함.
* **`assets/`**: README 및 보고서에 들어갈 Neo4j 그래프 캡처 이미지나 시각화 GIF 파일 보관소.

### `config/` (프로젝트 설정)
* **`settings.py`**: `.env`에 적힌 DB 주소, 계정, API 키를 읽어와 파이썬 코드 전체에서 안전하게 쓰도록 연결함.

### `data/` (데이터 보관소)
* **`raw/`**: 원본 데이터 저장소. 학사행정 규정 PDF 문서 및 학생 수강 내역 CSV 파일이 들어감.
* **`processed/`**: PDF와 CSV에서 추출해 낸 중간 형태의 정제된 JSON 데이터를 저장함.

### `ontology/` (지식 그래프 설계도)
* **`ontology_spec.json`**: 도메인 스키마 정의서. `[학생]`, `[과목]`, `[규정]` 노드와 `[:이수했음]`, `[:필수과목으로_지정]` 관계의 규칙을 명시함.
* **`schema.cypher`**: Neo4j 탐색 속도를 높이는 인덱스(Index) 및 중복 노드를 막는 제약조건(Constraint) 생성 쿼리 모음.

### `src/` (핵심 실행 엔진)
* **`database/neo4j_client.py`**: 파이썬에서 Neo4j DB로 Cypher 쿼리를 안전하게 던지고 결과를 받아오는 통신 드라이버.
* **`kg_builder/builder.py`**: `processed/`의 중간 데이터를 읽어서 Neo4j에 노드와 화살표를 `MERGE` 쿼리로 구축하는 생성 파이프라인.
* **`decision_engine/evaluator.py`**: 완공된 Neo4j 그래프의 연결 경로를 탐색하여 학생의 졸업 요건 충족 여부(Pass/Fail 및 사유)를 판단하는 추론 엔진.

### `tests/` (검증 모듈)
* **`test_neo4j.py`**: 전체 시스템 구동 전, Neo4j DB 접속 및 기본 쿼리 실행이 정상인지 단독으로 확인하는 테스트 코드.

## 개발환경 및 AI 작업 기록

이 프로젝트는 팀원 간 재현 가능한 개발환경과 AI 에이전트 작업 추적을 위해 별도 문서를 관리합니다.

### 공통 개발환경

- Windows 11 + WSL2
- Ubuntu 24.04
- Python 3.12.3
- uv
- Docker Desktop
- Neo4j 2026.06.0

상세 설치 및 검증 절차는 [로컬 개발환경 구축 가이드](docs/environment-setup.md)를 참고합니다.

### AI 시뮬레이션 로그

AI 에이전트가 수행한 주요 작업, 변경 파일, 의사결정, 검증 결과와 남은 작업을 팀원별로 기록합니다.

- [AI 시뮬레이션 로그 운영 규칙](docs/ai-simulation-logs/README.md)
- [정이량 작업 로그](docs/ai-simulation-logs/jeong-iryang/README.md)
- [황대겸 작업 로그](docs/ai-simulation-logs/hwang-daegyeom/README.md)
