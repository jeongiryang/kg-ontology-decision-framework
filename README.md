# KG Ontology Decision Framework

2026학년도 공통 교양 이수요건과 컴퓨터공학과 교육과정을 온톨로지와 Neo4j 지식그래프로 구조화하고, 자연어 질문에 검증된 규정 원문과 페이지 근거를 제공하는 로컬 GraphRAG PoC입니다.

이 프로젝트의 현재 중심 기능은 **Evidence 기반 학사규정 질의응답**입니다. 브라우저에
저장한 학적·이수 정보로 학점 진행률과 확인 가능한 요건을 개인화할 수 있지만, 성적표
전체를 받아 졸업 가능 여부를 최종 판정하는 시스템은 아닙니다.

## 현재 PoC 범위

| 항목 | 현재 기준 |
|---|---|
| 데이터 | 2026학년도 공통 교양 이수요건, 컴퓨터공학과 교육과정 |
| 원문 | Git에서 제외된 19페이지 발췌 PDF |
| Verified KG | 노드 1,536개 · 관계 3,287개 · Evidence 520개 |
| 그래프 DB | 로컬 Neo4j |
| 로컬 LLM | Ollama `qwen2.5-coder:14b`, context 8,192 |
| 웹 UI | Starlette + SSE + vanilla HTML/CSS/JavaScript |
| 기본 주소 | `http://127.0.0.1:8501` |

확정 답변에는 `VERIFIED` Fact와 직접 연결된 `VERIFIED Evidence`가 필요합니다. Citation은 Evidence 원문과 발췌 PDF·원본 PDF·인쇄 페이지를 구분해 제공합니다.

현재 `pyproject.toml`의 런타임 기준은 Python `3.12`, Neo4j Python Driver `6.2+`, Starlette `1.6+`, Uvicorn `0.52+`, PyMuPDF `1.28+`입니다. 정확한 해석 버전은 `uv.lock`으로 고정합니다.

## 전체 아키텍처

```text
2026 교육과정 PDF
→ ontology/ontology_spec.json
→ data/verified/2026 JSON bundle
→ Neo4j
→ 자연어 질문
→ 브라우저 프로필·현재 메시지의 검증된 사용자 진술 결합
→ 데이터 기반 semantic slot 또는 LLM QueryPlan
→ 관련 스키마 선택
→ LLM Cypher 후보 생성
→ comment-free canonicalization
→ 정적 안전 검증
→ Neo4j EXPLAIN
→ execute_read
→ ResultValidator
→ 구조화 Claim
→ 결정론적 한국어 답변
→ VERIFIED Evidence Citation
→ Starlette/SSE 웹 UI
```

- 명시적인 과목·규칙 semantic slot은 Verified bundle 기반 결정론적 planner가 처리하고,
  그 밖의 자연어 `QueryPlan`과 Cypher 후보는 LLM이 생성합니다.
- 생성된 Cypher는 주석 제거, 제한 문법·스키마 검사와 Neo4j `EXPLAIN`을 모두 통과해야 읽기 트랜잭션으로 실행됩니다.
- 최종 사실 문장은 LLM이 자유롭게 작성하지 않습니다. Python이 검증된 Claim의 값·단위·극성을 결정론적으로 렌더링합니다.
- Evidence가 없거나 `REVIEW_REQUIRED`인 사실은 확정 답변으로 승격하지 않습니다.
- clarification 선택지는 sealed `ChatResponse`에 추가하지 않고 별도의 versioned SSE envelope로 전달합니다.
- 개인화 결과의 다섯 상태와 프로필 갱신도 별도 versioned SSE envelope이며 sealed
  `ChatResponse`의 기존 8필드는 바꾸지 않습니다.
- 선택 스키마, 승인된 canonical Cypher와 조회 provenance 그래프는 표시 전용 데이터이며 답변·Citation을 변경하지 않습니다.

## 현재 기능

- 온톨로지와 Verified JSON bundle의 계약 검증
- Neo4j 제약조건·인덱스 적용, 멱등 적재와 대표 사실 검증
- provider-neutral `StructuredLLMClient`
  - Ollama
  - OpenAI-compatible
- 자연어 `QueryPlan`, 데이터 기반 clarification과 관련 스키마 선택
- 동적 Text-to-Cypher와 comment-free canonicalization
- deny-by-default 정적 안전 검증과 Neo4j `EXPLAIN`
- `execute_read` 기반 읽기 경로와 결과 크기 제한
- Fact·Evidence 상태 및 직접 provenance 검증
- 구조화 Claim과 결정론적 한국어 답변
- Evidence 원문 및 발췌 PDF·원본 PDF·인쇄 페이지 Citation
- 19페이지 PDF 이미지와 PyMuPDF 실제 텍스트 검색 강조
- Starlette/SSE 처리 타임라인과 상태별 UI
- 상세 모드의 선택 스키마·승인 Cypher·정적 조회 그래프
- `localStorage` 기반 versioned 사용자 프로필, 채팅 정보 추출·정정·후속 질문 재사용
- `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`, `OUT_OF_SCOPE`,
  `ADVISORY` 개인화 outcome

## 지원하지 않거나 제한된 범위

- 성적표 전체, 성적·재수강·실시간 수강신청을 포함한 최종 졸업 가능 여부 판정
- 모든 학년도와 모든 학과 데이터
- Evidence가 `REVIEW_REQUIRED`인 규칙의 확정 답변
- 아직 등록되지 않은 Claim 또는 fact family의 임의 설명
- 실제 연구실 vLLM 서버와의 통합 검증
- Neo4j Community Edition의 DB 역할 수준 읽기 전용 보장
- 인증, 다중 사용자 queue, 고가용성과 자동 복구를 갖춘 운영 서비스
- 정식 클라우드 배포와 GitHub Actions 기반 CD

현재 범위를 벗어나거나 근거가 없는 질문은 추측하지 않습니다. sealed 응답의 안전 상태와
별도로 UI outcome은 사용자 정보 부족, 근거 부족, 범위 밖, 조건부 추천을 명시적으로
구분합니다.

## 프로젝트 구조

```text
kg-ontology-decision-framework/
├── ontology/
│   ├── ontology_spec.json       # 온톨로지·그래프 계약 원본
│   ├── llm_query_schema.json    # 명세에서 생성한 LLM 질의 스키마
│   └── schema.cypher            # Neo4j 스키마 정적 재현본
├── data/
│   ├── raw/                     # 원본 기반 JSON과 Git 제외 원문 파일 위치
│   └── verified/2026/           # 검증·적재 기준 JSON bundle
├── src/kg_builder/
│   ├── llm/                     # provider adapter, planner, Cypher generator
│   ├── query/                   # QueryPlan, selector, SafetyPipeline, validator
│   ├── answer/                  # Claim 검증, 한국어 renderer, 개인화 outcome
│   ├── personalization.py       # 브라우저 프로필 검증·메시지 정보 추출
│   └── neo4j_ingest.py          # Verified KG 검증·적재 CLI
├── src/evidence_chat/
│   ├── server.py                # Starlette 앱과 /api/ask SSE 진입점
│   ├── chat_adapter.py          # ChatResponse 표시 adapter
│   ├── graph_projection.py      # 승인 결과의 표시용 graph projection
│   ├── pdf_evidence.py          # PDF 페이지·텍스트 강조
│   └── static/                  # vanilla 웹 UI
├── tests/                       # 단위 및 선택적 로컬 통합 테스트
├── docs/                        # 설계·환경·운영·AI 작업 문서
└── .github/workflows/           # 비밀값 없는 CI 검증
```

루트의 `main.py`, `config/settings.py`, `src/database/neo4j_client.py`, `src/decision_engine/evaluator.py`, `src/kg_builder/builder.py`는 현재 0바이트 초기 골격이며 실행 진입점이 아닙니다.

## 빠른 시작

### 1. 저장소와 Python 환경

WSL2 Ubuntu 셸에서 실행합니다. Python 계약은 `>=3.12,<3.13`입니다.

```bash
git clone https://github.com/jeongiryang/kg-ontology-decision-framework.git
cd kg-ontology-decision-framework
uv sync --locked
cp .env.example .env
```

Windows 11, WSL2, Docker Desktop, Neo4j와 Ollama 설치 과정은 [로컬 개발환경 구축 가이드](docs/environment-setup.md)를 참고합니다.

### 2. 로컬 서비스 확인

Docker Desktop과 Neo4j, Ollama를 실행하고 모델이 이미 준비됐는지 확인합니다.

```bash
docker ps
ollama list
curl http://127.0.0.1:11434/api/tags
```

기본 실측 모델은 `qwen2.5-coder:14b`입니다. 모델이 없다면 [로컬 LLM 문서](docs/local-llm-query-pipeline.md)의 설치·VRAM 조건을 확인한 뒤 준비합니다.

### 3. 환경변수

실제 자격증명은 Git에서 제외된 `.env`에만 둡니다. 변수명과 안전한 자리표시자는 [`.env.example`](.env.example)을 기준으로 합니다.

| 그룹 | 역할 |
|---|---|
| `NEO4J_*` | 빈 데이터베이스 스키마·적재용 계정 |
| `NEO4J_QUERY_*` | 동적 질의용 명시적 query 계정 |
| `KG_LLM_*` | provider, loopback base URL, 모델, timeout, context |
| `KG_QUERY_TRACE_*` | 질문 원문·fingerprint opt-in과 보존 설정 |
| `KG_CHAT_*` | UI debug, 상세 조회, 동시성, timeout, PDF |
| `CHATBOT_HOST`, `CHATBOT_PORT` | 웹 bind 주소와 포트 |

발췌 PDF는 Git에서 제외됩니다. 19페이지 PDF의 로컬 경로를 다음 변수로 지정합니다.

```dotenv
KG_CHAT_PDF_PATH=/path/to/ignored/2026_curriculum_excerpt.pdf
```

PDF가 없으면 답변과 Citation 텍스트는 표시되지만 페이지 이미지와 강조는 사용할 수 없습니다. 실제 비밀번호·토큰·개인 경로는 문서나 커밋에 기록하지 않습니다.

## 빈 Neo4j 구축

아래 명령은 온톨로지와 Verified bundle을 검사하고 빈 로컬 Neo4j를 멱등하게 구축합니다.

```bash
uv run python -m kg_builder.neo4j_ingest validate
uv run python -m kg_builder.neo4j_ingest check-connection
uv run python -m kg_builder.neo4j_ingest apply-schema
uv run python -m kg_builder.neo4j_ingest load
uv run python -m kg_builder.neo4j_ingest verify
```

`load`는 비어 있지 않은 DB를 자동 삭제하거나 덮어쓰지 않습니다. 이미 이 Verified bundle이 적재되고 `verify`가 통과한 사용자는 다시 적재할 필요가 없습니다. 상세 절차는 [Neo4j 적재 가이드](docs/neo4j-ingestion.md)를 참고합니다.

## 웹 챗봇 실행

Neo4j query 설정과 Ollama가 준비된 WSL2 셸에서 실행합니다.

```bash
uv run python -m evidence_chat.server
```

브라우저에서 `http://127.0.0.1:8501`을 엽니다. 선택 스키마, 승인 Cypher와 정적 조회 그래프를 표시하려면 로컬 `.env`에서 다음 값을 선택적으로 사용합니다.

```dotenv
KG_CHAT_SHOW_QUERY_DETAILS=true
```

상세 모드도 정적 검증과 동일 candidate의 Neo4j `EXPLAIN`을 통과한 comment-free canonical Cypher만 표시합니다. 실패·폐기 후보, prompt, 모델 원문, 접속정보와 로컬 경로는 표시하지 않습니다.

로컬 PC에서 `tmux`로 서버와 ngrok 터널을 유지하는 시연 절차는 [로컬 시연 배포 및 정식 배포 계획](docs/deployment.md)을 참고합니다.

## CLI

검증된 구조화 조회 결과:

```bash
uv run python -m kg_builder.query.natural_language_cli \
  "2026학년도 컴퓨터공학과 자료구조의 이수구분은?"
```

구조화 Claim 기반 최종 한국어 답변:

```bash
uv run python -m kg_builder.answer.cli \
  "2026학년도 컴퓨터공학과 전공필수 과목을 알려줘"
```

확장 fact family의 LLM 없는 확인:

```bash
uv run python -m kg_builder.answer.plan_cli --print-examples
```

## 대표 질문

다음은 사용 예시이며 런타임 분기나 정답 하드코딩이 아닙니다.

```text
2026학년도 컴퓨터공학과 학생의 교양 최소 이수학점은?
자료구조는 몇 학년 몇 학기에 개설돼?
이산수학의 학수번호는?
컴퓨터공학과 전공필수 과목은?
편입생도 교양을 이수해야 해?
```

## 테스트

비밀값 없는 로컬 단위 테스트와 생성 스키마 검사는 다음과 같습니다.

```bash
uv run python -m unittest discover -s tests -v
uv run pytest -q
uv run python -m kg_builder.query.schema_exporter check
```

현재 GitHub Actions는 잠금 환경에서 `unittest` discovery와 schema exporter stale check를 실행합니다. 로컬 `pytest` 명령은 같은 테스트 모음을 별도로 확인할 때 사용합니다.

외부 서비스가 필요한 테스트는 명시적으로 opt-in합니다.

```bash
# 로컬 Neo4j 읽기 통합
KG_NEO4J_INTEGRATION=1 uv run pytest -q

# 로컬 Ollama + Neo4j smoke
KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_local_llm_integration.py -s
```

Neo4j·Ollama와 자격증명이 없는 기본 CI에서는 해당 통합 검사가 skip될 수 있습니다. skip된 검사를 통과로 간주하지 않습니다.

## 문서 안내

| 주제 | 문서 |
|---|---|
| 개발환경 | [로컬 개발환경 구축](docs/environment-setup.md) |
| 온톨로지 | [2026 학사 교육과정 온톨로지](docs/ontology/ontology-v1.md) |
| Verified KG 적재 | [Neo4j 적재 가이드](docs/neo4j-ingestion.md) |
| 기존 구조화 질의 | [Verified KG 질의·Evidence API](docs/query-evidence-api.md) |
| 동적 질의 안전성 | [Text-to-Cypher 안전 기반](docs/text-to-cypher-safety.md) |
| 로컬 LLM | [Ollama Text-to-Cypher PoC](docs/local-llm-query-pipeline.md) |
| Claim·답변·Citation | [Evidence 기반 한국어 답변](docs/evidence-answer-renderer.md) |
| 답변 범위 확장 | [확장 fact family](docs/extended-fact-families.md) |
| 개인화·50문항 평가 | [질의 정확도와 개인화 계약](docs/query-personalization.md) |
| 평가 결과표 | [질문셋 V1 50문항 결과](docs/evaluations/question-set-v1-2026-08-28.md) |
| Starlette UI | [학사규정 근거 챗봇](docs/evidence-chat.md) |
| 로컬 시연·배포 계획 | [배포 및 시연](docs/deployment.md) |
| AI 작업 기록 | [AI 시뮬레이션 로그](docs/ai-simulation-logs/README.md) |

## 데이터와 보안 원칙

- 숫자·학점·학수번호·규칙을 추정하지 않습니다.
- Raw·Verified 원문과 `ontology_spec.json`을 런타임 질문에 맞춰 수정하지 않습니다.
- 질문 값은 Cypher 파라미터로 전달하고 LLM 생성 임의 Cypher를 직접 실행하지 않습니다.
- `.env`, 원본 PDF, 모델 파일, Neo4j 데이터와 runtime trace는 Git에 포함하지 않습니다.
- 질문 원문과 fingerprint trace는 기본 비활성입니다.
- 현재 웹 앱에는 정식 인증이 없으므로 공개 시연은 제한된 시간과 대상에만 사용합니다.
