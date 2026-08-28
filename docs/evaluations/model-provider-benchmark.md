# LLM provider·모델 및 Agent 모드 비교

## 판정

2026-08-29 실측 결과 개발 기본값은 Ollama `qwen2.5-coder:14b`와
`KG_AGENT_MODE=conservative`를 유지한다. 연구실 A6000 한 장에서 실행한 세 후보 중
Qwen3-Coder 30B MoE가 더 빨랐지만 단일·다중 턴 상태 정확도가 기준선보다 낮았다.
Qwen2.5-Coder 32B AWQ는 더 느렸고, Qwen3 32B AWQ는 한 질문이 41.925초여서 공유 GPU를
장시간 점유하지 않도록 전체 평가를 중단했다.

DSW 상주 서비스는 실행하지 않았고 기본 provider를 원격 서버로 변경하지 않았다.

## 공통 안전 경계와 provider 호환

```text
StructuredLLMClient
├── OllamaClient             → loopback /api/chat
└── OpenAICompatibleClient   → SSH forwarding의 loopback /v1/chat/completions
```

- provider와 모델은 `KG_LLM_PROVIDER`, `KG_LLM_BASE_URL`, `KG_LLM_MODEL`로 선택한다.
- 애플리케이션은 loopback HTTP endpoint만 허용한다.
- planner, Cypher generator, Agent orchestrator는 provider별 분기를 만들지 않는다.
- canonical Cypher, SafetyPipeline, EXPLAIN, Result/Claim/Citation 검증은 모델과 무관하게
  동일하다.
- vLLM structured-output grammar가 지원하지 않는 JSON Schema `uniqueItems`는 provider용
  grammar projection에서만 생략한다. planner와 tool contract가 typed collection을
  구성할 때 중복을 제거하거나 거부하므로 애플리케이션 계약은 유지된다.

## 서버와 실행 조건

- Ubuntu 22.04, NVIDIA driver 580.173.02, CUDA driver 13.0
- RTX A6000 48GB 네 장 중 다른 연산이 없던 GPU 한 장만 사용
- 모든 실행에 `CUDA_VISIBLE_DEVICES` 지정
- 사용자 홈의 Python 3.10 가상환경, vLLM 0.28.0, 개인 Hugging Face cache만 사용
- vLLM은 `127.0.0.1`에 bind하고 SSH local forwarding으로만 접근
- context 16,384, temperature 0, 최대 동시 sequence 1
- 시스템 CUDA toolkit 11.8과 FlashInfer sampler의 CUDA 12 요구가 맞지 않아
  `VLLM_USE_FLASHINFER_SAMPLER=0`으로 vLLM 표준 sampler를 사용

실행 전 전체 `nvidia-smi`, `w`, `uptime`, `df -h`와 사용자 홈 사용량을 확인했다. 실행
중 선택 GPU의 다른 사용자 프로세스는 없었고, 다른 세 GPU에는 Xorg의 4MiB 외 연산
프로세스가 없었다. 원격 주소·계정·키·자격증명은 저장소에 기록하지 않았다.

## 후보와 자원

| 후보 | runtime·quantization | 실행 범위 | 최대 VRAM | 판정 |
|---|---|---:|---:|---|
| 로컬 `qwen2.5-coder:14b` | Ollama, Q4 계열 로컬 배포 | 50 + 50 + 65턴 | 11,506MiB(기존 실측) | 기본 유지 |
| [`Qwen2.5-Coder-32B-Instruct-AWQ`](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct-AWQ) | vLLM, AWQ 4-bit | 50 + 50 + 65턴 | 43,133MiB | 정확도·지연 열세 |
| [`Qwen3-32B-AWQ`](https://huggingface.co/Qwen/Qwen3-32B-AWQ) | vLLM, AWQ 4-bit | 구조화 smoke + 집중 질문 | 42,581MiB | S01 41.925초로 전체 중단 |
| [`Qwen3-Coder-30B-A3B-Instruct-FP8`](https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8) | vLLM, FP8 MoE(8 active experts) | 50 + 50 + 65턴 | 43,291MiB | 빠르나 정확도 열세 |

Qwen3-Coder의 최초 다운로드는 278.791초, checkpoint load는 약 301.7초였다. 공식 카드가
BF16/FP16 실행에 약 55GB를 안내하는 Mistral Small 3.1 24B는 A6000 한 장 원칙에 맞지 않아
다운로드하지 않았다.

## 보수적 모드 모델 비교

정확도는 저장소의 승인된 평가 보고서 상태와 비교했다. `ANSWERED Citation`은 해당 상태의
모든 응답에 Citation이 하나 이상 있었는지를 뜻한다. 응답의 Fact–Evidence 의미 연결은
동일한 ResultValidator·ClaimValidator·CitationRenderer를 통과한 sealed 응답으로 다시
확인했다.

| 모델·평가 | 상태 정확도 | 평균 | 중앙값 | P95 | `ANSWERED` Citation | 공개 오류 |
|---|---:|---:|---:|---:|---:|---:|
| 로컬 14B / PR #10 50 | 50/50 | 6.865s | 8.284s | 10.651s | 22/22 | 0 |
| 로컬 14B / 미공개 단일 50 | 50/50 | 9.338s | 9.707s | 15.941s | 29/29 | 0 |
| 로컬 14B / 다중 턴 65 | 65/65 | 9.566s | 9.637s | 22.998s | 43/43 | 0 |
| Qwen2.5-Coder 32B AWQ / PR #10 | 45/50 | 21.442s | 18.916s | 47.162s | 25/25 | 0 |
| Qwen2.5-Coder 32B AWQ / 미공개 단일 | 42/50 | 17.599s | 16.018s | 44.639s | 28/28 | 0 |
| Qwen2.5-Coder 32B AWQ / 다중 턴 | 57/65 | 13.767s | 14.635s | 36.398s | 39/39 | 0 |
| Qwen3-Coder 30B FP8 / PR #10 | 46/50 | 7.988s | 5.793s | 16.412s | 23/23 | 0 |
| Qwen3-Coder 30B FP8 / 미공개 단일 | 46/50 | 5.321s | 4.503s | 11.973s | 28/28 | 0 |
| Qwen3-Coder 30B FP8 / 다중 턴 | 60/65 | 4.241s | 4.294s | 9.050s | 39/39 | 0 |

Qwen2.5-Coder 32B의 최초 요청은 vLLM이 `uniqueItems` grammar를 구현하지 않아 HTTP 400을
반환했다. provider grammar projection 수정 뒤 PR #10 192회, 단일 178회, 다중 187회의
구조화 HTTP 호출은 모두 200이었다. Qwen3-Coder 보수적 모드도 596/596 구조화 HTTP 호출이
200이었다. 이는 provider grammar 수락률이며, 원문 모델 응답을 저장하지 않는 현재 개인
정보 정책상 generation payload의 의미 재시도율을 별도 계측한 값은 아니다.

## Agent 역할 A/B

`expanded`는 별도 질의 파이프라인이 아니다. 기존 도구와 SafetyPipeline을 그대로 두고
도구/조회/하위질문 예산을 `4/4/3`에서 `6/6/5`로 늘리며, Python이 모든 과목명·학수번호·
학점·집계를 재검증할 수 있는 과목 목록만 LLM 표시 문장 대상으로 넓힌다. 동일 조회 반복은
금지하고 시간 예산은 150초다.

| Qwen3-Coder 30B 모드·평가 | 상태 정확도 | 평균 | P95 | `ANSWERED` Citation | 공개 오류 |
|---|---:|---:|---:|---:|---:|
| conservative / PR #10 | 46/50 | 7.988s | 16.412s | 23/23 | 0 |
| expanded / PR #10 | 45/50 | 7.912s | 20.551s | 23/23 | 0 |
| conservative / 미공개 단일 | 46/50 | 5.321s | 11.973s | 28/28 | 0 |
| expanded / 미공개 단일 | 45/50 | 6.740s | 16.314s | 27/27 | 0 |
| conservative / 다중 턴 | 60/65 | 4.241s | 9.050s | 39/39 | 0 |
| expanded / 다중 턴 | 57/65 | 4.449s | 11.031s | 37/37 | 0 |

확대 모드의 584/584 구조화 HTTP 호출은 200이었고 115/115 턴에 `agent_trace`가 있었다.
그러나 세 평가 모두 정확도가 낮아졌고 자연스러운 목록 표현의 제한적 개선이 이를 상쇄하지
못했다. 따라서 기본 모드는 conservative이며 expanded는 명시적인 실험 옵션으로만 남긴다.

## 선택 결론

- 개발·시연 기본: 로컬 Ollama `qwen2.5-coder:14b`, conservative
- 고성능 원격 실험: provider-neutral OpenAI-compatible 설정은 유지하되 기본 자동 전환 없음
- DSW 상주 사용: 연구실 협의 전까지 미구현
- DSW 연결 실패: 애플리케이션이 자동 원격 fallback하지 않으며 로컬 Ollama 구성은 독립

모델이 바뀌어도 학교 규정값은 FactPacket 밖에서 생성할 수 없고, 계산은 Python이 수행하며,
Citation 없는 사실 답변은 승인되지 않는다.

## 종료와 잔여물

벤치마크 종료 후 본인이 시작한 vLLM 서버, tmux 세션, Starlette 서버와 SSH forwarding을
모두 종료했다. 종료 후 네 GPU는 각각 15MiB만 사용했고 vLLM 연산 프로세스는 없었다.
재평가를 위해 개인 홈에 모델 cache 약 66GB와 일반 cache 약 3.9GB를 유지했다. 상주 서비스,
외부 공개 포트, 타인 파일·프로세스 변경은 없다.

수행하지 않은 검증은 다중 동시 요청 부하 시험과 Qwen3-32B 전체 165턴 평가다. 전자는
공유 GPU 정책상 단일 sequence로 제한했고, 후자는 첫 질문 지연으로 조기 중단했다.
