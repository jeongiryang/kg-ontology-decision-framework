# RTX 4070 Ti 로컬 LLM Text-to-Cypher PoC

## 1. 목적과 실행 위치

이 PoC는 provider-neutral `StructuredLLMClient` 경계 뒤에서 로컬 모델을 사용해 한국어 질문을 QueryPlan과 Cypher로 변환하고, 기존 로컬 Neo4j Verified KG를 읽기 전용으로 조회한다. 현재 실측 provider는 개인 PC WSL2의 Ollama이며, 연구실 vLLM은 OpenAI-compatible adapter로 교체할 수 있다.

```text
한국어 질문
→ 로컬 LLM provider QueryPlan
→ QueryPlan 계약 검증
→ 온톨로지 스키마 부분집합 선택
→ 로컬 LLM provider Cypher 후보
→ PR #13 SafetyPipeline
→ Neo4j EXPLAIN·execute_read
→ VERIFIED Evidence 포함 JSON
```

개인 PC는 Python 애플리케이션·Neo4j·테스트와 이번 4070 Ti PoC를 담당한다. DSW A6000 서버는 더 큰 모델을 평가할 후속 배포 후보이며 현재 실행 경로에 필요하지 않다. 후속 답변 계층은 검증 행에서 구조화 Claim을 만들고 Python으로 최종 문장을 렌더링하며, 프론트엔드 연결은 아직 구현하지 않았다.

## 2. 엔진과 모델 선정

Ollama `0.32.5`가 이미 설치되어 있고 `127.0.0.1:11434`에만 노출된 상태를 재사용했다. 모델 캐시는 Git 저장소 밖의 Ollama 관리 영역에 있고 프로젝트 `.venv`에는 PyTorch·CUDA·vLLM을 추가하지 않았다.

| 모델 | 파라미터 | 양자화 | 로컬 파일 | 컨텍스트 후보 | 6문항 최종 성공 | 평균 시간 | 관측 VRAM | 판정 |
|---|---:|---|---:|---:|---:|---:|---:|---|
| `qwen2.5-coder:7b` | 7.6B | Q4_K_M | 4.7GB | 8,192 | 1/6 | 약 6.6초 | 6,694MiB | 계약 준수율 부족 |
| `qwen2.5-coder:14b` | 14.8B | Q4_K_M | 9.0GB | 8,192 | 6/6 | 약 12.5초 | 11,506MiB | **선정** |

두 모델 모두 Apache-2.0이고 Ollama가 표시하는 원래 컨텍스트 상한은 32,768이다. RTX 4070 Ti 12GB에서는 14B 모델이 100% GPU로 올라갔지만 관측 가용 VRAM이 489MiB까지 감소했다. 따라서 최초 운영값은 8K이며, 다른 GPU 프로세스가 있거나 컨텍스트를 늘릴 때 OOM 위험이 있다. 7B는 빠르고 VRAM 여유가 크지만 Rule 계획과 strict Cypher 생성 정확도가 PoC 기준을 만족하지 못했다.

모델 출처와 라이선스는 [Ollama Qwen2.5-Coder 라이브러리](https://ollama.com/library/qwen2.5-coder), [Qwen2.5-Coder-7B-Instruct 모델 카드](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct), [Qwen2.5-Coder-14B-Instruct 모델 카드](https://huggingface.co/Qwen/Qwen2.5-Coder-14B-Instruct)에서 대조했다.

## 3. 환경변수

실제 값은 Git에서 제외된 `.env`에만 기록한다.

```dotenv
KG_LLM_PROVIDER=ollama
KG_LLM_BASE_URL=http://127.0.0.1:11434
KG_LLM_MODEL=qwen2.5-coder:14b
KG_LLM_API_KEY=
KG_LLM_TIMEOUT_SECONDS=180
KG_LLM_MAX_RETRIES=1
KG_LLM_TEMPERATURE=0
KG_LLM_CONTEXT_LENGTH=8192
KG_LLM_MAX_OUTPUT_TOKENS=2048

NEO4J_QUERY_URI=bolt://localhost:7687
NEO4J_QUERY_USER=your-local-query-user
NEO4J_QUERY_PASSWORD=
NEO4J_QUERY_DATABASE=neo4j
```

`KG_LLM_PROVIDER`는 `ollama` 또는 `openai-compatible`만 허용한다. `KG_LLM_BASE_URL`은 `http://localhost` 또는 `http://127.0.0.1`만 허용하며 클라우드 API fallback은 없다. API key는 OpenAI-compatible 로컬 서버가 요구할 때만 환경변수로 전달하고 URL·로그·trace에는 기록하지 않는다. `NEO4J_QUERY_*`도 기존 ingestion 자격증명으로 자동 fallback하지 않는다.

두 provider adapter는 HTTP `301`, `302`, `303`, `307`, `308`을 포함한 redirect를 모두 거부한다. 외부 목적지뿐 아니라 다른 loopback 주소나 같은 서버의 다른 경로로도 따라가지 않으며, redirect 응답에는 일반 네트워크 재시도를 적용하지 않는다. 실패는 본문·`Location`·Authorization·prompt를 노출하지 않는 `LLM_HTTP_REDIRECT_REJECTED`로 반환한다.

연구실 vLLM은 외부 주소를 애플리케이션에 직접 설정하지 않고 SSH 터널의 loopback endpoint로 접근한다.

```dotenv
KG_LLM_PROVIDER=openai-compatible
KG_LLM_BASE_URL=http://127.0.0.1:8000/v1
KG_LLM_MODEL=your-research-server-model
KG_LLM_API_KEY=
KG_LLM_TIMEOUT_SECONDS=180
KG_LLM_MAX_RETRIES=1
KG_LLM_TEMPERATURE=0
KG_LLM_CONTEXT_LENGTH=16384
KG_LLM_MAX_OUTPUT_TOKENS=2048
```

base URL이 `/v1`으로 끝나면 adapter는 `/chat/completions`만 붙이고, root URL이면 `/v1/chat/completions`를 붙인다. `/v1/v1` 중복 경로는 설정 오류다.

`KG_LLM_CONTEXT_LENGTH`는 Ollama 요청의 `options.num_ctx`에 사용된다. OpenAI Chat Completions에는 동일한 표준 요청 필드가 없으므로 OpenAI-compatible adapter가 이 값을 서버에 전달하거나 서버 상한과 자동 동기화한다고 보장하지 않는다. vLLM의 context 상한은 서버 기동 시 `--max-model-len` 등으로 별도 설정해야 하며, 실제 vLLM 연결 검증에서는 클라이언트 기대값과 서버 상한을 각각 확인해야 한다.

현재 로컬 Neo4j Community Edition PoC에서는 `.env`에 query 변수를 명시적으로 분리했지만 동일 로컬 사용자를 가리킨다. 데이터베이스 역할 기반의 최종 읽기 전용 권한은 보장되지 않는다. 따라서 validator, EXPLAIN, executor 방어가 모두 필요하며, 운영 배포에서는 권한 분리가 가능한 Neo4j 환경의 reader 계정을 최종 방어선으로 사용해야 한다.

## 4. Provider 경계와 모델 교체

```text
StructuredLLMClient
├── OllamaClient               → /api/chat
└── OpenAICompatibleClient     → /v1/chat/completions
```

`LLMSettings.from_env()`가 provider·endpoint·model·timeout·context·출력 token 상한을 검증하고, `create_llm_client()`만 provider를 분기한다. planner, Cypher generator, 자연어 서비스는 특정 엔진을 import하거나 조건 분기하지 않는다. CLI와 향후 웹 composition root도 동일 factory를 사용한다.

모델·엔진 교체 시 QueryPlan, SchemaSelector, Cypher 반환 계약, SafetyPipeline, Neo4j, Evidence 검증과 향후 renderer·프론트엔드 계약은 유지한다. provider 설정·adapter·JSON 준수율·Cypher 정확도·프롬프트 호환성·context·응답시간·VRAM·회귀 질문 결과만 다시 검증한다. KG 재적재나 온톨로지 재작성은 필요하지 않다.

OpenAI-compatible adapter는 실제 네트워크 없이 요청 envelope, 선택적 Bearer token, JSON Schema, 응답 파싱, 크기·timeout·HTTP 오류 비노출과 redirect 무추적을 단위 테스트했다. 실제 vLLM 서버 통합은 아직 실행하지 않았다.

## 5. Planner와 스키마 선택

planner는 provider가 반환한 JSON Schema 계약 응답을 사용하고 다음 상태를 구분한다.

- `READY`
- `CLARIFICATION_REQUIRED`
- `OUT_OF_SCOPE`
- `UNSUPPORTED`
- `UNRESOLVED`

질문 원문을 코드의 분기 키로 비교하지 않는다. 지원 연도, 학과 ID, 통제어휘와 VERIFIED Rule 식별자 목록은 Verified KG·온톨로지에서 실행 시 파생한다. Rule의 의미 힌트에서는 숫자를 제거해 계획 단계가 정답값을 직접 전달하지 않게 한다. `REVIEW_REQUIRED` Rule도 값 없이 의미와 Condition의 `subject_field`만 별도 컨텍스트에 제공한다. 따라서 TOEIC 같은 일반 기준 질문이 미검증 임계값을 요구하면 개인 이력 질문으로 오분류하거나 숫자를 추측하지 않고 `UNRESOLVED`로 끝난다. 단일 Rule은 `rule_id` 또는 한 원소 `rule_ids`, 영역의 복수 Rule은 `rule_ids`로 표현한다.

졸업 관련 질문 분류는 다음 데이터 요구량을 구분한다.

- 일반 규정 조회: 개인 이력 없이 기준·점수·학점·과목을 조회한다.
- 단일 조건 비교: 사용자가 제시한 한 점수·학점과 규정의 충족 여부를 비교한다. 비교 기능이 미지원이면 개인 이력 부재와 다른 고정 한국어 `UNSUPPORTED` 안내를 사용한다.
- 전체 개인 이력 판정: 수강내역·취득학점·성적표 등 개인 기록과 졸업 가능 판정을 함께 요구할 때만 결정론적으로 `UNSUPPORTED` 처리한다.

`내가`, `학생`, `졸업` 같은 단어만으로 전체 개인 이력 판정으로 승격하지 않는다. 분류기는 답·Rule ID·Evidence ID·페이지·점수를 만들지 않는다.

`QuerySchemaSelector`는 QueryPlan 필터·요청 필드에서 필요한 fact 계열을 고르고, 온톨로지 관계 그래프의 최단 경로로 관련 라벨·관계·통제어휘만 선택한다. 전체 26개 라벨을 매번 모델에 보내지 않는다.

`SINGLE_COURSE`는 selection mode를 결과 검증까지 유지한다. 모델이 학년도·학과·정확한 과목명/학수번호를 모두 추출하고도 잠재적 동명 과목을 이유로 `CLARIFICATION_REQUIRED`를 반환하면, planner는 모델에 DB identity 검증 책임을 명시해 한 번만 재계획을 요청한다. 두 번째 응답도 모호하면 강제로 `READY`로 바꾸지 않는다. `Course.course_id`를 안정 identity로 반환해 후보 0개는 `NOT_FOUND`, 서로 다른 identity 1개는 정상 조회, 2개 이상은 `CLARIFICATION_REQUIRED`로 처리한다. 같은 Course에 Evidence가 여러 개 연결되거나 여러 편성이 있어 행이 늘어난 것은 동명 과목으로 보지 않는다. 학수번호와 이름이 함께 주어지면 학수번호를 우선한다.

## 6. Cypher 생성과 안전 실패

작은 로컬 모델은 일반 Neo4j 문법을 사용해 노드 map 조건이나 잘못된 Evidence 연결을 생성하는 경향이 있었다. 검증기를 완화하지 않고 QueryPlan의 fact 계열과 필터 바인딩에서 결정론적으로 만든 `required_syntax_scaffold`를 프롬프트에 제공한다. 이 scaffold는 질문 답이나 과목값을 포함하지 않고 허용 경로·alias·파라미터·반환 계약만 제한한다.

모델 후보는 항상 다음 순서를 통과한다.

1. deny-by-default Cypher 정적 검증
2. Neo4j `EXPLAIN`
3. `execute_read`
4. fact–Evidence provenance와 결과 크기 검증

첫 후보가 실패하면 오류 코드만 모델에 전달해 한 번 재생성한다. 두 번째 실패는 `SAFE_FAILURE`로 끝나며 미검증 Cypher 직접 실행 fallback은 없다.

## 7. CLI

선택한 provider와 기존 Neo4j가 실행 중일 때 다음처럼 구조화 JSON을 받는다. CLI는 provider adapter를 직접 생성하지 않고 `create_llm_client()` factory를 사용한다.

```bash
uv run python -m kg_builder.query.natural_language_cli \
  "2026년 컴퓨터공학과 3학년 2학기 전공선택 중 3학점 과목을 알려줘"
```

이 구조화 조회 CLI 출력은 `request_id`, 상태, 질문 원문이 제거된 QueryPlan, 검증된 Cypher, result rows, Evidence, 오류 단계·코드, 모델명과 시간을 포함하며 자연어 답변 렌더링은 하지 않는다. 별도 `kg_builder.answer.cli`가 구조화 Claim 기반 최종 답변을 제공한다.

## 8. 실제 14B smoke 결과

| 질문 유형 | 결과 | 행/Evidence | 핵심 값 |
|---|---|---:|---|
| 일반 교양 최소학점 | PASS | 1/1 | 34학점 |
| 균형교양 요건 | PASS | 2/2 | 4개 영역에서 영역별 1과목 이상, 총 12학점 이상 |
| 편입생 교양 면제 | PASS | 1/1 | 면제 Rule |
| 자료구조 개설 | PASS | 1/1 | 2학년 1학기 |
| 컴공 전공필수 | PASS | 9/9 | 9과목, 21학점 |
| 자료구조 이수구분 | PASS | 1/1 | `MAJOR_ELECTIVE` |

마지막 값은 기존 결정론적 `GET_COURSE_COMPLETION_TYPE` 결과와 동일하다. smoke 전후 DB는 노드 1,518개, 관계 3,260개, Evidence 511개로 변하지 않았다. 최대 관측 VRAM은 11,506MiB였다.

## 9. 테스트

모델이 필요 없는 테스트:

```bash
uv run python -m unittest discover -s tests -v
uv run pytest -q
uv run python -m kg_builder.query.schema_exporter check
```

기존 Neo4j 읽기 통합 테스트:

```bash
KG_NEO4J_INTEGRATION=1 uv run pytest -q
```

Ollama 14B와 Neo4j를 모두 사용하는 선택적 smoke:

```bash
KG_LOCAL_LLM_INTEGRATION=1 uv run pytest -q tests/test_local_llm_integration.py -s
```

GitHub Actions에서는 로컬 모델·Neo4j 비밀값이 필요한 smoke를 성공한 것으로 가장하지 않고 skip한다.

Provider adapter 단위 테스트는 네트워크 없이 Ollama와 OpenAI-compatible 양쪽의 요청 경로·구조화 출력·응답 제한·오류 비노출을 검증한다. `301`, `302`, `303`, `307`, `308` 각각에서 목적지 요청과 재시도가 없고 API key·prompt·응답 본문·`Location`이 오류에 포함되지 않는지도 검사한다. 실제 Neo4j·Ollama 통합 테스트의 runtime trace는 `TemporaryDirectory`에 격리해 저장소의 `logs/query-runs/`에 테스트 질문 파일을 남기지 않는다. 실제 vLLM integration은 실행하지 않는다.

## 10. 개인정보와 로그

기본 runtime trace에는 질문 원문과 fingerprint를 모두 저장하지 않는다. 중복 식별이 꼭 필요할 때만 HMAC-SHA256 fingerprint를 opt-in하며 키는 환경변수로만 받는다. LLM 원문 응답도 trace에 저장하지 않는다. CLI 화면 출력과 `logs/query-runs/` 저장 정책은 분리되어 있고 런타임 로그는 Git에서 제외된다.

## 11. 제한과 다음 단계

- 14B는 12GB VRAM 여유가 작아 동시 실행·긴 컨텍스트에 취약하다.
- Ollama 프로세스 자체의 운영 로그·보존 정책은 별도로 관리해야 한다.
- OpenAI-compatible adapter는 구현·단위 테스트됐지만 실제 연구실 vLLM과의 호환성은 아직 검증하지 않았다.
- Neo4j Community 로컬 사용자는 권한 수준의 읽기 전용 경계가 아니다.
- 자연어 모델 출력은 결정론을 보장하지 않으므로 회귀 질문셋을 반복 평가해야 한다.
- 구조화 Claim 기반 한국어 답변 렌더링은 후속 계층에서 구현되었으며, 다음 작업은 황대겸 프론트엔드 응답 계약에 연결하는 것이다.
