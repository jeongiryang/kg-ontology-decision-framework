# 0012. LLM HTTP redirect 보안 강화

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/local-llm-query-pipeline` |
| 기준 커밋 | `093d307b158e1f757327e2d615c787d3a54ac2bf` |
| 관련 커밋 | 본 로그 포함 커밋 |
| 관련 PR | Draft PR #15 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #15 최종 독립 검토에서 발견된 HTTP redirect BLOCKER를 재현하고, Ollama와 OpenAI-compatible provider가 모든 redirect를 목적지와 무관하게 거부하도록 보완한다. 통합 테스트 trace를 임시 디렉터리에 격리하고 OpenAI-compatible/vLLM context 설정의 실제 책임 경계도 문서화한다.

## 2. 작업 전 상태와 검토 이력

- 로컬과 원격 작업 브랜치는 기준 커밋에서 일치했고 작업 트리는 clean이었다.
- PR #15는 Draft이며 GitHub Actions `unit-tests`는 성공 상태였다.
- PR #15에 새 review, comment, unresolved thread는 없었다.
- 팀원 Draft PR #14는 현재 계정이 이미 검토한 동일 Head라 중복 리뷰를 제출하지 않았다.
- Raw·Verified KG, `ontology/ontology_spec.json`, `.env`, `AGENTS.md`, 원본 PDF와 Neo4j 저장 데이터는 수정하지 않았다.

## 3. BLOCKER 재현

외부 네트워크 대신 두 개의 임시 loopback HTTP 서버를 사용했다. 최초 OpenAI-compatible endpoint가 `302`와 두 번째 loopback URL을 반환하게 하자, 기존 `urllib.request.urlopen()`은 redirect 목적지로 GET 요청을 한 번 보냈고 최초 요청의 합성 Authorization 헤더도 전달했다.

재현 관측값:

- redirect 목적지 요청: 1회
- redirect method: GET
- 합성 Authorization 전달: 참
- 최종 애플리케이션 오류: 목적지 응답 파싱 단계의 `LLM_CHOICES_MISSING`

실제 외부 서버와 실제 API key는 사용하지 않았다.

## 4. 수정 내용

### 모든 redirect 거부

- 생산 client마다 기본 redirect handler를 대체하는 전용 거부 handler를 사용한다.
- `301`, `302`, `303`, `307`, `308`을 포함한 redirect에서 후속 요청을 만들기 전에 즉시 중단한다.
- redirect 목적지가 외부, 다른 loopback, 같은 서버 경로인지와 관계없이 따라가지 않는다.
- redirect는 일반 HTTP 오류·transport 재시도 경로에 넣지 않는다.
- handler 대신 `HTTPError`로 전달되는 경우도 방어적으로 같은 상태 코드를 즉시 차단한다.
- 상위 계층에는 `LLM_HTTP_REDIRECT_REJECTED`와 provider·3xx 범주·무재시도 사실만 전달한다.
- 오류에는 Location, 응답 본문, API key, Authorization, prompt와 JSON Schema를 포함하지 않는다.

수정 후 동일한 loopback 재현에서 초기 요청은 1회, redirect 목적지 요청은 0회였고 오류 코드는 `LLM_HTTP_REDIRECT_REJECTED`였다.

### 통합 trace 격리

실제 Ollama smoke가 `SafetyPipeline`에 `TemporaryDirectory`를 주입하도록 변경했다. 테스트 중 생성되는 trace는 종료 시 정리되며 질문 원문이 trace에 없는지 확인한다. 저장소 `logs/query-runs/` 파일 수는 smoke 전후 62개로 변하지 않았다. 기존 운영 trace 정책은 변경하지 않았다.

### vLLM context 책임 경계

- `KG_LLM_CONTEXT_LENGTH`는 Ollama 요청의 `options.num_ctx`에 사용된다.
- OpenAI Chat Completions에는 동일한 표준 요청 필드가 없다.
- vLLM context 상한은 서버의 `--max-model-len` 등으로 별도 설정한다.
- 클라이언트 기대값과 서버 상한이 자동 동기화된다고 간주하지 않으며 실제 vLLM 연결 시 각각 검증한다.

## 5. 회귀 테스트

추가한 검증:

- 두 provider의 `301`, `302`, `303`, `307`, `308` 차단
- OpenAI-compatible Bearer token 설정·미설정 양쪽 차단
- 최초 loopback 요청 1회, 목적지 요청·Authorization·body 전달 0회
- redirect 재시도 0회와 안전한 오류 문자열
- handler를 우회해 redirect `HTTPError`가 발생하는 경우의 방어 차단
- redirect provider 오류가 자연어 서비스의 `SAFE_FAILURE`와 같은 오류 코드로 연결됨
- 기존 정상 envelope, timeout·1회 transport 재시도, 1 MiB 제한, JSON 오류 계약 회귀 없음

## 6. 실제 검증 결과

| 검증 | 실제 결과 |
|---|---|
| 수정 전 loopback `302` 재현 | 목적지 1회, 합성 Authorization 전달 확인 |
| 수정 후 loopback `302` 재현 | 초기 1회, 목적지 0회, 무재시도, 안전 오류 코드 |
| provider·pipeline 집중 테스트 | 23 PASS |
| 전체 unittest | 총 78개: 74 PASS, 4 integration skip |
| 전체 pytest | 74 PASS, 4 integration skip, 75 subtests PASS |
| Neo4j 읽기 통합 | 77 PASS, local LLM smoke 1 skip, 81 subtests PASS |
| Ollama 14B 실제 smoke | 1 test, 6 subtests PASS |
| schema exporter | current/PASS |
| Python compile | PASS |
| `uv lock --check` | PASS |
| `git diff --check` | PASS |

Ollama smoke의 여섯 결과는 교양 34학점, 균형교양 4개 영역별 1과목 및 총 12학점, 편입생 면제, 자료구조 2학년 1학기, 컴퓨터공학과 전공필수 9과목·21학점, 자료구조 `MAJOR_ELECTIVE`였다. 모두 VERIFIED Evidence를 포함했다.

Neo4j 개수는 전체 읽기 통합과 Ollama smoke 전후 모두 노드 1,518개, 관계 3,260개, Evidence 511개로 동일했다.

## 7. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `src/kg_builder/llm/client.py` | 수정 | 전용 no-redirect opener와 안전 오류 계약 |
| `tests/test_llm_providers.py` | 수정 | 상태 코드·provider·token별 redirect 회귀 |
| `tests/test_local_llm_pipeline.py` | 수정 | `SAFE_FAILURE` 오류 코드 전달 회귀 |
| `tests/test_local_llm_integration.py` | 수정 | 임시 trace 격리·원문 비저장·정리 확인 |
| `docs/local-llm-query-pipeline.md` | 수정 | redirect 및 provider context 정책 |
| `docs/text-to-cypher-safety.md` | 수정 | LLM HTTP 방어층 설명 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 링크와 다음 번호 |

## 8. 남은 위험과 미검증 범위

- 실제 연구실 vLLM/OpenAI-compatible 서버 통합은 실행하지 않았다.
- redirect 테스트는 mock transport와 임시 loopback 서버로 수행했으며 실제 외부 목적지 요청은 하지 않았다.
- Neo4j Community 로컬 사용자는 권한 수준의 읽기 전용 경계가 아니다.
- Python 애플리케이션의 redirect 방어와 별개로 reverse proxy 및 실제 vLLM 서버의 redirect 정책도 배포 시 확인해야 한다.

## 9. 다음 작업

최신 PR #15 Head를 독립 재검토한 뒤 사용자가 Draft 해제·병합 여부를 결정한다. 병합 후 연구실 vLLM을 SSH 터널의 loopback endpoint로 연결해 provider 계약, context 상한과 동일 회귀 질문을 다시 검증한다.
