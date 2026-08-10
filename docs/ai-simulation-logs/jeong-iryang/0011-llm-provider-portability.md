# 0011. LLM provider 이식성과 질의 의미 회귀 보완

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-11 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/local-llm-query-pipeline` |
| 기준 커밋 | `5bc4a6e4f27e38c763a70bafb01bfc6b65d949d6` |
| 관련 PR | Draft PR #15 |
| 작업 상태 | 완료 |

## 1. 작업 목적

PR #15 독립 검토에서 확인된 세 가지 비차단 문제를 고치고, planner와 Cypher generator가 Ollama 전용 구현에 결합되지 않도록 provider 경계를 완성한다. 실제 연구실 vLLM 서버를 실행하지 않은 상태에서도 OpenAI-compatible Chat Completions adapter의 계약과 오류 처리를 네트워크 없는 단위 테스트로 검증한다.

## 2. 작업 시작 상태와 PR 확인

- 로컬과 원격 작업 브랜치 Head가 기준 커밋에서 일치하고 작업 트리가 clean인 것을 확인했다.
- PR #15는 Draft이며 GitHub Actions는 성공 상태였다. 새 리뷰, inline comment, unresolved thread는 없었다.
- 팀원 PR #14는 이전에 검토한 Head와 동일해 중복 리뷰를 제출하지 않았다.
- Raw·Verified KG와 `ontology/ontology_spec.json`은 수정하지 않았다. `.env`, `AGENTS.md`, 모델 파일과 runtime query log는 Git 대상에서 제외했다.

## 3. 독립 검토 MINOR 보완

### 균형교양 의미

기존의 모호한 숫자 집합 검사를 다음 의미 계약으로 강화했다.

- 최소 총량: `value=12`, `unit=CREDIT`
- 영역별 조건: `value=1`, `unit=COURSE_PER_AREA`
- Rule 설명: 4개 영역과 영역별 1과목 조건
- 두 Rule 모두 `VERIFIED`
- 두 Rule 각각 직접 연결된 `VERIFIED Evidence`
- Evidence 원문과 발췌·원본·인쇄 페이지 확인

문서와 이전 로그의 표현도 “4개 영역에서 영역별 1과목 이상, 총 12학점 이상”으로 통일했다. 숫자 1을 학점으로 해석하지 않는다.

### 단일 과목 모호성

- `SelectionMode.SINGLE_COURSE`를 QueryPlan부터 결과 검증까지 유지한다.
- 학수번호와 이름이 함께 제공되면 안정 식별자인 학수번호를 우선한다.
- 결과에는 `Course.course_id` 기반 `course_identity`를 요구한다.
- 결과 0건은 `NOT_FOUND`, 한 identity는 정상, 서로 다른 identity가 둘 이상이면 `CLARIFICATION_REQUIRED`다.
- 같은 Course에 Evidence가 여러 건 붙은 행 증가는 모호한 과목으로 판단하지 않는다.
- 모델이 충분한 범위 필터를 만들고도 모호하다고 응답하면 강제 승격하지 않고 한 번만 재계획한다. 두 번째도 모호하면 중단한다.

초기 실제 smoke에서는 자료구조 개설 질문이 `CLARIFICATION_REQUIRED`로 중단됐다. 한 번의 안전한 재계획과 DB identity 판정 책임을 명시한 뒤 6개 질문이 모두 통과했다.

## 4. Provider 구조

공식 경계는 다음과 같다.

```text
StructuredLLMClient
├── OllamaClient
└── OpenAICompatibleClient
```

- `LLMSettings`가 provider, loopback endpoint, model, 선택적 API key, timeout, 재시도, context와 출력 token 상한을 검증한다.
- `LLMProvider`는 `ollama`, `openai-compatible`만 허용한다.
- `create_llm_client()` 한 곳에서만 provider를 분기한다.
- CLI는 factory를 사용하고 planner, generator, 자연어 서비스는 provider 조건문을 갖지 않는다.
- 두 adapter는 동일한 `LLMGeneration` 계약을 반환한다.
- 모든 endpoint는 `localhost` 또는 `127.0.0.1` HTTP만 허용한다. 연구실 서버는 SSH 터널을 전제로 한다.

OpenAI-compatible adapter는 `/v1/chat/completions`, 선택적 Bearer token, JSON Schema response format, 응답 크기 상한, timeout, 최대 한 번의 transport 재시도를 지원한다. HTTP 오류에는 본문, API key, prompt 또는 모델 원문을 포함하지 않는다. base URL의 `/v1` 중복도 방지한다.

## 5. 테스트와 실제 결과

| 검증 | 실제 결과 |
|---|---|
| provider·로컬 pipeline 집중 테스트 | 42 PASS, 1 integration skip, 37 subtests PASS |
| 최종 unittest | 75 PASS, 4 integration skip |
| 최종 pytest | 71 PASS, 4 integration skip, 60 subtests PASS |
| Neo4j 읽기 통합 | 74 PASS, 로컬 LLM smoke 1 skip, 66 subtests PASS |
| Ollama 14B 실제 smoke 최초 | 5/6 PASS, 자료구조 개설 질문 모호성으로 실패 |
| Ollama 14B 실제 smoke 보완 후 | 1 test 및 6 subtests PASS |
| schema exporter | current/PASS |
| Python compile | PASS |
| `git diff --check` | PASS |

보완 후 6개 질문은 모두 `ANSWERABLE`이며 VERIFIED Evidence를 포함했다. 균형교양은 두 의미 Rule과 Evidence를 모두 검증했다. smoke 전후 Neo4j 개수는 노드 1,518, 관계 3,260, Evidence 511로 동일했다.

## 6. 실패한 접근과 원인

- 모델의 `CLARIFICATION_REQUIRED`를 단순히 필터 존재 여부로 `READY`로 바꾸는 기존 방식은 실제 동명 과목을 숨길 수 있어 제거했다.
- 이를 제거한 직후 모델이 정상 자료구조 질문도 모호하다고 판단해 smoke가 5/6에 그쳤다.
- 모델 응답을 강제 변경하는 대신 한 번만 재계획하고, 실제 결과의 안정 Course identity를 최종 판단 근거로 삼아 해결했다.

## 7. 검증하지 않은 범위와 남은 문제

- 실제 연구실 vLLM/OpenAI-compatible 서버에 접속하지 않았다. adapter는 단위 테스트까지만 완료했다.
- OpenAI-compatible 서버별 JSON Schema 지원 차이는 실제 배포 시 재검증해야 한다.
- Neo4j Community 로컬 사용자는 권한 수준의 읽기 전용 계정이 아니며 기존 안전 파이프라인을 계속 최종 애플리케이션 방어로 사용한다.
- Ollama 14B는 12GB VRAM 여유가 작아 긴 context와 동시 요청 위험이 남아 있다.

## 8. 다음 권장 작업

연구실 vLLM을 SSH 터널의 loopback endpoint로 연결해 동일 6개 회귀 질문과 JSON/Cypher 계약, 응답시간을 재검증한다. 그 후 Verified rows와 Evidence를 근거 포함 한국어 답변으로 렌더링하고 프론트엔드 응답 계약에 연결한다.
