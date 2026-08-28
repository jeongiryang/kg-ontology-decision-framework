# LLM provider·모델 비교 기준선과 DSW 사전 조사

## 1. 판정 요약

2026-08-29 기준 기본 모델은 Ollama `qwen2.5-coder:14b`를 유지한다. 프로젝트에는 이미
`ollama`와 `openai-compatible` provider가 같은 `StructuredLLMClient` 계약으로 구현돼
있고, 연구실 endpoint는 SSH 터널의 loopback 주소로 연결할 수 있다. 따라서 provider
코드나 환경변수 계약을 추가로 변경하지 않았다.

DSW 연구실 서버는 표준 사용자 SSH 설정에서 사용할 수 있는 host alias가 확인되지 않아
접속하지 않았다. 주소나 계정을 추측하지 않았고, 원격 `nvidia-smi`, `w`, `uptime`,
`df -h`, 모델 다운로드와 후보 모델 실행도 수행하지 않았다. 이 문서의 DSW 후보는 공식
모델 카드에 근거한 **벤치마크 대상 목록**이며 실행 결과나 선정 결과가 아니다.

## 2. 현재 provider 경계

```text
StructuredLLMClient
├── OllamaClient             → loopback /api/chat
└── OpenAICompatibleClient   → loopback /v1/chat/completions
```

- `KG_LLM_PROVIDER`, `KG_LLM_BASE_URL`, `KG_LLM_MODEL`로 provider와 모델을 선택한다.
- base URL은 `localhost` 또는 `127.0.0.1`의 HTTP endpoint만 허용한다.
- 원격 연구실 모델은 외부 주소를 애플리케이션에 넣지 않고 SSH local forwarding으로
  loopback에 연결한다.
- planner, Cypher generator와 agent orchestrator는 provider 이름을 분기하지 않는다.
- 모델 교체와 무관하게 canonical Cypher, SafetyPipeline, Result/Claim/Citation 검증은
  그대로 적용한다.
- DSW가 없어도 로컬 Ollama로 애플리케이션을 실행할 수 있다. 원격 provider로 자동
  fallback하거나 연구실 연결 실패 때문에 로컬 구성을 무효화하지 않는다.

## 3. 로컬 14B 기준선

측정 환경은 현재 로컬 Ollama의 `qwen2.5-coder:14b`, context 8,192,
temperature 0이다. 측정 시 Ollama loopback endpoint와 해당 모델 설치를 확인했다. 아래
통계는 PR #32/#34 구현 검증 중 실제 `/api/ask` SSE로 얻은 마지막 성공 산출물을 합쳐 다시
계산했다.

| 평가 | 건수 | 상태 분포 | 총 시간 | 평균 | 중앙값 | P95 | `ANSWERED` Citation | 공개 오류 |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| PR #10 원본 | 50 | A 22 / N 5 / I 16 / O 1 / D 6 | 343.237s | 6.865s | 8.284s | 10.651s | 22/22 | 0 |
| 미공개 단일 턴 | 50 | A 29 / N 3 / I 10 / O 5 / D 3 | 466.881s | 9.338s | 9.707s | 15.941s | 29/29 | 0 |
| 미공개 다중 턴 | 65턴 | A 43 / N 1 / I 8 / O 1 / D 12 | 621.800s | 9.566s | 9.637s | 22.998s | 43/43 | 0 |

상태 약어는 `ANSWERED`, `NEEDS_USER_INFO`, `INSUFFICIENT_EVIDENCE`,
`OUT_OF_SCOPE`, `ADVISORY` 순이다. 미공개 평가 115턴 모두 `agent_trace`를 반환했고,
실제 KG 조회가 필요한 79턴에서 `GRAPH_EXECUTION`이 발생했다. 나머지는 사용자 정보 부족,
근거 부족, 범위 밖 또는 조언 상태처럼 조회가 필수가 아닌 흐름을 포함한다.

이전 로컬 6문항 모델 비교에서 기록한 14B 최대 VRAM은 11,506MiB다. 이번 추가 조사에서
GPU 메모리는 다시 측정하지 않았다. 기존 SSE 산출물은 모델 generation별 최초 JSON 성공,
재시도와 schema 오류를 별도 계수하지 않으므로 **JSON schema 준수율과 개별 tool-call
성공률은 소급 계산하지 않았다**. 115/115 trace는 사용자 요청의 처리 완료 관측값이지
generation 단위 schema 준수율을 뜻하지 않는다.

## 4. DSW 1-GPU 후보

첫 비교는 A6000 한 장과 명시적인 `CUDA_VISIBLE_DEVICES` 안에서 실행해야 한다.

| 우선순위 | 후보 | 공식 카드에서 확인한 특성 | 확인 목적 | 상태 |
|---:|---|---|---|---|
| 1 | [`Qwen/Qwen2.5-Coder-32B-Instruct-AWQ`](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct-AWQ) | Apache-2.0, 32B, AWQ 4-bit, config 기준 32K | 현재 Coder 14B와 가장 가까운 저위험 용량 비교 | 미실행 |
| 2 | [`Qwen/Qwen3-32B-AWQ`](https://huggingface.co/Qwen/Qwen3-32B-AWQ) | Apache-2.0, 32B, AWQ 4-bit, native 32K, tool calling 안내 | 다중 턴·도구 선택·한국어 설명 품질 비교 | 미실행 |
| 보류 | [`Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8`](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8) | Apache-2.0, 30.5B MoE, FP8, agentic coding·tool calling 안내 | vLLM·A6000 FP8 호환성 확인 뒤 평가 | 미실행 |

[`Mistral-Small-3.1-24B-Instruct-2503`](https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503)은
공식 카드가 BF16/FP16 GPU 메모리를 약 55GB로 안내하므로 48GB A6000 한 장을 우선하는
이번 사전 목록에서는 제외했다. 임의 제3자 양자화 모델을 대신 채택하지 않는다.

후보 이름은 코드 기본값에 넣지 않는다. 실제 비교에서는 각 모델을 같은 prompt, JSON
schema, temperature 0, context/output 상한과 평가셋으로 실행하고, 서버별로 불가피한 설정
차이는 결과에 기록한다.

## 5. 재현 가능한 DSW 절차

SSH 접속 정보가 정상적으로 구성된 뒤 다음 순서로 진행한다. placeholder는 실제 문서나
Git 기록에 서버 주소·계정·키를 남기지 않기 위한 것이다.

1. 설정된 본인 SSH alias로 접속한다.
2. 모델 다운로드나 프로세스 시작 전에 `nvidia-smi`, `w`, `uptime`, `df -h`를 실행한다.
3. 다른 사용자가 점유하지 않은 GPU 한 장을 선택하고
   `CUDA_VISIBLE_DEVICES=<FREE_GPU>`를 명시한다.
4. 본인 홈의 가상환경·캐시만 사용해 loopback에 임시 OpenAI-compatible server를 띄운다.
5. 로컬 PC에서 SSH local forwarding을 열고 `KG_LLM_PROVIDER=openai-compatible`과
   loopback `KG_LLM_BASE_URL`로 서버를 시작한다.
6. `scripts/evaluate_question_set.py`로 PR #10 50문항을, 같은 commit의
   `scripts/evaluate_agentic_chat.py`로 미공개 단일 50문항과 다중 20시나리오를 실행한다.
7. 모델별 올바른 상태, Citation coverage, 주장-Evidence 일치, JSON schema/tool 호출,
   평균·중앙값·P95, timeout·safe failure와 최대 VRAM을 기록한다.
8. 본인이 시작한 서버·tmux·추론 프로세스를 종료하고 `nvidia-smi`로 VRAM 반환을 확인한다.

원본 PDF, `.env`, Neo4j 자격증명과 사용자 프로필은 DSW에 복사하지 않는다. 모델에는 평가에
필요한 최소 prompt·schema·조회 packet만 전달하며 raw 질문·프로필 로그는 남기지 않는다.
상주 vLLM/Ollama 서비스와 외부 공개 포트는 연구실 협의 없이는 만들지 않는다.

## 6. 모델 변경 조건

다음 항목을 같은 데이터로 측정하기 전에는 기본 모델을 변경하지 않는다.

- 단일·다중 턴 의미 정확도와 올바른 다섯 outcome 비율
- generation 단위 JSON schema 준수율과 tool 호출 성공률
- `ANSWERED` Citation 보유율과 주장-Evidence 일치율
- Cypher 생성 후 SafetyPipeline·EXPLAIN 통과율
- clarification과 사용자 정보 정정 품질
- 평균·중앙값·P95 시간, timeout, 최대 VRAM과 단일 GPU 안정성
- 한 요청씩 실행한 뒤 제한된 동시 요청에서의 안정성

향상이 불명확하거나 상주 서비스 협의가 없으면 로컬 `qwen2.5-coder:14b`를 유지한다.
DSW 후보가 더 좋아도 기존 Ollama 설정은 개발·fallback 경로로 계속 지원한다.

## 7. 이번에 수행하지 않은 항목

- DSW SSH 접속과 본인 계정 사용: 접속 alias 부재로 미실행
- DSW `nvidia-smi`, `w`, `uptime`, `df -h`: 미실행
- GPU 선택·모델 다운로드·vLLM 설치·후보 실행: 미실행
- DSW 정확도·tool calling·Citation·지연·VRAM 비교: 결과 없음
- 동시 요청 안정성: 미실행
- 서버 파일 생성과 프로세스 시작·종료·VRAM 반환 확인: 해당 없음
- 상주 서비스: 실행하지 않음
