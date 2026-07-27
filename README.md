# KG Ontology Decision Framework

지식 그래프 및 온톨로지 기반의 의사결정 추론 프레임워크

---

## 프로젝트 구조

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
