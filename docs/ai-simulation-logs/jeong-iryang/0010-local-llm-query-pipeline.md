# 0010. RTX 4070 Ti 로컬 LLM Text-to-Cypher PoC

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/local-llm-query-pipeline` |
| 기준 커밋 | `ce2fa12424468706af8be3c0728b75fa49c57b87` |
| 관련 Issue/PR | PR #13 병합 이후 작업, PR #14 교차 검토 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #13의 동적 Cypher 안전 기반 위에 로컬 Ollama planner와 Cypher generator를 연결해 한국어 질문을 기존 Verified KG의 Evidence 포함 JSON으로 조회하는 PoC를 구현한다.

## 2. 작업 전 확인

- PR #13 병합 커밋이 `main`에 반영된 것을 확인했다.
- `main`을 fast-forward 동기화하고 `feat/jeongiryang/local-llm-query-pipeline`을 생성했다.
- `.env`, `AGENTS.md`, Raw·Verified 데이터와 `ontology_spec.json`은 Git 변경 대상에서 제외했다.
- 열린 팀원 PR #14의 최신 Head를 교차 검토했다. 테스트 73개 통과, 1개 skip, 9개 오류와 `.gitignore`의 `AGENTS.md` 제외 제거를 MAJOR로 보고 `REQUEST_CHANGES` 리뷰를 제출했다.

## 3. 기존 Neo4j 통합 검증

`KG_NEO4J_INTEGRATION=1 uv run pytest -q`를 실행해 기존 고정 Intent 6개와 동적 안전 조회 통합 테스트를 실제로 수행했다. 결과는 53개 테스트 및 62개 서브테스트 통과였다. 전후 개수는 노드 1,518, 관계 3,260, Evidence 511로 동일했다.

## 4. PR #13 후속 보완

- 기본 trace에서 salt 없는 질문 SHA-256을 제거했다.
- fingerprint는 HMAC-SHA256 opt-in으로 바꾸고 키가 없으면 설정 오류로 중단한다.
- LLM 스키마에 괄호 없는 WHERE 조건 생성 제약을 추가했다.
- `SUPPORTED_BY` 출발 호환성과 상속 `status`를 함께 만족하는 fact 라벨을 온톨로지에서 파생했다. `Course`는 제외하고 편성 사실은 `CourseOffering`을 사용한다.
- Rule 목록 필터를 위해 안전한 `property IN $parameter` 바인딩과 결과 scope 검증을 추가했다.

## 5. 로컬 모델 비교와 결정

Ollama `0.32.5`를 재사용하고 공식 `qwen2.5-coder:7b`, `qwen2.5-coder:14b` 두 후보만 내려받았다. 두 모델은 Q4_K_M, Apache-2.0, 원래 컨텍스트 32K다.

| 모델 | 6문항 성공 | 평균 시간 | 관측 VRAM | 결정 |
|---|---:|---:|---:|---|
| 7B | 1/6 | 약 6.6초 | 6,694MiB | 계획·Cypher 계약 실패가 많아 제외 |
| 14B | 6/6 | 약 12.5초 | 11,506MiB | PoC 선정 |

일반 `qwen2.5:7b`도 초기 JSON probe에서 필터·필드 계약을 지키지 못했다. 14B는 VRAM 가용량이 489MiB까지 줄어 8K 컨텍스트를 초기 상한으로 정했다.

## 6. 구현

- 로컬 HTTP만 허용하는 Ollama JSON client와 환경 계약
- JSON Schema 기반 QueryPlan planner와 단일 안전 재시도
- Verified KG에서 숫자를 제거한 Rule 의미 힌트·식별자 컨텍스트 파생
- QueryPlan에서 온톨로지 부분집합을 선택하는 schema selector
- QueryPlan의 fact 계열·필터 바인딩에서 허용 문법 scaffold 생성
- 후보 Cypher를 반드시 `SafetyPipeline`으로 전달하는 오케스트레이터
- 질문 원문을 결과 QueryPlan과 runtime trace에서 제외하는 JSON CLI
- fake client 단위 테스트와 선택적 실제 Ollama 통합 테스트

질문 문자열, 과목값, 학점값 또는 Evidence 페이지를 런타임 분기문에 하드코딩하지 않았다. smoke 질문과 기대 결과는 테스트에만 존재한다.

## 7. 실제 14B 결과

- 교양 최소: 34학점, Evidence 1
- 균형교양: 4개 영역에서 영역별 1과목 이상(`COURSE_PER_AREA`), 총 12학점 이상(`CREDIT`), Evidence 2
- 편입생: 면제 Rule, Evidence 1
- 자료구조 개설: 2학년 1학기, Evidence 1
- 전공필수: 9과목·21학점, Evidence 9
- 자료구조 이수구분: `MAJOR_ELECTIVE`, Evidence 1

마지막 값은 기존 결정론적 Intent와 별도로 대조해 동일함을 확인했다. smoke 전후 DB는 1,518/3,260/511로 불변이었다.

## 8. 실패한 접근과 원인

- 일반 7B와 coder 7B는 JSON 필드명·필터를 자주 누락했다.
- strict 문법 scaffold 이전에는 14B도 노드 map 조건, 반대 방향 관계, 무관한 Evidence를 생성해 validator에서 차단됐다.
- 초기 broad `area_id` 계획은 최소학점 규칙 외 특수·상한 규칙까지 반환했다. 단일/복수 Rule 식별자 계약으로 보완했다.
- 7B 최종 비교는 6문항 중 1개만 성공했으며 실패를 통과로 기록하지 않았다.

## 9. 검증

| 검증 | 실제 결과 |
|---|---|
| 기존 Neo4j 통합(최종) | 64 tests, 62 subtests PASS, 로컬 LLM smoke 1개 skip |
| PR #13 보완 회귀 | 22 tests, 33 subtests PASS(중간 실행) |
| 로컬 LLM 단위 테스트 | 9 PASS(중간 실행) |
| 14B 실제 smoke | 1 test, 6 subtests PASS |
| 7B 비교 smoke | 1/6 성공, 테스트 실패로 기록 |
| DB 불변 | 1,518 / 3,260 / 511 유지 |
| 최대 VRAM | 14B 11,506MiB, 7B 6,694MiB |
| 최종 unittest | 65 PASS, 4 skip |
| 최종 pytest | 61 PASS, 4 skip |
| schema exporter | current/PASS |
| Markdown 상대 링크 | PASS |
| `git diff --check` | PASS |

`uv sync --locked`, `uv lock --check`, compile, JSON 파싱, schema stale, Markdown 링크, 민감정보 후보와 Git 제외 검사를 완료했다. 통합 검증은 쓰기 쿼리 없이 수행했다.

## 10. 남은 위험과 다음 작업

- Neo4j Community 로컬 계정은 권한 수준의 읽기 전용 계정이 아니다.
- 14B는 VRAM 여유가 작고 모델 생성은 반복 실행에서 차이가 날 수 있다.
- Ollama 자체 로그의 보존·접근 정책은 애플리케이션 trace와 별도로 운영해야 한다.
- 다음 작업은 Evidence 기반 한국어 답변 renderer와 황대겸 프론트엔드 응답 계약 연결이다.
