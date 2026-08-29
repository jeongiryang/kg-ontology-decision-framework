# 0030. DSW 모델 벤치마크와 Agent 역할 A/B

## 기본 정보

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-08-29 |
| 담당자 | 정이량 |
| 사용 에이전트 | Codex |
| 작업 브랜치 | `feat/jeongiryang/agentic-multiturn-graphrag` |
| 관련 Issue/PR | PR #34 |
| 작업 상태 | 완료 — 기본 모델·모드는 유지 |

## 1. 작업 목적

로컬 Ollama `qwen2.5-coder:14b`와 연구실 A6000 한 장에서 실행 가능한 모델을 같은
GraphRAG 평가로 비교하고, 제한된 도구 계획을 확대했을 때 의미 정확도·Citation·지연이
실제로 좋아지는지 확인한다.

## 2. 요청 내용 요약

- 본인 계정의 비대화형 SSH 설정을 사용해 서버·GPU·디스크 상태를 확인한다.
- 비어 있는 GPU 한 장만 명시적으로 사용해 후보를 하나씩 임시 실행한다.
- PR #10 50문항, 미공개 단일 50문항, 다중 턴 65턴을 같은 API/SSE로 평가한다.
- 보수적 Agent와 확대 Agent를 A/B하고, 선정 조건을 충족할 때만 기본값을 바꾼다.
- 서버 주소·계정·키·자격증명을 저장소에 기록하지 않고 모든 원격 프로세스를 종료한다.

## 3. 작업 전 상태

- Ollama와 OpenAI-compatible provider가 이미 `StructuredLLMClient`를 공유했다.
- 기본 Agent는 도구 4개, KG 조회 4회, 하위 질문 3개로 제한됐다.
- DSW 사전 조사 로그에는 SSH 설정 부재 때문에 원격 실측이 없었다.
- 로컬 14B 기준선은 PR #10 50/50, 미공개 단일 50/50, 다중 턴 65/65였다.

## 4. 수행한 작업

- 접속 직후 `whoami`, hostname, 전체 `nvidia-smi`, `w`, `uptime`, `df -h`, 사용자 홈
  사용량과 tmux 상태를 확인했다.
- 다른 연산이 없던 GPU 한 장만 `CUDA_VISIBLE_DEVICES`로 선택했다.
- 본인 가상환경에 vLLM 0.28.0을 사용하고 loopback에만 임시 서버를 열었다.
- Qwen2.5-Coder 32B AWQ, Qwen3 32B AWQ, Qwen3-Coder 30B-A3B FP8을 순차 실행했다.
- vLLM grammar의 `uniqueItems` 미지원 원인을 재현하고 provider 전용 schema projection을
  추가했다. typed planner와 tool 입력은 중복 정규화·검증을 유지했다.
- 세 평가셋을 보수적 모드에서 비교하고 Qwen3-Coder로 확대 모드까지 A/B했다.
- 확대 모드에는 최대 도구/조회/하위질문 `6/6/5`, 시간 150초, 동일 조회 반복 금지와
  검증 가능한 과목 목록 재서술만 허용했다.
- 기본 로컬 Ollama 경로로 실제 한 질문을 다시 실행했다.
- 원격 vLLM·tmux, 로컬 Starlette와 SSH forwarding을 모두 종료했다.

## 5. 변경된 파일

| 경로 | 변경 유형 | 내용 |
|---|---|---|
| `.env.example` | 수정 | 기본 `KG_AGENT_MODE=conservative` |
| `src/kg_builder/llm/client.py` | 수정 | vLLM grammar subset projection |
| `src/kg_builder/llm/planner.py` | 수정 | collection 중복 typed 정규화 |
| `src/kg_builder/agent/contracts.py` | 수정 | `AgentMode`, bounded `AgentPolicy` |
| `src/kg_builder/agent/orchestrator.py` | 수정 | 예산·중복 방지·opt-in 확대 목록 재서술 |
| `src/kg_builder/agent/__init__.py` | 수정 | 정책 공개 export |
| `src/evidence_chat/server.py` | 수정 | 환경 기반 정책 composition |
| `tests/test_llm_providers.py` | 수정 | provider grammar projection 회귀 |
| `tests/test_agentic_graphrag.py` | 수정 | 모드·예산·중복·목록 Grounding 회귀 |
| `docs/agentic-multiturn-graphrag.md` | 수정 | 두 모드와 유지되는 안전 경계 |
| `docs/local-llm-query-pipeline.md` | 수정 | 실제 DSW 비교 결과 |
| `docs/evaluations/model-provider-benchmark.md` | 수정 | 모델·Agent A/B 실측 보고 |
| `docs/ai-simulation-logs/jeong-iryang/0030-dsw-model-benchmark-and-agent-ab.md` | 추가 | 이번 작업 기록 |
| `docs/ai-simulation-logs/jeong-iryang/README.md` | 수정 | 로그 인덱스 |

## 6. 주요 결정과 이유

- 기본 모델은 로컬 14B를 유지했다. Qwen3-Coder가 빨랐지만 정확도가 낮았고, 다른 32B
  모델도 지연·정확도 선정 조건을 충족하지 못했다.
- 확대 Agent는 opt-in으로만 남겼다. Citation과 sealed Grounding은 유지했지만 세
  평가에서 모두 보수적 모드보다 상태 정확도가 낮았다.
- 원격 장애 시 자동 provider fallback을 만들지 않았다. 설정 오류를 숨기거나 서로 다른
  provider의 부분 결과를 섞지 않고 로컬 구성은 독립적으로 계속 동작한다.
- generation 원문은 저장하지 않았다. HTTP grammar 수락률과 공개 응답 오류는 측정했지만
  payload 의미 재시도율은 현재 개인정보 최소화 telemetry로 독립 계측할 수 없다고
  명시했다.

## 7. 검증

| 검증 항목 | 명령 또는 방법 | 결과 |
|---|---|---|
| Qwen2.5-Coder 32B | 실제 SSE 50+50+65 | 상태 45/50, 42/50, 57/65; Citation 25/25, 28/28, 39/39 |
| Qwen3-Coder 30B conservative | 실제 SSE 50+50+65 | 상태 46/50, 46/50, 60/65; Citation 23/23, 28/28, 39/39 |
| Qwen3-Coder 30B expanded | 실제 SSE 50+50+65 | 상태 45/50, 45/50, 57/65; Citation 23/23, 27/27, 37/37 |
| Qwen3 32B | 실제 S01 | ANSWERED·Citation 1, 41.925초; 전체 중단 |
| provider·Agent 관련 | 관련 pytest | PASS |
| 전체 unittest | `python -m unittest discover -s tests -v` | 355 PASS, 6 skip |
| 전체 pytest | `pytest -q` | 349 PASS, 6 skip, 370 subtests PASS |
| schema exporter | `python -m kg_builder.query.schema_exporter check` | PASS |
| Neo4j bundle·연결·검증 | validate/check-connection/verify | PASS, 1,536 nodes / 3,287 relationships / 520 Evidence |
| Neo4j opt-in 조회 | 관련 integration pytest | 3 PASS, 6 subtests PASS |
| 로컬 Ollama fallback | 실제 planner·answer·Starlette 통합 | 3 PASS, 18 subtests PASS |
| lock·diff | `uv lock --check`, `git diff --check` | PASS |
| 원격 정리 | 전체 `nvidia-smi`, own process와 tmux 확인 | GPU별 15MiB, vLLM/tmux 없음 |

GitHub Actions는 push 뒤 최신 Head에서 별도로 확인한다. 위 Neo4j 검사는 읽기 전용이며
현재 PR #32 기준 bundle 개수 `1,536 / 3,287 / 520`이 전후 동일했다.

## 8. 발견된 문제와 위험

- vLLM structured grammar는 표준 JSON Schema의 `uniqueItems`를 구현하지 않는다.
- Qwen3-Coder expanded는 추가 탐색이 정확도를 높이지 않고 일부 근거 부족·조언 상태를
  잘못 바꿨다.
- Qwen3 32B AWQ의 단일 질문 지연은 시연 기준과 공유 GPU 점유 원칙에 맞지 않았다.
- 연구실 상주 endpoint는 협의되지 않았으므로 벤치마크 결과가 좋아도 운영 기본으로 쓸
  수 없다.

## 9. 남은 작업

- PR #34의 GitHub Actions가 새 Head에서 통과하는지 확인한다.
- expanded는 향후 더 강한 모델과 별도 평가에서 정확도 향상이 입증될 때만 기본 승격한다.
- payload 의미 재시도율이 꼭 필요하면 원문을 저장하지 않는 집계 counter를 별도 설계한다.

## 10. 다음 작업 제안

현재 PR에서는 conservative 기본과 로컬 Ollama를 유지한다. DSW 상주 서비스는 연구실
협의 뒤 별도 운영 작업으로 다루고, 이번 모델 cache는 후속 재현을 위해 개인 홈에만 남긴다.
